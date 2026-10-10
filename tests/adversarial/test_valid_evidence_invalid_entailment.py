"""Valid premise records must not silently become an entailed conclusion."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import database, main
from app.auth import hash_password
from app.core.artifacts import ArtifactDraft, ArtifactKind
from app.core.contracts import RequestContext
from app.core.evidence import ExecutionReceiptDraft, ProtocolDraft, ProtocolStatus
from app.core.qualification import (
    QualificationVerdict,
    ValidationModality,
    canonical_hash,
    claim_semantic_hash,
)
from app.core.research import (
    VerificationAttemptDraft,
    VerificationKind,
    VerificationOutcome,
)
from app.profiles.propositional_entailment import (
    CLAIM_KIND,
    ENTAILMENT_PRINCIPAL,
    PREMISE_CONTRACT,
    PREMISE_PRINCIPAL,
    PROFILE,
    build_entailment_certificate,
    certificate_input_digest,
)
from app.repository import SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.evidence import EvidenceService
from app.services.qualification import QualificationService
from app.services.research_registry import ResearchRegistryService


def _setup(tmp_path, conclusion: str, *, second_premise: str = "B"):
    repository = SQLiteRepository(tmp_path / "logic.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    artifacts = ArtifactService(repository_provider=provider)
    evidence = EvidenceService(provider)
    research = ResearchRegistryService(
        provider, artifact_service=artifacts, evidence_service=evidence
    )
    qualification = QualificationService(
        provider, artifact_service=artifacts, evidence_service=evidence
    )
    context = RequestContext(
        request_id="logic-candidate",
        workspace_id="workspace-logic",
        principal_id="research-agent",
    )
    premises = []
    for key, formula in (("E1", "A=>B"), ("E2", second_premise)):
        artifact = artifacts.create_artifact(
            context,
            ArtifactDraft(
                name=f"{key}.json",
                kind=ArtifactKind.JSON,
                media_type="application/json",
                content_text=json.dumps(
                    {
                        "contract_version": PREMISE_CONTRACT,
                        "key": key,
                        "formula": formula,
                    },
                    sort_keys=True,
                ),
                metadata={"role": "logic_premise"},
            ),
        )
        premises.append(
            {
                "key": key,
                "artifact_id": artifact["id"],
                "sha256": artifact["content_hash"],
                "formula": formula,
            }
        )
    protocol = evidence.create_protocol(
        context,
        ProtocolDraft(
            name="Finite propositional entailment fixture",
            profile=PROFILE.profile_id,
            purpose="Preserve premise identity separately from consequence.",
            scope="Two frozen premises over A and B.",
            completion_predicate="A qualification decision is recorded.",
            status=ProtocolStatus.FROZEN,
        ),
        content_hash=canonical_hash(premises),
    )
    receipt = evidence.create_receipt(
        context,
        protocol["id"],
        ExecutionReceiptDraft(
            status="candidate_registered",
            input_digest=canonical_hash(premises),
            output_digest=premises[0]["sha256"],
            artifact_ids=(premises[0]["artifact_id"],),
        ),
    )
    claim_values = {
        "claim_key": f"LOGIC-{conclusion}",
        "revision_number": 1,
        "claim_type": CLAIM_KIND,
        "statement": conclusion,
        "scope": "Classical two-valued propositional logic over A and B.",
        "definitions": [],
        "negative_boundaries": ["A countermodel refutes entailment, not actual-world truth."],
        "dependency_claim_ids": [],
        "parent_revision_id": None,
    }
    case = repository.create_research_registry(
        context.workspace_id,
        {
            "protocol_id": protocol["id"],
            "receipt_id": receipt["id"],
            "profile": PROFILE.profile_id,
            "name": "Entailment counterexample",
            "registry_id": f"LOGIC-{conclusion}",
            "registry_version": "1",
            "authority": "candidate_only",
            "status": "frozen",
            "source_artifact_id": premises[0]["artifact_id"],
        },
        [
            {
                "ref_key": item["key"],
                "source_key": item["key"],
                "locator": f"artifact:{item['artifact_id']}",
                "content_hash": item["sha256"],
                "status": "frozen",
            }
            for item in premises
        ],
        [
            {
                **claim_values,
                "source_ref_keys": ["E1", "E2"],
                "method_revision": "logic.propositional.ab.v1",
                "lifecycle_status": "candidate",
                "closure_status": "closed",
                "semantic_hash": claim_semantic_hash(claim_values),
            }
        ],
    )
    claim = repository.list_research_claims(
        context.workspace_id, research_case_id=case["id"], limit=1
    )[0]
    premise_context = RequestContext(
        request_id="logic-premise-check",
        workspace_id=context.workspace_id,
        principal_id=PREMISE_PRINCIPAL,
    )
    premise_attempts = []
    for item in premises:
        premise_attempts.append(
            research.record_verification_attempt(
                premise_context,
                claim["id"],
                VerificationAttemptDraft(
                    kind=VerificationKind.SOURCE_AUDIT,
                    outcome=VerificationOutcome.PASSED,
                    validation_modality=ValidationModality.EXACT_REPLAY,
                    method="Check frozen premise bytes and grammar.",
                    scope="Premise identity, not consequence or actual-world truth.",
                    input_digest=item["sha256"],
                    output_digest=item["sha256"],
                    artifact_ids=(item["artifact_id"],),
                ),
            )
        )
    return (
        repository,
        artifacts,
        research,
        qualification,
        context,
        claim,
        premises,
        premise_attempts,
    )


def _record_entailment(
    artifacts,
    research,
    context,
    claim,
    premises,
    *,
    outcome: VerificationOutcome,
    principal: str = ENTAILMENT_PRINCIPAL,
    tamper: bool = False,
):
    certificate = build_entailment_certificate(
        claim["id"], claim["semantic_hash"], premises, claim["statement"]
    )
    if tamper:
        certificate["entailed"] = True
        certificate["countermodel"] = None
    artifact = artifacts.create_artifact(
        context,
        ArtifactDraft(
            name="truth-table.json",
            kind=ArtifactKind.JSON,
            media_type="application/json",
            content_text=json.dumps(certificate, sort_keys=True),
            metadata={"role": "logic_entailment_certificate"},
        ),
    )
    verifier = RequestContext(
        request_id="logic-entailment-check",
        workspace_id=context.workspace_id,
        principal_id=principal,
    )
    attempt = research.record_verification_attempt(
        verifier,
        claim["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.CALCULATION,
            outcome=outcome,
            validation_modality=ValidationModality.LOGICAL_ENTAILMENT,
            method="Recompute all four A/B truth assignments.",
            scope="Only the frozen premises and conclusion.",
            input_digest=certificate_input_digest(claim["id"], claim["semantic_hash"], premises),
            output_digest=artifact["content_hash"],
            artifact_ids=(artifact["id"],),
        ),
    )
    return certificate, artifact, attempt


def test_valid_evidence_invalid_entailment(tmp_path) -> None:
    (
        repository,
        artifacts,
        research,
        qualification,
        context,
        claim,
        premises,
        premise_attempts,
    ) = _setup(tmp_path, "A")
    assert len(premise_attempts) == 2
    assert all(item["outcome"] == "passed" for item in premise_attempts)

    missing = qualification.evaluate(context, claim["id"], PROFILE.profile_id)
    assert missing["evaluation"]["verdict"] == QualificationVerdict.UNRESOLVED
    assert "insufficient_entailment_evidence" in missing["evaluation"]["blockers"]
    assert missing["qualification_receipt"] is None

    # A model can upload a matching table and even claim PASS, but it cannot
    # appoint itself as the server-derived entailment verifier.
    _record_entailment(
        artifacts,
        research,
        context,
        claim,
        premises,
        outcome=VerificationOutcome.PASSED,
        principal=context.principal_id,
    )
    forged = qualification.evaluate(context, claim["id"], PROFILE.profile_id)
    assert forged["evaluation"]["verdict"] == QualificationVerdict.UNRESOLVED
    assert forged["qualification_receipt"] is None

    # Even a server-identity attempt cannot turn a false certificate into
    # consequence: the Gate checks every row against the frozen premises.
    _record_entailment(
        artifacts,
        research,
        context,
        claim,
        premises,
        outcome=VerificationOutcome.PASSED,
        tamper=True,
    )
    tampered = qualification.evaluate(context, claim["id"], PROFILE.profile_id)
    assert tampered["evaluation"]["verdict"] == QualificationVerdict.UNRESOLVED
    assert tampered["qualification_receipt"] is None

    certificate, artifact, failed_attempt = _record_entailment(
        artifacts,
        research,
        context,
        claim,
        premises,
        outcome=VerificationOutcome.FAILED,
    )
    assert certificate["entailed"] is False
    assert certificate["countermodel"] == {"A": False, "B": True}
    counterexample = next(
        row
        for row in certificate["truth_table"]
        if row["assignment"] == certificate["countermodel"]
    )
    assert counterexample["premises"] == [True, True]
    assert counterexample["conclusion"] is False

    result = qualification.evaluate(context, claim["id"], PROFILE.profile_id)
    assert result["evaluation"]["verdict"] == QualificationVerdict.BLOCKED
    assert "premises_do_not_entail_claim" in result["evaluation"]["blockers"]
    assert result["qualification_receipt"] is None
    assert result["current_use_binding"] is None
    status = qualification.claim_status(context, claim["id"])
    assert status["receipts"] == []
    assert status["current_use_bindings"] == []
    assert qualification.qualified_search(context, claim["claim_key"], PROFILE.profile_id) == []
    assert repository.get_artifact(context.workspace_id, artifact["id"]) is not None
    recorded = repository.list_research_verification_attempts(context.workspace_id, claim["id"])
    assert {item["id"] for item in premise_attempts} <= {
        item["id"] for item in recorded if item["outcome"] == "passed"
    }
    assert failed_attempt["id"] in {item["id"] for item in recorded if item["outcome"] == "failed"}


def test_two_premises_are_jointly_necessary_but_not_knowledge_admission(tmp_path) -> None:
    (
        _repository,
        artifacts,
        research,
        qualification,
        context,
        claim,
        premises,
        _premise_attempts,
    ) = _setup(tmp_path, "B", second_premise="A")
    certificate, _artifact, _attempt = _record_entailment(
        artifacts,
        research,
        context,
        claim,
        premises,
        outcome=VerificationOutcome.PASSED,
    )
    assert certificate["entailed"] is True
    assert certificate["countermodel"] is None
    assert any(
        row["premises"][0] and not row["conclusion"] for row in certificate["truth_table"]
    ), "E1 alone must not entail B"
    assert any(
        row["premises"][1] and not row["conclusion"] for row in certificate["truth_table"]
    ), "E2 alone must not entail B"
    assert all(
        not all(row["premises"]) or row["conclusion"] for row in certificate["truth_table"]
    ), "E1 and E2 together must entail B"
    result = qualification.evaluate(context, claim["id"], PROFILE.profile_id)
    assert result["evaluation"]["verdict"] == QualificationVerdict.ADMITTED
    assert result["qualification_receipt"] is not None
    assert result["current_use_binding"] is None
    assert qualification.qualified_search(context, claim["claim_key"], PROFILE.profile_id) == []


def test_http_caller_cannot_impersonate_entailment_verifier(tmp_path, monkeypatch) -> None:
    (
        repository,
        artifacts,
        _research,
        qualification,
        context,
        claim,
        premises,
        _premise_attempts,
    ) = _setup(tmp_path, "A")
    repository.create_tenant("Logic Workspace", context.workspace_id)
    user = repository.create_user(
        context.workspace_id,
        "logic-admin@example.test",
        "Logic Admin",
        hash_password("long-enough-test-password"),
        role="admin",
    )
    assert not user["id"].startswith("system:verifier:")

    certificate = build_entailment_certificate(
        claim["id"], claim["semantic_hash"], premises, claim["statement"]
    )
    certificate_artifact = artifacts.create_artifact(
        context,
        ArtifactDraft(
            name="external-truth-table.json",
            kind=ArtifactKind.JSON,
            media_type="application/json",
            content_text=json.dumps(certificate, sort_keys=True),
            metadata={"role": "logic_entailment_certificate"},
        ),
    )

    def connect_to_test_db():
        connection = sqlite3.connect(tmp_path / "logic.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    monkeypatch.setattr(main.settings, "scheduler_enabled", False)
    payload = {
        "kind": "calculation",
        "outcome": "failed",
        "validation_modality": ValidationModality.LOGICAL_ENTAILMENT.value,
        "method": "Claim to have run the frozen truth-table checker.",
        "scope": "Exact frozen A/B revision.",
        "input_digest": certificate_input_digest(claim["id"], claim["semantic_hash"], premises),
        "output_digest": certificate_artifact["content_hash"],
        "artifact_ids": [certificate_artifact["id"]],
    }
    url = f"/api/research-registry/claims/{claim['id']}/verification-attempts"
    with TestClient(main.app) as client:
        assert client.post(url, json=payload).status_code == 401
        login = client.post(
            "/api/auth/login",
            json={
                "email": "logic-admin@example.test",
                "password": "long-enough-test-password",
            },
        )
        assert login.status_code == 200
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}",
            "X-User-Id": ENTAILMENT_PRINCIPAL,
            "X-Principal-Id": ENTAILMENT_PRINCIPAL,
            "X-Forwarded-User": ENTAILMENT_PRINCIPAL,
        }
        injected = client.post(
            url,
            headers=headers,
            json={
                **payload,
                "verifier_lineage": {
                    "principal_id": ENTAILMENT_PRINCIPAL,
                    "runtime_derived": True,
                    "model_route": None,
                },
            },
        )
        assert injected.status_code == 422
        attempted = client.post(url, headers=headers, json=payload)
        assert attempted.status_code == 201
        assert attempted.json()["verifier_lineage"]["runtime_derived"] is True
        assert attempted.json()["verifier_lineage"]["principal_id"] == user["id"]
        assert attempted.json()["verifier_lineage"]["principal_id"] != ENTAILMENT_PRINCIPAL

    decision = qualification.evaluate(context, claim["id"], PROFILE.profile_id)
    assert decision["evaluation"]["verdict"] == QualificationVerdict.UNRESOLVED
    assert "insufficient_entailment_evidence" in decision["evaluation"]["blockers"]
    assert decision["qualification_receipt"] is None
    assert decision["current_use_binding"] is None
    assert qualification.qualified_search(context, claim["claim_key"], PROFILE.profile_id) == []


def test_propositional_ab_v1_contract_is_pinned() -> None:
    assert PROFILE.profile_id == "logic.propositional.ab.v1"
    assert PROFILE.version == 1
    assert PROFILE.policy_version == "logic.propositional.ab.policy.v1"
    assert PROFILE.content_hash == (
        "5ec6ad6e24af7a51bdd07faeb9bf7fb8486fb9122fd54be0e06eeae310fc5475"
    )
    with pytest.raises(ValueError, match="Only two E1/E2 premises"):
        build_entailment_certificate(
            "revision",
            "0" * 64,
            [
                {"key": "E1", "formula": "A=>B"},
                {"key": "E2", "formula": "not A"},
            ],
            "B",
        )
