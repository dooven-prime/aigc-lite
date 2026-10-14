"""Only a frozen, dataset-specific in-vitro observation may qualify."""

from __future__ import annotations

import copy
import sqlite3
from dataclasses import replace

import pytest
from authority_support import provision_admin
from fastapi.testclient import TestClient

from app import database, main
from app.core.contracts import RequestContext
from app.core.errors import InvalidEvidenceError
from app.core.qualification import KnowledgeAdmissionDraft
from app.profiles.bio_in_vitro import (
    CONTRACT_VERSION,
    PROFILE,
    replay,
    statement,
    validate_witness,
    witness_sha256,
)
from app.repository import SQLiteRepository
from app.services.bio_in_vitro_case import BioInVitroCaseService
from app.services.qualification import QualificationService
from app.tenancy import Tenant


def witness(*, threshold: str = "0.5") -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "study": {
            "study_id": "retrospective-assay-1",
            "completed_at": "2026-09-01",
            "biological_model": "cell-line-X",
            "assay": "endpoint-assay",
            "endpoint": "signal",
            "unit": "arbitrary-units",
            "timepoint": "24h",
        },
        "analysis": {"direction": "increase", "minimum_mean_delta": threshold},
        "pairs": [
            {"pair_id": "pair-1", "batch_id": "batch-1", "control": "1.0", "treated": "1.8"},
            {"pair_id": "pair-2", "batch_id": "batch-1", "control": "2.0", "treated": "2.6"},
            {"pair_id": "pair-3", "batch_id": "batch-2", "control": "3.0", "treated": "3.4"},
        ],
    }


