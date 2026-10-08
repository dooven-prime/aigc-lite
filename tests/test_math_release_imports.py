"""Pinned catalogue import must never silently promote mathematical claims."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.research_import import OpenAIMathReleaseAdapter
from app.api import math_release_imports as import_api
from app.auth import current_admin_user
from app.core.contracts import RequestContext
from app.core.errors import InvalidEvidenceError, ResourceNotFoundError
from app.profiles.math_theorem import PROFILE_ID
from app.repository import SQLiteRepository
from app.services.math_release_imports import MathReleaseImportService
from app.services.qualification import QualificationService
from app.tenancy import Tenant, current_tenant

COMMIT = "a" * 40


def _catalogue(families: int = 2, manuscripts: int = 3) -> bytes:
    result = [
        "# Mathematics manuscript collection",
        f"**{manuscripts} manuscripts covering {families} result families.**",
        "## Manuscript map",
    ]
    left = manuscripts
    for index in range(1, families + 1):
        count = left // (families - index + 1)
        left -= count
        result.extend(
            [
                "<table><tbody><tr><td>",
                f"**{index:03d}. Candidate theorem {index}.** "
                f"This family claims proposition {index}. "
                + (f"([Lean](lean/docs/{index:03d}.md))" if index == 1 else ""),
                "</td></tr></tbody></table>",
            ]
        )
        for manuscript_index in range(count):
            result.extend(
                [
                    "<table><tbody><tr><td>",
                    f"&emsp;[Paper {index}-{manuscript_index}]"
                    f"(preprints/Family-{index}/paper-{manuscript_index}.pdf)",
                    "A proposed argument; no verification is implied.",
                    "</td></tr></tbody></table>",
                ]
            )
    return "\n".join(result).encode()


class _FixtureAdapter(OpenAIMathReleaseAdapter):
    def __init__(self, contents: bytes) -> None:
        self.contents = contents

    def fetch(self, source_commit: str) -> tuple[bytes, bytes]:
        self._validate_commit(source_commit)
        return self.contents, b'version: "v0.4"\nsources: []\n'


def _service(tmp_path, contents: bytes):
    repository = SQLiteRepository(tmp_path / "math-release.db")
    repository.init()
    adapter = _FixtureAdapter(contents)
    service = MathReleaseImportService(lambda: repository, adapter=adapter)
    context = RequestContext("import-request", "workspace-a", "admin-a")
    return repository, service, adapter, context


def test_candidate_import_is_atomic_idempotent_and_not_qualified(tmp_path) -> None:
    repository, service, adapter, context = _service(tmp_path, _catalogue())
    preview = service.preview(context, COMMIT)
    assert (preview["family_count"], preview["manuscript_count"]) == (2, 3)
    assert preview["lean_linked_family_count"] == 1
    assert preview["claim_revision_count"] == 0

    committed = service.commit(context, COMMIT, preview["preview_hash"])
    assert committed["deduplicated"] is False
    assert committed["admission_state"] == "candidate"
    assert service.commit(context, COMMIT, preview["preview_hash"])["deduplicated"] is True
    stored = service.get(context, committed["id"], include_families=True)
    assert len(stored["manifest"]["families"]) == 2
    assert stored["manifest"]["families"][0]["claim_semantics"] == (
        "catalog_summary_not_frozen_theorem"
    )
    assert stored["manifest"]["families"][0]["manuscripts"][0][
        "artifact_integrity"
    ] == "not_downloaded"
    assert len(service.list(context)) == 1
    with pytest.raises(ResourceNotFoundError):
        service.get(RequestContext("other", "workspace-b", "admin-b"), committed["id"])
    assert QualificationService(lambda: repository).qualified_search(
        context, "Candidate theorem", PROFILE_ID
    ) == []
    with repository._connect() as db:
        for table in (
            "research_claim_revisions",
            "qualification_receipts",
            "knowledge_admission_receipts",
            "current_use_bindings",
            "authorization_grants",
        ):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 2

    adapter.contents = _catalogue(2, 4)
    changed = service.preview(context, COMMIT)
    with pytest.raises(InvalidEvidenceError, match="different catalogue"):
        service.commit(context, COMMIT, changed["preview_hash"])
    with repository._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(
                "UPDATE math_release_imports SET family_count = 9 WHERE id = ?",
                (committed["id"],),
            )


def test_preview_hash_blocks_changed_bytes_and_malformed_catalogue(tmp_path) -> None:
    repository, service, adapter, context = _service(tmp_path, _catalogue())
    preview = service.preview(context, COMMIT)
    adapter.contents = _catalogue(2, 4)
    with pytest.raises(InvalidEvidenceError, match="no longer matches preview"):
        service.commit(context, COMMIT, preview["preview_hash"])
    assert service.list(context) == []

    with pytest.raises(InvalidEvidenceError, match="exact 40-character"):
        adapter.parse("main", _catalogue(), b"version: v0.4")
    repeated_path = _catalogue().replace(b"paper-1.pdf", b"paper-0.pdf")
    with pytest.raises(InvalidEvidenceError, match="Duplicate manuscript"):
        adapter.parse(COMMIT, repeated_path, b"version: v0.4")
    truncated = _catalogue().replace(b"3 manuscripts", b"4 manuscripts", 1)
    with pytest.raises(InvalidEvidenceError, match="declared counts"):
        adapter.parse(COMMIT, truncated, b"version: v0.4")
    with repository._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM math_release_imports").fetchone()[0] == 0


def test_372_family_722_manuscript_scale_stays_candidate_only(tmp_path) -> None:
    repository, service, _adapter, context = _service(tmp_path, _catalogue(372, 722))
    preview = service.preview(context, COMMIT)
    assert (preview["family_count"], preview["manuscript_count"]) == (372, 722)
    committed = service.commit(context, COMMIT, preview["preview_hash"])
    assert committed["admission_state"] == "candidate"
    assert service.get(context, committed["id"])["family_count"] == 372
    assert QualificationService(lambda: repository).qualified_search(
        context, "Candidate theorem", PROFILE_ID
    ) == []


def test_failed_batch_insert_rolls_back_both_raw_artifacts(tmp_path) -> None:
    repository, service, _adapter, context = _service(tmp_path, _catalogue())
    preview = service.preview(context, COMMIT)
    with repository._connect() as db:
        db.execute(
            "CREATE TRIGGER reject_math_release_insert BEFORE INSERT ON math_release_imports "
            "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        service.commit(context, COMMIT, preview["preview_hash"])
    with repository._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM math_release_imports").fetchone()[0] == 0


def test_source_artifact_tampering_fails_closed_on_read(tmp_path) -> None:
    repository, service, _adapter, context = _service(tmp_path, _catalogue())
    preview = service.preview(context, COMMIT)
    committed = service.commit(context, COMMIT, preview["preview_hash"])
    with repository._connect() as db:
        db.execute(
            "UPDATE artifacts SET content_text = ? WHERE id = ?",
            ("changed contents", committed["contents_artifact_id"]),
        )
    with pytest.raises(InvalidEvidenceError, match="Artifact changed"):
        service.get(context, committed["id"])


def test_http_candidate_preview_commit_and_detail(tmp_path, monkeypatch) -> None:
    repository, service, _adapter, _context = _service(tmp_path, _catalogue())
    app = FastAPI()
    app.include_router(
        import_api.create_math_release_import_router(
            import_service=service,
            request_context_factory=lambda request, tenant: RequestContext(
                "http-math-import", tenant.id, "admin-a"
            ),
        )
    )
    app.dependency_overrides[current_admin_user] = lambda: {
        "id": "admin-a",
        "tenant_id": "workspace-a",
        "role": "admin",
    }
    app.dependency_overrides[current_tenant] = lambda: Tenant(
        "workspace-a", "Workspace A"
    )
    monkeypatch.setattr(import_api, "get_repository", lambda: repository)
    with TestClient(app) as client:
        preview = client.post(
            "/api/research/math-release-imports/preview",
            json={"source_commit": COMMIT},
        )
        assert preview.status_code == 200
        committed = client.post(
            "/api/research/math-release-imports",
            json={
                "source_commit": COMMIT,
                "expected_preview_hash": preview.json()["preview_hash"],
            },
        )
        assert committed.status_code == 201
        assert committed.json()["admission_state"] == "candidate"
        import_id = committed.json()["id"]
        assert client.get("/api/research/math-release-imports").json()[0]["id"] == import_id
        detail = client.get(
            f"/api/research/math-release-imports/{import_id}?include_families=true"
        )
        assert detail.status_code == 200
        assert len(detail.json()["manifest"]["families"]) == 2
