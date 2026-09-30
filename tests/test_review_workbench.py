from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import database, main
from app.core.contracts import RequestContext
from app.core.errors import ResourceNotFoundError
from app.profiles.execution_integrity import (
    EXECUTION_INTEGRITY_PROFILE,
    ExecutionIntegrityReviewer,
)
from app.repository import SQLiteRepository
from app.services.reviews import ReviewProfileRegistry, ReviewService


def _repository(tmp_path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "reviews.db")
    repository.init()
    return repository


def _reviewable_run(repository: SQLiteRepository, workspace_id: str) -> dict:
    session = repository.create_session(workspace_id, "Review subject")
    run = repository.create_run(
        workspace_id,
        session["id"],
        "review-request",
        None,
        "review-model",
    )
    repository.append_run_step(
        workspace_id,
        run["id"],
        1,
        "tool",
        "unbound_tool",
        "failed",
        '{"query":"redacted"}',
        "",
        {},
    )
    repository.append_run_step(
        workspace_id,
        run["id"],
        2,
        "tool",
        "local_lookup",
        "succeeded",
        '{"query":"safe"}',
        '{"answer":42}',
        {
            "source": "local",
            "provider_id": "local",
            "risk": "low",
            "execution_mode": "thread",
            "cancellation_mode": "soft",
        },
    )
    repository.finish_run(workspace_id, run["id"], "succeeded")
    return repository.get_run(workspace_id, run["id"])


def test_profile_registry_is_versioned_and_rejects_duplicate_ids() -> None:
    registry = ReviewProfileRegistry([ExecutionIntegrityReviewer()])

    profile = registry.list_profiles()[0]
    assert profile["profile_id"] == "execution.integrity.v1"
    assert profile["version"] == 1
    assert len(profile["profile_hash"]) == 64
    assert {rule["rule_id"] for rule in profile["rules"]} == {
        "execution.terminal_completion",
        "execution.step_sequence",
        "execution.status_alignment",
        "execution.error_provenance",
        "execution.tool_provenance",
        "execution.output_materialization",
    }
    with pytest.raises(ValueError, match="already registered"):
        registry.register(ExecutionIntegrityReviewer())


def test_execution_integrity_review_persists_bounded_findings(tmp_path) -> None:
    repository = _repository(tmp_path)
    workspace_id = "workspace-a"
    run = _reviewable_run(repository, workspace_id)
    context = RequestContext(
        request_id="review-1",
        workspace_id=workspace_id,
        principal_id="reviewer-a",
    )
    service = ReviewService(lambda: repository)

    result = service.review_execution_run(
        context, run["id"], EXECUTION_INTEGRITY_PROFILE.profile_id
    )

    assert result["status"] == "completed"
    assert result["profile_hash"] == EXECUTION_INTEGRITY_PROFILE.content_hash
    assert result["finding_count"] == 4
    assert result["severity_counts"] == {"P1": 2, "P2": 1, "P3": 1}
    assert {item["rule_id"] for item in result["findings"]} == {
        "execution.status_alignment",
        "execution.error_provenance",
        "execution.tool_provenance",
        "execution.output_materialization",
    }
    assert all(item["status"] == "open" for item in result["findings"])
    assert all(len(item["finding_hash"]) == 64 for item in result["findings"])
    assert all(item["origin"] == "deterministic_rule" for item in result["findings"])

    frozen_step = result["input_snapshot"]["steps"][1]
    assert "input_content" not in frozen_step
    assert "output_content" not in frozen_step
    assert frozen_step["has_output"] is True
    assert len(frozen_step["output_digest"]) == 64
    assert repository.get_run(workspace_id, run["id"])["status"] == "succeeded"

    stored = service.get_review_run(context, result["id"])
    assert stored == result
    assert service.list_findings(context, subject_id=run["id"], severity="P1")


def test_review_ledger_is_tenant_scoped(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = _reviewable_run(repository, "workspace-a")
    alpha = RequestContext(request_id="a", workspace_id="workspace-a")
    beta = RequestContext(request_id="b", workspace_id="workspace-b")
    service = ReviewService(lambda: repository)
    review = service.review_execution_run(
        alpha, run["id"], EXECUTION_INTEGRITY_PROFILE.profile_id
    )

    assert service.list_review_runs(alpha, subject_id=run["id"])
    assert service.list_review_runs(beta, subject_id=run["id"]) == []
    assert service.list_findings(beta, review_run_id=review["id"]) == []
    with pytest.raises(ResourceNotFoundError) as error:
        service.get_review_run(beta, review["id"])
    assert getattr(error.value, "code", None) == "resource_not_found"


def test_review_run_and_findings_are_inserted_atomically(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = _reviewable_run(repository, "workspace-a")
    context = RequestContext(request_id="a", workspace_id="workspace-a")
    service = ReviewService(lambda: repository)

    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_review_finding BEFORE INSERT ON review_findings "
            "BEGIN SELECT RAISE(ABORT, 'reject finding'); END"
        )

    with pytest.raises(sqlite3.IntegrityError):
        service.review_execution_run(
            context, run["id"], EXECUTION_INTEGRITY_PROFILE.profile_id
        )
    assert repository.list_review_runs("workspace-a") == []


def test_review_http_surface_requires_admin_to_create_and_allows_readback(
    tmp_path, monkeypatch
) -> None:
    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "review-api.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    with TestClient(main.app) as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "email": "reviewer@example.test",
                "password": "review-pass-123",
                "name": "Reviewer",
                "workspace_name": "Review workspace",
            },
        )
        assert registered.status_code == 200
        token = registered.json()["access_token"]
        workspace_id = registered.json()["tenant_id"]
        headers = {"Authorization": f"Bearer {token}"}
        run = _reviewable_run(database.get_repository(), workspace_id)

        profiles = client.get("/api/reviews/profiles", headers=headers)
        assert profiles.status_code == 200
        assert profiles.json()[0]["profile_id"] == "execution.integrity.v1"

        created = client.post(
            f"/api/reviews/runs/{run['id']}",
            headers=headers,
            json={"profile_id": "execution.integrity.v1"},
        )
        assert created.status_code == 201
        review = created.json()
        assert review["finding_count"] == 4
        assert client.get(
            f"/api/reviews/{review['id']}", headers=headers
        ).json() == review
        findings = client.get(
            "/api/review-findings",
            headers=headers,
            params={"subject_id": run["id"], "severity": "P1"},
        )
        assert findings.status_code == 200
        assert len(findings.json()) == 2