def _setup(tmp_path):
    repository = SQLiteRepository(tmp_path / "bio.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    service = BioInVitroCaseService(provider)
    context = RequestContext("bio-request", "bio-workspace", "research-admin")
    return repository, service, context


def test_exact_observation_qualifies_without_current_use_or_execution(tmp_path) -> None:
    repository, service, context = _setup(tmp_path)
    value = service.submit(context, witness())
    assert value["observed_result"]["mean_delta_exact"] == {"numerator": 3, "denominator": 5}
    assert value["observed_result"]["sample_variance_exact"] == {
        "numerator": 1, "denominator": 25,
    }
    assert value["observed_result"]["declared_batch_count"] == 2
    assert value["evaluation"]["verdict"] == "ADMITTED"
    receipt = value["qualification_receipt"]
    assert receipt is not None
    assert receipt["profile"] == PROFILE.profile_id
    assert receipt["evidence_vector"]["independent_validation"] == "undetermined"
    assert receipt["evidence_vector"]["external_reality"] == "undetermined"
    assert value["current_use_binding"] is None
    assert value["knowledge_admission_required"] is True
    assert repository.list_current_use_bindings(context.workspace_id, PROFILE.profile_id) == []
    assert repository.list_authorization_grants(context.workspace_id) == []
    assert QualificationService(lambda: repository).qualified_search(
        context, "retrospective-assay-1", PROFILE.profile_id
    ) == []
    claim = repository.get_research_claim(context.workspace_id, value["claim_revision_id"])
    assert claim["scope"].startswith("Exact submitted in-vitro measurement pairs only")
    assert "clinical" in " ".join(claim["negative_boundaries"])
    assert repository.get_run(context.workspace_id, value["run_id"])["status"] == "succeeded"


def test_unsupported_threshold_remains_recorded_but_blocked(tmp_path) -> None:
    repository, service, context = _setup(tmp_path)
    value = service.submit(context, witness(threshold="0.7"))
    assert value["observed_result"]["threshold_met"] is False
    assert value["evaluation"]["verdict"] == "BLOCKED"
    assert "observed_threshold_not_met" in value["evaluation"]["blockers"]
    assert value["qualification_receipt"] is None
    attempts = repository.list_research_verification_attempts(
        context.workspace_id, value["claim_revision_id"]
    )
    assert len(attempts) == 1 and attempts[0]["outcome"] == "failed"
    assert repository.get_artifact(
        context.workspace_id, value["witness_artifact_id"]
    )["content_text"]
    assert repository.list_current_use_bindings(context.workspace_id, PROFILE.profile_id) == []


def test_future_completion_date_is_rejected_before_ledger_writes(tmp_path) -> None:
    repository, service, context = _setup(tmp_path)
    value = witness()
    value["study"]["completed_at"] = "2099-01-01"
    with pytest.raises(InvalidEvidenceError):
        service.submit(context, value)
    assert repository.list_research_claims(context.workspace_id, research_case_id=None) == []


def test_tampered_certificate_cannot_issue_another_receipt(tmp_path) -> None:
    repository, service, context = _setup(tmp_path)
    value = service.submit(context, witness())
    assert value["evaluation"]["verdict"] == "ADMITTED"
    with sqlite3.connect(tmp_path / "bio.db") as db:
        db.execute(
            "UPDATE artifacts SET content_text = ? WHERE id = ?",
            ('{"forged":true}', value["certificate_artifact_id"]),
        )
    rechecked = QualificationService(lambda: repository).evaluate(
        context, value["claim_revision_id"], PROFILE.profile_id
    )
    assert rechecked["evaluation"]["verdict"] == "UNRESOLVED"
    assert rechecked["qualification_receipt"] is None
    assert len(repository.list_qualification_receipts(
        context.workspace_id, value["claim_revision_id"]
    )) == 1  # Historical receipt is not erased by later tampering.
    with pytest.raises(InvalidEvidenceError, match="stale qualification evidence"):
        admin_context, authority = provision_admin(repository, context)
        QualificationService(lambda: repository).admit_knowledge(
            admin_context,
            KnowledgeAdmissionDraft(
                qualification_receipt_id=value["qualification_receipt"]["qualification_receipt_id"],
                admission_policy_id="knowledge.default.v1",
                rationale="Attempt to reuse a tampered certificate",
            ),
            authority=authority,
        )


def test_statement_drift_does_not_inherit_dataset_qualification(tmp_path) -> None:
    repository, service, context = _setup(tmp_path)
    value = service.submit(context, witness())
    with sqlite3.connect(tmp_path / "bio.db") as db:
        db.execute(
            "UPDATE research_claim_revisions SET statement = ? WHERE id = ?",
            ("The intervention is clinically safe and effective.", value["claim_revision_id"]),
        )
    rechecked = QualificationService(lambda: repository).evaluate(
        context, value["claim_revision_id"], PROFILE.profile_id
    )
    assert rechecked["evaluation"]["verdict"] != "ADMITTED"
    assert rechecked["qualification_receipt"] is None


def test_changed_profile_cannot_reuse_historical_receipt_for_knowledge(tmp_path) -> None:
    repository, service, context = _setup(tmp_path)
    value = service.submit(context, witness())
    qualification = QualificationService(lambda: repository)
    qualification._registry.get(PROFILE.profile_id).profile = replace(
        PROFILE, description="Different checker policy"
    )
    with pytest.raises(InvalidEvidenceError, match="stale qualification evidence"):
        admin_context, authority = provision_admin(repository, context)
        qualification.admit_knowledge(
            admin_context,
            KnowledgeAdmissionDraft(
                qualification_receipt_id=value["qualification_receipt"]["qualification_receipt_id"],
                admission_policy_id="knowledge.default.v1",
                rationale="Attempt to reuse an old policy snapshot",
            ),
            authority=authority,
        )


@pytest.mark.parametrize("change", [
    lambda value: value.update({"claim_type": "clinical_efficacy"}),
    lambda value: value["study"].update({"endpoint": "signal\nproven safe"}),
    lambda value: value["pairs"][1].update({"pair_id": "pair-1"}),
    lambda value: value["pairs"][0].update({"treated": "NaN"}),
    lambda value: value["analysis"].update({"direction": "clinical benefit"}),
])
def test_out_of_contract_or_malformed_input_is_rejected(change) -> None:
    value = witness()
    change(value)
    with pytest.raises(InvalidEvidenceError):
        validate_witness(value)


def test_fractional_mean_and_unverified_batch_labels() -> None:
    value = witness(threshold="0.6")
    value["pairs"][2]["treated"] = "3.5"
    result = replay(value)
    assert result["mean_delta_exact"] == {"numerator": 19, "denominator": 30}
    assert result["threshold_met"] is True
    assert result["declared_batch_count"] == 2
    assert any("do not establish biological independence" in item for item in result["limitations"])


def test_decrease_statement_has_an_explicit_negative_bound() -> None:
    value = witness()
    value["analysis"]["direction"] = "decrease"
    for item, treated in zip(value["pairs"], ("0.2", "1.4", "2.6"), strict=True):
        item["treated"] = treated
    assert replay(value)["threshold_met"] is True
    assert "<= -0.5 arbitrary-units" in statement(value, witness_sha256(value))


def test_http_submission_is_admin_only_and_does_not_bind_knowledge(tmp_path, monkeypatch) -> None:
    def connect():
        db = sqlite3.connect(tmp_path / "bio-http.db", timeout=10)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(database, "_connect", connect)
    user = {
        "id": "bio-admin", "tenant_id": "bio-workspace", "email": "bio@example.test",
        "name": "Admin", "role": "admin",
    }
    main.app.dependency_overrides[main.current_admin_user] = lambda: user
    main.app.dependency_overrides[main.current_tenant] = lambda: Tenant(
        "bio-workspace", "Bio Workspace"
    )
    try:
        with TestClient(main.app) as client:
            contract = client.get("/api/research/bio-in-vitro/contract")
            assert contract.status_code == 200
            assert contract.json()["profile"]["profile_id"] == PROFILE.profile_id
            profiles = client.get("/api/qualification/profiles")
            assert PROFILE.profile_id in {item["profile_id"] for item in profiles.json()}
            created = client.post("/api/research/bio-in-vitro/witnesses", json=witness())
            assert created.status_code == 201, created.text
            assert created.json()["evaluation"]["verdict"] == "ADMITTED"
            with connect() as db:
                assert db.execute("SELECT COUNT(*) FROM qualification_receipts").fetchone()[0] == 1
                assert db.execute("SELECT COUNT(*) FROM current_use_bindings").fetchone()[0] == 0
                assert db.execute("SELECT COUNT(*) FROM authorization_grants").fetchone()[0] == 0
            malformed = copy.deepcopy(witness())
            malformed["clinical_efficacy"] = True
            assert client.post("/api/research/bio-in-vitro/witnesses", json=malformed).status_code == 422
            main.app.dependency_overrides.pop(main.current_admin_user)
            assert client.post("/api/research/bio-in-vitro/witnesses", json=witness()).status_code in {401, 403}
    finally:
        main.app.dependency_overrides.clear()
