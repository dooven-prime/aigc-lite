"""Synthetic withdrawal cascade, not a validation of the OpenAI mathematics.

Model the October 2026 topology: one failed construction, two hard dependents,
and a separate paper whose citation changes without a proof dependency. Fake
kernel certificates exercise qualification plumbing; they are not Lean runs.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import research as research_api
from app.auth import current_admin_user
from app.core.artifacts import ArtifactDraft, ArtifactKind
from app.core.contracts import RequestContext
from app.core.errors import InvalidEvidenceError
from app.core.invalidation import (
    InvalidationDecisionDraft,
    InvalidationReason,
    InvalidationScope,
    NoticeVerificationDraft,
)
from app.core.kernel_verification import KERNEL_EXECUTION_CONTRACT_VERSION
from app.core.qualification import (
    AuthorizationGrantDraft,
    CurrentUseState,
    KnowledgeAdmissionDraft,
    MathTheoremCandidateDraft,
    QualificationVerdict,
    ValidationModality,
)
from app.core.research import VerificationAttemptDraft, VerificationKind, VerificationOutcome
from app.profiles.math_theorem import KERNEL_CERTIFICATE_VERSION, PROFILE_ID
from app.repository import SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.evidence import EvidenceService
from app.services.invalidation import InvalidationService
from app.services.qualification import QualificationService
from app.services.research_registry import ResearchRegistryService
from app.tenancy import Tenant, current_tenant


def _services(tmp_path):
    repository = SQLiteRepository(tmp_path / "withdrawal-cascade.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    artifacts = ArtifactService(repository_provider=provider)
    evidence = EvidenceService(provider)
    qualification = QualificationService(
        provider, artifact_service=artifacts, evidence_service=evidence
    )
    research = ResearchRegistryService(
        provider, artifact_service=artifacts, evidence_service=evidence
    )
    context = RequestContext("withdrawal-fixture", "workspace-a", "reviewer-a")
    return repository, artifacts, research, qualification, context


def _register(qualification, context, key, statement, *, dependencies=(), parent=None):
    return qualification.register_math_theorem(
        context,
        MathTheoremCandidateDraft(
            claim_key=key,
            name=key,
            statement=statement,
            scope="Synthetic withdrawal regression fixture only.",
            dependency_claim_ids=dependencies,
            parent_revision_id=parent,
        ),
    )["claim"]


def _qualify(artifacts, research, qualification, context, claim, *, admit=True):
    """Use a deliberately synthetic certificate to exercise gate and binding logic."""

    proof = artifacts.create_artifact(
        context,
        ArtifactDraft(
            name=f"{claim['claim_key']}.lean",
            kind=ArtifactKind.TEXT,
            media_type="text/plain",
            content_text=f"-- synthetic fixture for {claim['id']}",
            metadata={"role": "formal_proof", "language": "Lean"},
        ),
    )
    certificate = artifacts.create_artifact(
        context,
        ArtifactDraft(
            name=f"{claim['claim_key']}.kernel-certificate.json",
            kind=ArtifactKind.JSON,
            media_type="application/json",
            content_text=json.dumps(
                {
                    "contract_version": KERNEL_CERTIFICATE_VERSION,
                    "execution_contract_version": KERNEL_EXECUTION_CONTRACT_VERSION,
                    "claim_revision_id": claim["id"],
                    "claim_semantic_hash": claim["semantic_hash"],
                    "statement_hash": claim["semantic_hash"],
                    "proof_artifact_id": proof["id"],
                    "proof_artifact_hash": proof["content_hash"],
                    "checker": {
                        "backend": "lean4",
                        "name": "lean-kernel",
                        "version": "fixture-only",
                        "executable_hash": "a" * 64,
                        "toolchain_hash": "a" * 64,
                    },
                    "status": "passed",
                    "axioms": [],
                    "sorry_present": False,
                    "dependencies": [],
                    "invocation": {"command": ["lean", "Main.lean"], "exit_code": 0},
                    "isolation": {"shell": False, "request_controls_command": False},
                },
                sort_keys=True,
            ),
            metadata={"role": "kernel_certificate", "synthetic_fixture": True},
        ),
    )
    kernel_context = RequestContext(
        f"kernel-{claim['id']}", context.workspace_id, "system:verifier:lean-kernel"
    )
    kernel_attempt = research.record_verification_attempt(
        kernel_context,
        claim["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.CALCULATION,
            outcome=VerificationOutcome.PASSED,
            validation_modality=ValidationModality.KERNEL_CHECK,
            method="Synthetic certificate; no Lean execution in this test.",
            scope="Exact frozen fixture revision.",
            input_digest=claim["semantic_hash"],
            output_digest=certificate["content_hash"],
            artifact_ids=(proof["id"], certificate["id"]),
        ),
    )
    research.record_verification_attempt(
        context,
        claim["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.REVIEW,
            outcome=VerificationOutcome.PASSED,
            validation_modality=ValidationModality.EXPERT_REVIEW,
            method="Synthetic statement-alignment fixture.",
            scope="Exact frozen fixture revision.",
            input_digest=claim["semantic_hash"],
            output_digest=proof["content_hash"],
            artifact_ids=(proof["id"],),
        ),
    )
    result = qualification.evaluate(context, claim["id"], PROFILE_ID)
    assert result["evaluation"]["verdict"] == QualificationVerdict.ADMITTED
    receipt = result["qualification_receipt"]
    assert receipt is not None
    assert result["current_use_binding"] is None
    if admit:
        admission = qualification.admit_knowledge(
            context,
            KnowledgeAdmissionDraft(
                qualification_receipt_id=receipt["id"],
                admission_policy_id="knowledge.default.v1",
                rationale="Synthetic fixture: explicitly select this receipt for current use.",
            ),
        )
        assert admission["current_use_binding"]["state"] == CurrentUseState.CURRENT
    return receipt, kernel_attempt


def test_withdrawn_construction_taints_hard_dependents_but_not_citation_only(tmp_path):
    repository, artifacts, research, qualification, context = _services(tmp_path)
    source = _register(
        qualification, context, "WEIL-R1", "A construction proves the original claim."
    )
    dependent = _register(
        qualification,
        context,
        "KUGA-SATAKE-R1",
        "The first dependent conclusion uses the construction.",
        dependencies=(source["id"],),
    )
    transitive = _register(
        qualification,
        context,
        "K3-PRODUCT-R1",
        "The second dependent conclusion uses the first conclusion.",
        dependencies=(dependent["id"],),
    )
    citation_only = _register(
        qualification,
        context,
        "CITATION-ONLY-R1",
        "An unrelated conclusion merely cites a companion paper.",
    )
    original_receipts = {}
    for claim in (source, dependent, transitive, citation_only):
        receipt, _attempt = _qualify(artifacts, research, qualification, context, claim)
        original_receipts[claim["id"]] = receipt

    source_binding = repository.get_current_use_binding(
        context.workspace_id, source["id"], PROFILE_ID
    )
    # A trusted withdrawal decision is represented here by an explicit binding
    # transition. Ingesting and authenticating the upstream notice is not yet
    # implemented; this test starts at the resulting local invalidation event.
    repository.upsert_current_use_binding(
        context.workspace_id,
        {
            **source_binding,
            "state": CurrentUseState.STALE.value,
            "stale_reason": "withdrawn_upstream_construction",
            "bound_by": "system:withdrawal-fixture",
        },
    )

    # A new gate run is BLOCKED. Current-use reads lazily refresh old bindings;
    # evaluation alone does not mutate authority or historical receipts.
    for claim in (dependent, transitive):
        reevaluated = qualification.evaluate(context, claim["id"], PROFILE_ID)
        assert reevaluated["evaluation"]["verdict"] == QualificationVerdict.BLOCKED
        assert "stale_dependency" in reevaluated["evaluation"]["blockers"]
        assert reevaluated["qualification_receipt"] is None
        binding = qualification.refresh_receipt_binding(
            context, original_receipts[claim["id"]]["id"]
        )
        assert binding["state"] == CurrentUseState.STALE
        assert "dependency_binding_changed" in binding["stale_reason"]
    assert (
        repository.get_current_use_binding(context.workspace_id, citation_only["id"], PROFILE_ID)[
            "state"
        ]
        == CurrentUseState.CURRENT
    )
    qualified_ids = {
        item["id"] for item in qualification.qualified_search(context, "conclusion", PROFILE_ID)
    }
    assert dependent["id"] not in qualified_ids
    assert transitive["id"] not in qualified_ids
    assert citation_only["id"] in qualified_ids
    for claim in (source, dependent, transitive, citation_only):
        receipt = original_receipts[claim["id"]]
        historical = qualification.get_receipt(context, receipt["id"])
        assert historical["receipt_hash"] == receipt["receipt_hash"]
        assert historical["verdict"] == QualificationVerdict.ADMITTED

    revised = _register(
        qualification,
        context,
        "WEIL-R2",
        "The construction proves a narrower claim under an added hypothesis.",
        parent=source["id"],
    )
    assert revised["parent_revision_id"] == source["id"]
    assert revised["semantic_hash"] != source["semantic_hash"]
    before_repair = qualification.evaluate(context, revised["id"], PROFILE_ID)
    assert before_repair["evaluation"]["verdict"] == QualificationVerdict.UNRESOLVED
    assert before_repair["qualification_receipt"] is None

    repaired_receipt, _attempt = _qualify(
        artifacts, research, qualification, context, revised, admit=False
    )
    assert repaired_receipt["id"] != original_receipts[source["id"]]["id"]
    assert repaired_receipt["claim_revision_id"] == revised["id"]
    assert repaired_receipt["claim_semantic_hash"] == revised["semantic_hash"]
    assert (
        repository.get_current_use_binding(context.workspace_id, revised["id"], PROFILE_ID) is None
    )
    assert qualification.qualified_search(context, "narrower", PROFILE_ID) == []
    qualification.admit_knowledge(
        context,
        KnowledgeAdmissionDraft(
            qualification_receipt_id=repaired_receipt["id"],
            admission_policy_id="knowledge.default.v1",
            rationale="Synthetic fixture: explicitly admit the repaired revision.",
        ),
    )
    assert (
        repository.get_current_use_binding(context.workspace_id, source["id"], PROFILE_ID)["state"]
        == CurrentUseState.STALE
    )
    assert (
        repository.get_current_use_binding(context.workspace_id, dependent["id"], PROFILE_ID)[
            "state"
        ]
        == CurrentUseState.STALE
    )


def test_failed_review_proposal_cannot_directly_revoke_old_receipt(tmp_path):
    repository, artifacts, research, qualification, context = _services(tmp_path)
    source = _register(qualification, context, "WEIL-AUDIT-R1", "The original construction works.")
    original_receipt, original_attempt = _qualify(
        artifacts, research, qualification, context, source
    )
    failure = artifacts.create_artifact(
        context,
        ArtifactDraft(
            name="sign-error-audit.json",
            kind=ArtifactKind.JSON,
            media_type="application/json",
            content_text=json.dumps(
                {
                    "invalidates_attempt_id": original_attempt["id"],
                    "finding": "A sign error invalidates the relied-upon construction.",
                }
            ),
            metadata={"role": "proof_failure", "synthetic_fixture": True},
        ),
    )
    research.record_verification_attempt(
        context,
        source["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.REVIEW,
            outcome=VerificationOutcome.FAILED,
            validation_modality=ValidationModality.EXPERT_REVIEW,
            method="Audit the sign in the stabilization-trace cancellation.",
            scope="The exact original construction and proof attempt.",
            input_digest=source["semantic_hash"],
            output_digest=failure["content_hash"],
            artifact_ids=(failure["id"],),
            metadata={"invalidates_attempt_id": original_attempt["id"]},
        ),
    )
    # A FAILED attempt is evidence, not an authorized withdrawal decision.
    # The missing next layer is a verified invalidation event that names the
    # exact proof/receipt and may transition current use before re-evaluation.
    assert (
        repository.get_current_use_binding(context.workspace_id, source["id"], PROFILE_ID)["state"]
        == CurrentUseState.CURRENT
    )
    assert qualification.get_receipt(context, original_receipt["id"])["verdict"] == (
        QualificationVerdict.ADMITTED
    )


def _verified_notice(repository, artifacts, context):
    admin = RequestContext(
        "invalidation-admin",
        context.workspace_id,
        "workspace-admin",
        scopes=frozenset({"qualification:invalidate"}),
    )

    def fetch(url):
        assert url == f"https://raw.githubusercontent.com/openai/math/{'a' * 40}/history.md"
        return b"# History\n## Withdrawals\nA sign error invalidated the construction.\n"

    service = InvalidationService(
        lambda: repository, artifact_service=artifacts, source_fetcher=fetch
    )
    verified = service.verify_notice(admin, NoticeVerificationDraft(source_commit="a" * 40))
    return service, admin, verified


def test_verified_withdrawal_decision_is_exact_and_does_not_erase_history(tmp_path):
    repository, artifacts, research, qualification, context = _services(tmp_path)
    old = _register(qualification, context, "WEIL-OLD", "Original broad statement.")
    child = _register(
        qualification,
        context,
        "K3-CHILD",
        "Uses the original construction.",
        dependencies=(old["id"],),
    )
    repaired = _register(
        qualification,
        context,
        "WEIL-NEW",
        "A narrower repaired statement.",
        parent=old["id"],
    )
    old_receipt, _ = _qualify(artifacts, research, qualification, context, old)
    child_receipt, _ = _qualify(artifacts, research, qualification, context, child)
    new_receipt, _ = _qualify(artifacts, research, qualification, context, repaired)
    qualification.create_authorization(
        context,
        "publisher-a",
        AuthorizationGrantDraft(
            qualification_receipt_id=old_receipt["id"],
            action="publish",
            target="knowledge/math",
            scope={"claim_revision_id": old["id"]},
        ),
    )
    service, admin, verified = _verified_notice(repository, artifacts, context)
    old_binding = repository.get_current_use_binding(context.workspace_id, old["id"], PROFILE_ID)
    assert old_binding["state"] == CurrentUseState.CURRENT
    draft = InvalidationDecisionDraft(
        notice_verification_id=verified["id"],
        target_claim_revision_id=old["id"],
        target_claim_semantic_hash=old["semantic_hash"],
        target_scope=InvalidationScope.CLAIM_REVISION,
        reason_code=InvalidationReason.WITHDRAWN_UPSTREAM_CONSTRUCTION,
        rationale="The pinned notice withdraws the original construction.",
    )
    with pytest.raises(InvalidEvidenceError, match="administrator"):
        service.decide(context, draft)
    with pytest.raises(InvalidEvidenceError, match="semantic hash"):
        service.decide(
            admin,
            InvalidationDecisionDraft(
                notice_verification_id=verified["id"],
                target_claim_revision_id=old["id"],
                target_claim_semantic_hash=repaired["semantic_hash"],
                target_scope=InvalidationScope.CLAIM_REVISION,
                reason_code=InvalidationReason.WITHDRAWN_UPSTREAM_CONSTRUCTION,
                rationale="Wrong-target hostile fixture.",
            ),
        )
    with pytest.raises(InvalidEvidenceError, match="another revision"):
        service.decide(
            admin,
            InvalidationDecisionDraft(
                notice_verification_id=verified["id"],
                target_claim_revision_id=old["id"],
                target_claim_semantic_hash=old["semantic_hash"],
                target_scope=InvalidationScope.RECEIPT,
                reason_code=InvalidationReason.RECEIPT_DEFECT,
                rationale="The repaired receipt belongs to another revision.",
                target_receipt_id=new_receipt["id"],
            ),
        )
    decision = service.decide(admin, draft)
    assert decision["affected_binding_ids"] == [old_binding["id"]]
    assert decision["evidence_artifact_ids"] == [verified["source_artifact_id"]]
    assert (
        service.list_decisions(context, old["id"])[0]["decision_hash"] == decision["decision_hash"]
    )
    assert (
        repository.get_current_use_binding(context.workspace_id, old["id"], PROFILE_ID)["state"]
        == CurrentUseState.REVOKED
    )
    assert (
        qualification.refresh_receipt_binding(context, child_receipt["id"])["state"]
        == CurrentUseState.STALE
    )
    assert (
        repository.get_current_use_binding(context.workspace_id, repaired["id"], PROFILE_ID)[
            "state"
        ]
        == CurrentUseState.CURRENT
    )
    assert qualification.get_receipt(context, old_receipt["id"])["verdict"] == (
        QualificationVerdict.ADMITTED
    )
    assert (
        qualification.evaluate(context, old["id"], PROFILE_ID)["evaluation"]["verdict"]
        == QualificationVerdict.BLOCKED
    )
    with pytest.raises(InvalidEvidenceError, match="stale qualification evidence"):
        qualification.admit_knowledge(
            context,
            KnowledgeAdmissionDraft(
                qualification_receipt_id=old_receipt["id"],
                admission_policy_id="knowledge.default.v1",
                rationale="A withdrawn receipt cannot be re-admitted.",
            ),
        )
    with pytest.raises(InvalidEvidenceError, match="current qualification binding"):
        qualification.create_authorization(
            context,
            "publisher-a",
            AuthorizationGrantDraft(
                qualification_receipt_id=old_receipt["id"],
                action="publish",
                target="knowledge/math",
                scope={"claim_revision_id": old["id"]},
            ),
        )
    with pytest.raises(sqlite3.IntegrityError, match="invalidated qualification"):
        repository.upsert_current_use_binding(
            context.workspace_id, {**old_binding, "state": "current"}
        )
    with (
        repository._connect() as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute(
            "UPDATE invalidation_decisions SET reason_code = ? WHERE id = ?",
            ("CHANGED", decision["id"]),
        )


def test_invalidating_attempt_a_does_not_revoke_repaired_attempt_b(tmp_path):
    repository, artifacts, research, qualification, context = _services(tmp_path)
    claim = _register(qualification, context, "TWO-PROOFS", "Two proof attempts.")
    receipt_a, attempt_a = _qualify(artifacts, research, qualification, context, claim)
    service, admin, verified = _verified_notice(repository, artifacts, context)
    decision = service.decide(
        admin,
        InvalidationDecisionDraft(
            notice_verification_id=verified["id"],
            target_claim_revision_id=claim["id"],
            target_claim_semantic_hash=claim["semantic_hash"],
            target_scope=InvalidationScope.PROOF_ATTEMPT,
            reason_code=InvalidationReason.PROOF_INVALIDATED,
            rationale="Attempt A has an accepted proof defect.",
            target_receipt_id=receipt_a["id"],
            target_attempt_id=attempt_a["id"],
        ),
    )
    assert decision["affected_binding_ids"]
    assert (
        repository.get_current_use_binding(context.workspace_id, claim["id"], PROFILE_ID)["state"]
        == CurrentUseState.STALE
    )
    receipt_b, attempt_b = _qualify(artifacts, research, qualification, context, claim)
    assert receipt_b["id"] != receipt_a["id"]
    assert attempt_b["id"] != attempt_a["id"]
    with pytest.raises(InvalidEvidenceError, match="not selected"):
        service.decide(
            admin,
            InvalidationDecisionDraft(
                notice_verification_id=verified["id"],
                target_claim_revision_id=claim["id"],
                target_claim_semantic_hash=claim["semantic_hash"],
                target_scope=InvalidationScope.PROOF_ATTEMPT,
                reason_code=InvalidationReason.PROOF_INVALIDATED,
                rationale="Attempt A cannot be relabeled as receipt B's selected proof.",
                target_receipt_id=receipt_b["id"],
                target_attempt_id=attempt_a["id"],
            ),
        )
    assert (
        qualification.refresh_receipt_binding(context, receipt_b["id"])["state"]
        == CurrentUseState.CURRENT
    )
    with pytest.raises(InvalidEvidenceError, match="stale qualification evidence"):
        qualification.admit_knowledge(
            context,
            KnowledgeAdmissionDraft(
                qualification_receipt_id=receipt_a["id"],
                admission_policy_id="knowledge.default.v1",
                rationale="Attempt A's receipt remains invalidated.",
            ),
        )


def test_tampered_notice_artifact_cannot_back_a_decision(tmp_path):
    repository, artifacts, _research, qualification, context = _services(tmp_path)
    claim = _register(qualification, context, "TAMPER-CASE", "A candidate statement.")
    service, admin, verified = _verified_notice(repository, artifacts, context)
    with repository._connect() as connection:
        connection.execute(
            "UPDATE artifacts SET content_text = ? WHERE tenant_id = ? AND id = ?",
            ("Forged notice text", context.workspace_id, verified["source_artifact_id"]),
        )
    with pytest.raises(InvalidEvidenceError, match="missing or changed"):
        service.decide(
            admin,
            InvalidationDecisionDraft(
                notice_verification_id=verified["id"],
                target_claim_revision_id=claim["id"],
                target_claim_semantic_hash=claim["semantic_hash"],
                target_scope=InvalidationScope.CLAIM_REVISION,
                reason_code=InvalidationReason.WITHDRAWN_UPSTREAM_CONSTRUCTION,
                rationale="Forged content must fail closed.",
            ),
        )
    assert service.list_decisions(context, claim["id"]) == []


def test_http_invalidation_requires_admin_route_and_exposes_decision(tmp_path, monkeypatch):
    repository, artifacts, _research, qualification, context = _services(tmp_path)
    claim = _register(qualification, context, "HTTP-WITHDRAWAL", "A candidate statement.")
    service, _admin, _verified = _verified_notice(repository, artifacts, context)
    app = FastAPI()
    app.include_router(
        research_api.create_research_router(
            assurance_bundle_service=None,
            decision_lab_service=None,
            evidence_service=None,
            kernel_verification_service=None,
            invalidation_service=service,
            qualification_service=qualification,
            research_registry_service=None,
            verification_runner=None,
            request_context_factory=lambda request, tenant: RequestContext(
                "http-invalidation", tenant.id, "workspace-admin"
            ),
        )
    )
    app.dependency_overrides[current_admin_user] = lambda: {
        "id": "workspace-admin",
        "tenant_id": context.workspace_id,
        "role": "admin",
    }
    app.dependency_overrides[current_tenant] = lambda: Tenant(context.workspace_id, "Workspace A")
    monkeypatch.setattr(research_api, "get_repository", lambda: repository)
    with TestClient(app) as client:
        verified = client.post(
            "/api/qualification/invalidation-notices/verify",
            json={"source_commit": "a" * 40},
        )
        assert verified.status_code == 201
        assert (
            client.get(
                f"/api/qualification/invalidation-notices/{verified.json()['id']}"
            ).status_code
            == 200
        )
        decided = client.post(
            "/api/qualification/invalidation-decisions",
            json={
                "notice_verification_id": verified.json()["id"],
                "target_claim_revision_id": claim["id"],
                "target_claim_semantic_hash": claim["semantic_hash"],
                "target_scope": "claim_revision",
                "reason_code": "WITHDRAWN_UPSTREAM_CONSTRUCTION",
                "rationale": "HTTP contract test of a pinned withdrawal notice.",
            },
        )
        assert decided.status_code == 201
        assert (
            client.get(f"/api/qualification/claims/{claim['id']}/invalidation-decisions").json()[0][
                "id"
            ]
            == decided.json()["id"]
        )
