import json
import subprocess
import sys
import zipfile

from app.core.contracts import RequestContext
from app.repository import SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.assurance import (
    AssuranceBundleService,
    AssuranceBundleVerifier,
    _assurance_vector,
)
from app.services.evidence import EvidenceService
from app.services.research_registry import ResearchRegistryService


def _registry() -> bytes:
    return json.dumps(
        {
            "registry_id": "assurance-fixture",
            "registry_version": "1.0.0",
            "scope": {
                "as_of_date": "2026-09-29",
                "authority": "research_only",
            },
            "claim_contract": {
                "required_fields": [
                    "claim_id",
                    "statement",
                    "claim_type",
                    "scope",
                    "source_refs",
                    "calculation_version",
                    "uncertainty_status",
                    "causal_status",
                    "revision_status",
                ],
                "claim_types": ["OBSERVATION"],
            },
            "claims": [
                {
                    "claim_id": "CLM-ASSURANCE",
                    "statement": "The fixture has one content-addressed observation.",
                    "claim_type": "OBSERVATION",
                    "scope": "Fixture only.",
                    "source_refs": [
                        {
                            "source_id": "SRC-ASSURANCE",
                            "locator": "fixture.json!claim=CLM-ASSURANCE",
                            "sha256": "a" * 64,
                        }
                    ],
                    "calculation_version": "fixture-v1",
                    "uncertainty_status": "observed",
                    "causal_status": "NOT_ESTABLISHED",
                    "revision_status": "current",
                }
            ],
        }
    ).encode()


def _bundle(tmp_path):
    repository = SQLiteRepository(tmp_path / "assurance.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    artifacts = ArtifactService(repository_provider=provider)
    research = ResearchRegistryService(
        provider,
        artifact_service=artifacts,
        evidence_service=EvidenceService(provider),
    )
    context = RequestContext(
        request_id="assurance-export",
        workspace_id="workspace-a",
        principal_id="admin-a",
    )
    imported = research.import_frontier(
        context, source_name="Assurance fixture", registry_bytes=_registry()
    )
    service = AssuranceBundleService(provider)
    return service, context, imported["case"]["id"]


def test_assurance_bundle_is_deterministic_and_verifies_offline(tmp_path) -> None:
    service, context, case_id = _bundle(tmp_path)

    first, manifest = service.export_zip(context, case_id)
    second, repeated_manifest = service.export_zip(context, case_id)
    bundle_path = tmp_path / "assurance.zip"
    bundle_path.write_bytes(first)
    report = AssuranceBundleVerifier().verify(bundle_path)

    assert first == second
    assert manifest == repeated_manifest
    assert report["valid"] is True
    assert report["bundle_digest"] == manifest["bundle_digest"]
    assert report["assurance"]["identity"]["status"] == "verified"
    assert report["assurance"]["epistemic_state"]["status"] == "undetermined"
    assert report["assurance"]["authority_state"]["status"] == "blocked"
    assert any(item["code"] == "bundle_unsigned" for item in report["limitations"])

    completed = subprocess.run(
        [sys.executable, "-m", "app.cli", "verify", str(bundle_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["valid"] is True


def test_assurance_bundle_detects_tampered_member(tmp_path) -> None:
    service, context, case_id = _bundle(tmp_path)
    archive, _manifest = service.export_zip(context, case_id)
    bundle_path = tmp_path / "assurance.zip"
    bundle_path.write_bytes(archive)
    directory = tmp_path / "expanded"
    with zipfile.ZipFile(bundle_path) as bundle:
        bundle.extractall(directory)
    claims_path = directory / "claims.json"
    claims_path.write_text(
        claims_path.read_text(encoding="utf-8").replace("content-addressed", "silently-mutated"),
        encoding="utf-8",
    )

    report = AssuranceBundleVerifier().verify(directory)

    assert report["valid"] is False
    assert report["assurance"]["identity"]["status"] == "failed"
    assert (
        next(item for item in report["checks"] if item["code"] == "member:claims.json")["status"]
        == "failed"
    )


def test_assurance_authority_excludes_expired_and_exhausted_grants() -> None:
    grant = {
        "id": "grant-1",
        "qualification_receipt_id": "receipt-1",
        "state": "active",
        "expires_at": "2000-01-01T00:00:00+00:00",
        "calls_used": 0,
        "max_calls": 2,
    }
    documents = {
        "qualification.json": {
            "receipts": [{"id": "receipt-1", "verdict": "ADMITTED"}],
            "current_use_bindings": [
                {
                    "qualification_receipt_id": "receipt-1",
                    "state": "current",
                }
            ],
            "authorization_grants": [grant],
        }
    }

    expired = _assurance_vector(documents, integrity_ok=True)
    assert expired["authority_state"] == {
        "status": "blocked",
        "authorization_grant_ids": [],
    }

    grant["expires_at"] = "2999-01-01T00:00:00+00:00"
    current = _assurance_vector(documents, integrity_ok=True)
    assert current["authority_state"] == {
        "status": "authorized",
        "authorization_grant_ids": ["grant-1"],
    }

    grant["calls_used"] = 2
    exhausted = _assurance_vector(documents, integrity_ok=True)
    assert exhausted["authority_state"] == {
        "status": "blocked",
        "authorization_grant_ids": [],
    }
