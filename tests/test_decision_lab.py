import json

import pytest
from fastapi.testclient import TestClient

from app import database, main
from app.core.contracts import RequestContext
from app.core.errors import InvalidEvidenceError
from app.repository import SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.decision_lab import DecisionLabService
from app.services.evidence import EvidenceService
from app.tenancy import Tenant


def _bundle() -> tuple[bytes, bytes, bytes, bytes]:
    requests = {
        "states": [
            {
                "id": "approval_0001",
                "state": "Materials are complete.",
                "questions": {
                    "approve": {
                        "type": "boolean",
                        "instructions": "Can this be approved automatically?",
                        "criteria": {"false": "No", "true": "Yes"},
                    }
                },
            },
            {
                "id": "route_0001",
                "state": "The page button does not respond.",
                "questions": {
                    "route": {
                        "type": "choice",
                        "instructions": "Choose a queue.",
                        "criteria": {"frontend": "Frontend", "backend": "Backend"},
                    }
                },
            },
            {
                "id": "priority_0001",
                "state": "All users are affected.",
                "questions": {
                    "priority": {
                        "type": "score",
                        "instructions": "Choose impact level.",
                        "criteria": ["Low", "Medium", "High", "Critical"],
                    }
                },
            },
        ]
    }
    predictions = {
        "schema_version": "1",
        "execution": {"device": "cpu"},
        "states": [
            {
                "id": "approval_0001",
                "answers": {
                    "approve": {
                        "type": "boolean",
                        "probabilities": {"false": 0.1, "true": 0.9},
                        "value": True,
                    }
                },
            },
            {
                "id": "route_0001",
                "answers": {
                    "route": {
                        "type": "choice",
                        "probabilities": {"frontend": 0.55, "backend": 0.45},
                        "value": "frontend",
                    }
                },
            },
            {
                "id": "priority_0001",
                "answers": {
                    "priority": {
                        "type": "score",
                        "probabilities": {"0": 0.05, "1": 0.1, "2": 0.15, "3": 0.7},
                        "value": 2.5,
                    }
                },
            },
        ],
    }
    metrics = {
        "status": "COMPLETED",
        "predicted_questions": 3,
        "accuracy": 0.5,
    }
    encode = lambda value: json.dumps(value).encode()  # noqa: E731
    return encode(requests), encode(predictions), encode(metrics), b"status: completed\n"


def _service(repository: SQLiteRepository) -> DecisionLabService:
    provider = lambda: repository  # noqa: E731
    return DecisionLabService(
        provider,
        artifact_service=ArtifactService(repository_provider=provider),
        evidence_service=EvidenceService(provider),
    )


def test_nanojev_import_builds_frozen_evidence_and_decision_projection(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "decisions.db")
    repository.init()
    service = _service(repository)
    context = RequestContext(request_id="request-1", workspace_id="workspace-a")
    request_bytes, predictions_bytes, metrics_bytes, receipt_bytes = _bundle()

    result = service.import_nanojev(
        context,
        source_name="NanoJev smoke",
        request_bytes=request_bytes,
        predictions_bytes=predictions_bytes,
        metrics_bytes=metrics_bytes,
        receipt_bytes=receipt_bytes,
        confidence_threshold=0.7,
    )

    assert result["imported"] is True
    assert result["protocol"]["status"] == "frozen"
    assert result["statistics"]["cases"] == 3
    assert result["statistics"]["decided"] == 2
    assert result["statistics"]["manual_review"] == 1
    assert result["statistics"]["question_types"] == {
        "boolean": 1,
        "choice": 1,
        "score": 1,
    }
    assert len(result["protocol"]["artifacts"]) == 4
    assert len(result["protocol"]["claims"]) == 1
    assert len(result["protocol"]["receipts"]) == 1
    assert result["protocol"]["receipts"][0]["reviews"][0]["independent"] is False
    assert len(result["protocol"]["freezes"][0]["members"]) == 4
    assert "decision_case" in {
        item["kind"] for item in repository.search_memory("workspace-a", "button", 20)
    }

    repeated = service.import_nanojev(
        context,
        source_name="Ignored duplicate name",
        request_bytes=request_bytes,
        predictions_bytes=predictions_bytes,
        metrics_bytes=metrics_bytes,
        receipt_bytes=receipt_bytes,
    )
    assert repeated["imported"] is False
    assert repeated["protocol"]["id"] == result["protocol"]["id"]


def test_nanojev_import_fails_closed_before_writing(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "invalid-decisions.db")
    repository.init()
    service = _service(repository)
    context = RequestContext(request_id="request-1", workspace_id="workspace-a")
    request_bytes, predictions_bytes, metrics_bytes, receipt_bytes = _bundle()
    predictions = json.loads(predictions_bytes)
    predictions["states"][0]["answers"]["approve"]["probabilities"] = {
        "false": 0.8,
        "unexpected": 0.2,
    }

    with pytest.raises(InvalidEvidenceError):
        service.import_nanojev(
            context,
            source_name="Invalid bundle",
            request_bytes=request_bytes,
            predictions_bytes=json.dumps(predictions).encode(),
            metrics_bytes=metrics_bytes,
            receipt_bytes=receipt_bytes,
        )

    assert repository.list_evidence_protocols("workspace-a") == []


def test_decision_lab_http_import_is_workspace_scoped(tmp_path, monkeypatch) -> None:
    def connect_to_test_db():
        import sqlite3

        connection = sqlite3.connect(tmp_path / "decision-api.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    user = {
        "id": "admin-a",
        "tenant_id": "workspace-a",
        "email": "admin@example.test",
        "name": "Admin",
        "role": "admin",
    }
    main.app.dependency_overrides[main.current_admin_user] = lambda: user
    main.app.dependency_overrides[main.current_tenant] = lambda: Tenant(
        "workspace-a", "Workspace A"
    )
    request_bytes, predictions_bytes, metrics_bytes, receipt_bytes = _bundle()
    try:
        with TestClient(main.app) as client:
            imported = client.post(
                "/api/decision-lab/import/nanojev",
                data={"source_name": "NanoJev API", "confidence_threshold": "0.7"},
                files={
                    "request_file": ("request.json", request_bytes, "application/json"),
                    "predictions_file": (
                        "predictions.json",
                        predictions_bytes,
                        "application/json",
                    ),
                    "metrics_file": ("metrics.json", metrics_bytes, "application/json"),
                    "receipt_file": ("receipt.yaml", receipt_bytes, "application/yaml"),
                },
            )
            listed = client.get("/api/decision-lab")
            assert imported.status_code == 201
            assert imported.json()["statistics"]["cases"] == 3
            assert listed.status_code == 200
            assert listed.json()["protocol"]["name"] == "NanoJev API"

            main.app.dependency_overrides[main.current_tenant] = lambda: Tenant(
                "workspace-b", "Workspace B"
            )
            isolated = client.get("/api/decision-lab")
            assert isolated.status_code == 200
            assert isolated.json()["protocol"] is None
            assert isolated.json()["cases"] == []
    finally:
        main.app.dependency_overrides.clear()
