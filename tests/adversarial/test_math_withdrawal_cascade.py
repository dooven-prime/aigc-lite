"""Synthetic withdrawal cascade, not a validation of the OpenAI mathematics.

Model the October 2026 topology: one failed construction, two hard dependents,
and a separate paper whose citation changes without a proof dependency. Fake
kernel certificates exercise qualification plumbing; they are not Lean runs.
"""

from __future__ import annotations

import io
import json
import secrets
import sqlite3
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import auth, tenancy
from app.api import research as research_api
from app.auth import token_hash
from app.core.artifacts import ArtifactDraft, ArtifactKind
from app.core.authority import ADMIN_AUTHORITY_SCOPES, AuthorityContext
from app.core.contracts import RequestContext, ToolHints, ToolRisk, ToolSource, ToolSpec
from app.core.errors import AuthorityDeniedError, InvalidEvidenceError
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
from app.services.assurance import AssuranceBundleService
from app.services.authorization import ToolAuthorizationGate
from app.services.evidence import EvidenceService
from app.services.invalidation import InvalidationService
from app.services.qualification import QualificationService
from app.services.research_registry import ResearchRegistryService


def provision_admin(repository, context: RequestContext) -> tuple[RequestContext, AuthorityContext]:
    if repository.get_tenant(context.workspace_id) is None:
        repository.create_tenant("Withdrawal test workspace", context.workspace_id)
    user = repository.create_user(
        context.workspace_id, f"withdrawal-{uuid4()}@example.test",
        "Withdrawal test admin", "unused-test-password-hash", role="admin",
    )
    session_hash = uuid4().hex + uuid4().hex
    repository.save_auth_session(
        session_hash, user["id"], context.workspace_id,
        (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        expected_auth_version=0,
    )
    return replace(context, principal_id=user["id"]), AuthorityContext(
        context.workspace_id, user["id"], session_hash, ADMIN_AUTHORITY_SCOPES
    )


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
    context, authority = provision_admin(repository, context)
    return repository, artifacts, research, qualification, context, authority


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


def _qualify(artifacts, research, qualification, context, authority, claim, *, admit=True):
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
            authority=authority,
        )
        assert admission["current_use_binding"]["state"] == CurrentUseState.CURRENT
    return receipt, kernel_attempt


def test_withdrawn_construction_taints_hard_dependents_but_not_citation_only(tmp_path):
    repository, artifacts, research, qualification, context, authority = _services(tmp_path)
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
        receipt, _attempt = _qualify(artifacts, research, qualification, context, authority, claim)
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
        artifacts, research, qualification, context, authority, revised, admit=False
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
        authority=authority,
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
    repository, artifacts, research, qualification, context, authority = _services(tmp_path)
    source = _register(qualification, context, "WEIL-AUDIT-R1", "The original construction works.")
    original_receipt, original_attempt = _qualify(
        artifacts, research, qualification, context, authority, source
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


def _verified_notice(repository, artifacts, context, authority):
    admin = context

    def fetch(url):
        assert url == f"https://raw.githubusercontent.com/openai/math/{'a' * 40}/history.md"
        return b"# History\n## Withdrawals\nA sign error invalidated the construction.\n"

    service = InvalidationService(
        lambda: repository, artifact_service=artifacts, source_fetcher=fetch
    )
    verified = service.verify_notice(
        admin, NoticeVerificationDraft(source_commit="a" * 40), authority=authority
    )
    return service, admin, verified


def test_verified_withdrawal_decision_is_exact_and_does_not_erase_history(tmp_path):
    repository, artifacts, research, qualification, context, authority = _services(tmp_path)
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
    old_receipt, _ = _qualify(artifacts, research, qualification, context, authority, old)
    child_receipt, _ = _qualify(artifacts, research, qualification, context, authority, child)
    new_receipt, _ = _qualify(artifacts, research, qualification, context, authority, repaired)
    qualification.create_authorization(
        context,
        "publisher-a",
        AuthorizationGrantDraft(
            qualification_receipt_id=old_receipt["id"],
            action="publish",
            target="knowledge/math",
            scope={"claim_revision_id": old["id"]},
        ),
        authority=authority,
    )
    service, admin, verified = _verified_notice(repository, artifacts, context, authority)
    old_binding = repository.get_current_use_binding(context.workspace_id, old["id"], PROFILE_ID)
    child_binding = repository.get_current_use_binding(context.workspace_id, child["id"], PROFILE_ID)
    assert old_binding["state"] == CurrentUseState.CURRENT
    draft = InvalidationDecisionDraft(
        notice_verification_id=verified["id"],
        target_claim_revision_id=old["id"],
        target_claim_semantic_hash=old["semantic_hash"],
        target_scope=InvalidationScope.CLAIM_REVISION,
        reason_code=InvalidationReason.WITHDRAWN_UPSTREAM_CONSTRUCTION,
        rationale="The pinned notice withdraws the original construction.",
    )
    with pytest.raises(AuthorityDeniedError, match="administrator"):
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
            authority=authority,
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
            authority=authority,
        )
    decision = service.decide(admin, draft, authority=authority)
    assert decision["affected_binding_ids"] == [old_binding["id"], child_binding["id"]]
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
            authority=authority,
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
            authority=authority,
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
    repository, artifacts, research, qualification, context, authority = _services(tmp_path)
    claim = _register(qualification, context, "TWO-PROOFS", "Two proof attempts.")
    receipt_a, attempt_a = _qualify(
        artifacts, research, qualification, context, authority, claim
    )
    service, admin, verified = _verified_notice(repository, artifacts, context, authority)
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
        authority=authority,
    )
    assert decision["affected_binding_ids"]
    assert (
        repository.get_current_use_binding(context.workspace_id, claim["id"], PROFILE_ID)["state"]
        == CurrentUseState.STALE
    )
    receipt_b, attempt_b = _qualify(
        artifacts, research, qualification, context, authority, claim
    )
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
            authority=authority,
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
            authority=authority,
        )


def test_shared_attempt_invalidation_reaches_current_receipt_and_authority(tmp_path):
    repository, artifacts, research, qualification, context, authority = _services(tmp_path)
    claim = _register(qualification, context, "SHARED-PROOF", "One proof, two receipts.")
    receipt_a, attempt_a = _qualify(
        artifacts, research, qualification, context, authority, claim
    )
    result_b = qualification.evaluate(context, claim["id"], PROFILE_ID)
    receipt_b = result_b["qualification_receipt"]
    assert receipt_b is not None and receipt_b["id"] != receipt_a["id"]
    binding_b = qualification.admit_knowledge(
        context,
        KnowledgeAdmissionDraft(
            qualification_receipt_id=receipt_b["id"],
            admission_policy_id="knowledge.default.v1",
            rationale="The second receipt selects the same synthetic proof.",
        ),
        authority=authority,
    )["current_use_binding"]
    with repository._connect() as db:
        selections = db.execute(
            "SELECT qualification_receipt_id FROM qualification_receipt_attempt_selections "
            "WHERE tenant_id = ? AND attempt_id = ? ORDER BY qualification_receipt_id",
            (context.workspace_id, attempt_a["id"]),
        ).fetchall()
    assert {row["qualification_receipt_id"] for row in selections} == {
        receipt_a["id"], receipt_b["id"]
    }
    dependent = _register(
        qualification, context, "SHARED-DEPENDENT", "Uses the shared proof.",
        dependencies=(claim["id"],),
    )
    dependent_receipt, _ = _qualify(
        artifacts, research, qualification, context, authority, dependent
    )
    dependent_binding = repository.get_current_use_binding(
        context.workspace_id, dependent["id"], PROFILE_ID
    )
    dependent_grant = qualification.create_authorization(
        context,
        context.principal_id,
        AuthorizationGrantDraft(
            qualification_receipt_id=dependent_receipt["id"],
            action="publish", target="knowledge/dependent", scope={},
        ),
        authority=authority,
    )
    grant = qualification.create_authorization(
        context,
        context.principal_id,
        AuthorizationGrantDraft(
            qualification_receipt_id=receipt_b["id"],
            action="robot_navigate_to",
            target="provider:robot",
            scope={},
        ),
        authority=authority,
    )
    old_hashes = {
        receipt_id: repository.get_qualification_receipt(context.workspace_id, receipt_id)[
            "receipt_hash"
        ]
        for receipt_id in (receipt_a["id"], receipt_b["id"])
    }
    service, admin, verified = _verified_notice(repository, artifacts, context, authority)
    decision = service.decide(
        admin,
        InvalidationDecisionDraft(
            notice_verification_id=verified["id"],
            target_claim_revision_id=claim["id"],
            target_claim_semantic_hash=claim["semantic_hash"],
            target_scope=InvalidationScope.PROOF_ATTEMPT,
            reason_code=InvalidationReason.PROOF_INVALIDATED,
            rationale="The selected proof attempt is defective in both receipts.",
            target_receipt_id=receipt_a["id"],
            target_attempt_id=attempt_a["id"],
        ),
        authority=authority,
    )
    assert set(decision["affected_binding_ids"]) == {
        binding_b["id"], dependent_binding["id"]
    }
    assert qualification.refresh_receipt_binding(context, receipt_b["id"])["state"] == (
        CurrentUseState.STALE
    )
    assert qualification.refresh_receipt_binding(context, dependent_receipt["id"])[
        "state"
    ] == CurrentUseState.STALE
    assert qualification.qualified_search(context, "SHARED-PROOF", PROFILE_ID) == []
    with pytest.raises(InvalidEvidenceError, match="current qualification binding"):
        qualification.create_authorization(
            context,
            context.principal_id,
            AuthorizationGrantDraft(
                qualification_receipt_id=receipt_b["id"],
                action="robot_navigate_to",
                target="provider:robot",
                scope={},
            ),
            authority=authority,
        )
    assert repository.consume_authorization_grant(
        context.workspace_id, context.principal_id, "robot_navigate_to",
        "provider:robot", datetime.now(UTC).isoformat(), grant_id=grant["id"],
    ) is None
    assert repository.consume_authorization_grant(
        context.workspace_id, context.principal_id, "publish",
        "knowledge/dependent", datetime.now(UTC).isoformat(),
        grant_id=dependent_grant["id"],
    ) is None
    spec = ToolSpec(
        name="robot__robot_navigate_to", native_name="robot_navigate_to",
        description="Synthetic motion action.", input_schema={"type": "object"},
        source=ToolSource.MCP, provider_id="robot", risk=ToolRisk.HIGH,
        required_scopes=frozenset({"robot:motion"}), hints=ToolHints(read_only=False),
        extensions={"capability": {"execution_class": "physical"}},
    )
    assert ToolAuthorizationGate(lambda: repository).authorize_and_consume(
        context, spec, {}
    ) is None
    assert next(
        item for item in repository.list_authorization_grants(context.workspace_id)
        if item["id"] == grant["id"]
    )["calls_used"] == 0
    with (
        repository._connect() as db,
        pytest.raises(sqlite3.IntegrityError, match="invalidated qualification"),
    ):
        db.execute(
            "UPDATE current_use_bindings SET state = 'current' WHERE id = ?",
            (binding_b["id"],),
        )
    bundle, _ = AssuranceBundleService(lambda: repository).export_zip(
        context, claim["research_case_id"]
    )
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        qualification_doc = json.loads(archive.read("qualification.json"))
    assert any(
        item["qualification_receipt_id"] == receipt_b["id"]
        and item["state"] == CurrentUseState.STALE
        for item in qualification_doc["current_use_bindings"]
    )
    for receipt_id, receipt_hash in old_hashes.items():
        historical = repository.get_qualification_receipt(context.workspace_id, receipt_id)
        assert historical["receipt_hash"] == receipt_hash
        assert historical["verdict"] == QualificationVerdict.ADMITTED

    receipt_c, attempt_c = _qualify(
        artifacts, research, qualification, context, authority, claim
    )
    assert attempt_c["id"] != attempt_a["id"]
    assert qualification.refresh_receipt_binding(context, receipt_c["id"])["state"] == (
        CurrentUseState.CURRENT
    )


def test_reused_certificate_does_not_make_distinct_attempts_interchangeable(tmp_path):
    repository, artifacts, research, qualification, context, authority = _services(tmp_path)
    claim = _register(qualification, context, "REUSED-CERT", "One certificate, two runs.")
    receipt_a, attempt_a = _qualify(
        artifacts, research, qualification, context, authority, claim
    )
    kernel_context = RequestContext(
        f"second-kernel-{claim['id']}", context.workspace_id,
        "system:verifier:lean-kernel",
    )
    attempt_b = research.record_verification_attempt(
        kernel_context,
        claim["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.CALCULATION,
            outcome=VerificationOutcome.PASSED,
            validation_modality=ValidationModality.KERNEL_CHECK,
            method="A second synthetic check references the same certificate bytes.",
            scope="Exact frozen fixture revision.",
            input_digest=claim["semantic_hash"],
            output_digest=attempt_a["output_digest"],
            artifact_ids=tuple(attempt_a["artifact_ids"]),
        ),
    )
    receipt_b = qualification.evaluate(context, claim["id"], PROFILE_ID)[
        "qualification_receipt"
    ]
    assert receipt_b is not None
    assert next(
        item for item in receipt_b["criteria"] if item["code"] == "kernel_check"
    )["evidence_node_ids"] == [attempt_b["id"]]
    qualification.admit_knowledge(
        context,
        KnowledgeAdmissionDraft(
            qualification_receipt_id=receipt_b["id"],
            admission_policy_id="knowledge.default.v1",
            rationale="Select the second check, not the first.",
        ),
        authority=authority,
    )
    service, admin, verified = _verified_notice(repository, artifacts, context, authority)
    decision = service.decide(
        admin,
        InvalidationDecisionDraft(
            notice_verification_id=verified["id"],
            target_claim_revision_id=claim["id"],
            target_claim_semantic_hash=claim["semantic_hash"],
            target_scope=InvalidationScope.PROOF_ATTEMPT,
            reason_code=InvalidationReason.PROOF_INVALIDATED,
            rationale="Only the first run's execution is defective.",
            target_receipt_id=receipt_a["id"],
            target_attempt_id=attempt_a["id"],
        ),
        authority=authority,
    )
    assert decision["affected_binding_ids"] == []
    assert qualification.refresh_receipt_binding(context, receipt_b["id"])["state"] == (
        CurrentUseState.CURRENT
    )


def test_proof_selection_upgrade_backfills_historical_receipts(tmp_path):
    repository, artifacts, research, qualification, context, authority = _services(tmp_path)
    claim = _register(qualification, context, "BACKFILL-PROOF", "Frozen proof selection.")
    receipt_a, attempt = _qualify(
        artifacts, research, qualification, context, authority, claim
    )
    receipt_b = qualification.evaluate(context, claim["id"], PROFILE_ID)[
        "qualification_receipt"
    ]
    hashes = {
        receipt_id: repository.get_qualification_receipt(context.workspace_id, receipt_id)[
            "receipt_hash"
        ]
        for receipt_id in (receipt_a["id"], receipt_b["id"])
    }
    binding_b = qualification.admit_knowledge(
        context,
        KnowledgeAdmissionDraft(
            qualification_receipt_id=receipt_b["id"],
            admission_policy_id="knowledge.default.v1",
            rationale="Historical current use of the second receipt.",
        ),
        authority=authority,
    )["current_use_binding"]
    dependent = _register(
        qualification, context, "BACKFILL-DEPENDENT", "Depends on the frozen proof.",
        dependencies=(claim["id"],),
    )
    _qualify(artifacts, research, qualification, context, authority, dependent)
    dependent_binding = repository.get_current_use_binding(
        context.workspace_id, dependent["id"], PROFILE_ID
    )
    app_root = Path(__file__).resolve().parents[2] / "app"
    config = Config(str(app_root / "alembic.ini"))
    config.set_main_option("script_location", str(app_root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{repository.database_path.as_posix()}")
    command.downgrade(config, "0028_native_goal_binding")
    decision_id = str(uuid4())
    # Simulate the old release's legitimate decision: it named receipt A and
    # missed the current receipt B, although both selected this proof attempt.
    with repository._connect() as db:
        db.execute(
            "INSERT INTO invalidation_decisions(id, tenant_id, notice_verification_id, "
            "target_claim_revision_id, target_claim_semantic_hash, target_receipt_id, "
            "target_attempt_id, target_scope, reason_code, evidence_artifact_ids, "
            "evidence_artifact_hashes, policy_id, policy_hash, rationale, decided_by, "
            "effect_state, affected_binding_ids, decision_hash, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'proof_attempt', 'PROOF_INVALIDATED', "
            "'[]', '{}', 'invalidation.pinned-source-admin.v1', ?, ?, ?, "
            "'stale', '[]', ?, ?)",
            (
                decision_id, context.workspace_id, str(uuid4()), claim["id"],
                claim["semantic_hash"], receipt_a["id"], attempt["id"],
                "d" * 64, "Historical shared-attempt defect", context.principal_id,
                "e" * 64, datetime.now(UTC).isoformat(),
            ),
        )
    assert repository.get_current_use_binding(
        context.workspace_id, claim["id"], PROFILE_ID
    )["state"] == CurrentUseState.CURRENT
    command.upgrade(config, "head")
    with repository._connect() as db:
        rows = db.execute(
            "SELECT qualification_receipt_id FROM qualification_receipt_attempt_selections "
            "WHERE tenant_id = ? AND attempt_id = ?",
            (context.workspace_id, attempt["id"]),
        ).fetchall()
    assert {row["qualification_receipt_id"] for row in rows} == set(hashes)
    reconciled = repository.get_current_use_binding(
        context.workspace_id, claim["id"], PROFILE_ID
    )
    assert reconciled["id"] == binding_b["id"]
    assert reconciled["state"] == CurrentUseState.STALE
    assert reconciled["stale_reason"] == f"invalidation_decision:{decision_id}"
    migrated_dependent = repository.get_current_use_binding(
        context.workspace_id, dependent["id"], PROFILE_ID
    )
    assert migrated_dependent["id"] == dependent_binding["id"]
    assert migrated_dependent["state"] == CurrentUseState.STALE
    assert {
        receipt_id: repository.get_qualification_receipt(context.workspace_id, receipt_id)[
            "receipt_hash"
        ]
        for receipt_id in hashes
    } == hashes


def test_grant_creation_rechecks_after_interleaved_attempt_invalidation(
    tmp_path, monkeypatch
):
    repository, artifacts, research, qualification, context, authority = _services(tmp_path)
    claim = _register(qualification, context, "ISSUE-RACE", "Shared proof grant race.")
    receipt_a, attempt = _qualify(
        artifacts, research, qualification, context, authority, claim
    )
    receipt_b = qualification.evaluate(context, claim["id"], PROFILE_ID)[
        "qualification_receipt"
    ]
    qualification.admit_knowledge(
        context,
        KnowledgeAdmissionDraft(
            qualification_receipt_id=receipt_b["id"],
            admission_policy_id="knowledge.default.v1",
            rationale="Select the second receipt before invalidation.",
        ),
        authority=authority,
    )
    service, admin, verified = _verified_notice(repository, artifacts, context, authority)
    original_create = repository.create_authorization_grant

    def invalidate_then_create(*args, **kwargs):
        service.decide(
            admin,
            InvalidationDecisionDraft(
                notice_verification_id=verified["id"],
                target_claim_revision_id=claim["id"],
                target_claim_semantic_hash=claim["semantic_hash"],
                target_scope=InvalidationScope.PROOF_ATTEMPT,
                reason_code=InvalidationReason.PROOF_INVALIDATED,
                rationale="The decision commits after service refresh, before Grant write.",
                target_receipt_id=receipt_a["id"],
                target_attempt_id=attempt["id"],
            ),
            authority=authority,
        )
        return original_create(*args, **kwargs)

    monkeypatch.setattr(repository, "create_authorization_grant", invalidate_then_create)
    with pytest.raises(InvalidEvidenceError, match="non-invalidated binding"):
        qualification.create_authorization(
            context,
            context.principal_id,
            AuthorizationGrantDraft(
                qualification_receipt_id=receipt_b["id"],
                action="robot_navigate_to", target="provider:robot", scope={},
            ),
            authority=authority,
        )
    assert repository.list_authorization_grants(context.workspace_id) == []


@pytest.mark.parametrize("grant_kind", ["source", "dependent"])
def test_sqlite_invalidation_commit_blocks_concurrent_grant_consume(
    tmp_path, grant_kind
):
    repository, artifacts, research, qualification, context, authority = _services(tmp_path)
    claim = _register(qualification, context, "SQLITE-RACE", "Shared attempt race.")
    receipt_a, attempt = _qualify(
        artifacts, research, qualification, context, authority, claim
    )
    receipt_b = qualification.evaluate(context, claim["id"], PROFILE_ID)[
        "qualification_receipt"
    ]
    qualification.admit_knowledge(
        context,
        KnowledgeAdmissionDraft(
            qualification_receipt_id=receipt_b["id"],
            admission_policy_id="knowledge.default.v1",
            rationale="Second receipt is current before invalidation.",
        ),
        authority=authority,
    )
    grant = qualification.create_authorization(
        context,
        context.principal_id,
        AuthorizationGrantDraft(
            qualification_receipt_id=receipt_b["id"],
            action="robot_navigate_to", target="provider:robot", scope={},
        ),
        authority=authority,
    )
    dependent = _register(
        qualification, context, "SQLITE-RACE-DEPENDENT", "Depends on the proof.",
        dependencies=(claim["id"],),
    )
    dependent_receipt, _ = _qualify(
        artifacts, research, qualification, context, authority, dependent
    )
    dependent_grant = qualification.create_authorization(
        context,
        context.principal_id,
        AuthorizationGrantDraft(
            qualification_receipt_id=dependent_receipt["id"],
            action="publish", target="knowledge/dependent", scope={},
        ),
        authority=authority,
    )
    started = threading.Event()

    def consume():
        started.set()
        selected_grant = grant if grant_kind == "source" else dependent_grant
        action, target = (
            ("robot_navigate_to", "provider:robot")
            if grant_kind == "source" else ("publish", "knowledge/dependent")
        )
        return repository.consume_authorization_grant(
            context.workspace_id, context.principal_id, action,
            target, datetime.now(UTC).isoformat(), grant_id=selected_grant["id"],
        )

    decision_id = str(uuid4())
    with repository._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO invalidation_decisions(id, tenant_id, notice_verification_id, "
            "target_claim_revision_id, target_claim_semantic_hash, target_receipt_id, "
            "target_attempt_id, target_scope, reason_code, evidence_artifact_ids, "
            "evidence_artifact_hashes, policy_id, policy_hash, rationale, decided_by, "
            "effect_state, affected_binding_ids, decision_hash, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'proof_attempt', 'PROOF_INVALIDATED', "
            "'[]', '{}', 'invalidation.pinned-source-admin.v1', ?, ?, ?, "
            "'stale', '[]', ?, ?)",
            (
                decision_id, context.workspace_id, str(uuid4()), claim["id"],
                claim["semantic_hash"], receipt_a["id"], attempt["id"],
                "d" * 64, "Concurrent synthetic proof defect", context.principal_id,
                "e" * 64, datetime.now(UTC).isoformat(),
            ),
        )
        db.execute(
            "UPDATE current_use_bindings SET state = 'stale', stale_reason = ? "
            "WHERE tenant_id = ? AND claim_revision_id = ?",
            (f"invalidation_decision:{decision_id}", context.workspace_id, claim["id"]),
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(consume)
            assert started.wait(5)
            time.sleep(0.1)
            assert not pending.done(), "SQLite writer escaped the pending invalidation"
            db.commit()
            assert pending.result(timeout=10) is None
    selected_grant = grant if grant_kind == "source" else dependent_grant
    assert next(
        item for item in repository.list_authorization_grants(context.workspace_id)
        if item["id"] == selected_grant["id"]
    )["calls_used"] == 0


def test_tampered_notice_artifact_cannot_back_a_decision(tmp_path):
    repository, artifacts, _research, qualification, context, authority = _services(tmp_path)
    claim = _register(qualification, context, "TAMPER-CASE", "A candidate statement.")
    service, admin, verified = _verified_notice(repository, artifacts, context, authority)
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
            authority=authority,
        )
    assert service.list_decisions(context, claim["id"]) == []


def test_http_invalidation_requires_admin_route_and_exposes_decision(tmp_path, monkeypatch):
    repository, artifacts, _research, qualification, context, authority = _services(tmp_path)
    claim = _register(qualification, context, "HTTP-WITHDRAWAL", "A candidate statement.")
    service, _admin, _verified = _verified_notice(repository, artifacts, context, authority)
    token = secrets.token_urlsafe(32)
    repository.save_auth_session(
        token_hash(token),
        context.principal_id,
        context.workspace_id,
        (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        expected_auth_version=repository.get_user(context.principal_id)["auth_version"],
    )
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
                "http-invalidation",
                tenant.id,
                getattr(request.state, "user_id", None),
            ),
        )
    )
    monkeypatch.setattr(auth, "get_repository", lambda: repository)
    monkeypatch.setattr(tenancy, "get_repository", lambda: repository)
    monkeypatch.setattr(research_api, "get_repository", lambda: repository)
    with TestClient(app) as client:
        verified = client.post(
            "/api/qualification/invalidation-notices/verify",
            json={"source_commit": "a" * 40},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert verified.status_code == 201
        assert (
            client.get(
                f"/api/qualification/invalidation-notices/{verified.json()['id']}",
                headers={"Authorization": f"Bearer {token}"},
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
            headers={"Authorization": f"Bearer {token}"},
        )
        assert decided.status_code == 201
        assert (
            client.get(
                f"/api/qualification/claims/{claim['id']}/invalidation-decisions",
                headers={"Authorization": f"Bearer {token}"},
            ).json()[0][
                "id"
            ]
            == decided.json()["id"]
        )
    with repository._connect() as connection:
        audit_rows = connection.execute("SELECT metadata FROM audit_logs").fetchall()
    assert audit_rows
    assert all(token_hash(token) not in row["metadata"] for row in audit_rows)


@pytest.mark.parametrize("target_index", [0, 1])
def test_http_historical_possible_attempt_can_invalidate_binding(
    tmp_path, monkeypatch, target_index
):
    repository, artifacts, research, qualification, context, authority = _services(tmp_path)
    claim = _register(
        qualification, context, f"HTTP-HISTORICAL-{target_index}",
        "A historical shared-certificate theorem.",
    )
    modern_receipt, first = _qualify(
        artifacts, research, qualification, context, authority, claim, admit=False
    )
    kernel_context = RequestContext(
        f"historical-kernel-{claim['id']}", context.workspace_id,
        "system:verifier:lean-kernel",
    )

    def add_attempt(outcome):
        return research.record_verification_attempt(
            kernel_context,
            claim["id"],
            VerificationAttemptDraft(
                kind=VerificationKind.CALCULATION,
                outcome=outcome,
                validation_modality=ValidationModality.KERNEL_CHECK,
                method="Synthetic historical verifier replay.",
                scope="Exact frozen fixture revision.",
                input_digest=claim["semantic_hash"],
                output_digest=first["output_digest"],
                artifact_ids=tuple(first["artifact_ids"]),
            ),
        )

    second = add_attempt(VerificationOutcome.PASSED)
    outside = add_attempt(VerificationOutcome.FAILED)
    original = next(
        item for item in repository.list_qualification_evaluations(
            context.workspace_id, claim["id"], PROFILE_ID
        ) if item["id"] == modern_receipt["evaluation_id"]
    )
    closure = {
        **original["evidence_closure"],
        "nodes": [
            *original["evidence_closure"]["nodes"],
            *(
                {
                    "node_type": "verification_attempt",
                    "node_id": attempt["id"],
                    "payload": {
                        "validation_modality": attempt["validation_modality"],
                        "artifact_ids": attempt["artifact_ids"],
                        "outcome": attempt["outcome"],
                        "run_id": attempt["run_id"],
                        "invalidated_by": [],
                        "verifier_lineage": attempt["verifier_lineage"],
                    },
                }
                for attempt in (second, outside)
            ),
        ],
    }
    historical_criteria = [
        {
            **criterion,
            "evidence_node_ids": [first["id"], second["id"]],
        } if criterion["code"] == "kernel_check" else criterion
        for criterion in modern_receipt["criteria"]
    ]
    historical_evaluation = repository.create_qualification_evaluation(
        context.workspace_id,
        {
            **original,
            "id": str(uuid4()),
            "evidence_closure": closure,
            "evidence_closure_hash": "9" * 64,
            "criteria": historical_criteria,
        },
    )
    historical_receipt = repository.create_qualification_receipt(
        context.workspace_id,
        {
            **modern_receipt,
            "id": str(uuid4()),
            "evaluation_id": historical_evaluation["id"],
            "evidence_closure_hash": historical_evaluation["evidence_closure_hash"],
            "criteria": historical_criteria,
            "receipt_hash": uuid4().hex * 2,
        },
    )
    binding = repository.admit_knowledge(
        context.workspace_id,
        {
            "id": str(uuid4()),
            "contract_version": "knowledge.admission.v1",
            "qualification_receipt_id": historical_receipt["id"],
            "qualification_receipt_hash": historical_receipt["receipt_hash"],
            "claim_revision_id": claim["id"],
            "claim_semantic_hash": claim["semantic_hash"],
            "profile_id": PROFILE_ID,
            "profile_version": historical_receipt["profile_version"],
            "use_scope": "knowledge",
            "admission_policy_id": "knowledge.default.v1",
            "admission_policy_version": 1,
            "admission_policy_hash": "f" * 64,
            "approved_by": context.principal_id,
            "rationale": "Select the historical ambiguous receipt.",
            "receipt_hash": uuid4().hex * 2,
            "issued_at": datetime.now(UTC).isoformat(),
        },
        context=context,
        authority=authority,
    )["current_use_binding"]
    selected = {first["id"], second["id"]}
    with repository._connect() as db:
        assert {
            row["attempt_id"]
            for row in db.execute(
                "SELECT attempt_id FROM qualification_receipt_attempt_selections "
                "WHERE tenant_id = ? AND qualification_receipt_id = ?",
                (context.workspace_id, historical_receipt["id"]),
            )
        } == selected

    service, _admin, verified = _verified_notice(
        repository, artifacts, context, authority
    )
    token = secrets.token_urlsafe(32)
    repository.save_auth_session(
        token_hash(token), context.principal_id, context.workspace_id,
        (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        expected_auth_version=repository.get_user(context.principal_id)["auth_version"],
    )
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
                "http-historical-invalidation", tenant.id,
                getattr(request.state, "user_id", None),
            ),
        )
    )

    @app.exception_handler(InvalidEvidenceError)
    async def invalid_evidence_response(_request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    monkeypatch.setattr(auth, "get_repository", lambda: repository)
    monkeypatch.setattr(tenancy, "get_repository", lambda: repository)
    monkeypatch.setattr(research_api, "get_repository", lambda: repository)
    request = {
        "notice_verification_id": verified["id"],
        "target_claim_revision_id": claim["id"],
        "target_claim_semantic_hash": claim["semantic_hash"],
        "target_scope": "proof_attempt",
        "reason_code": "PROOF_INVALIDATED",
        "target_receipt_id": historical_receipt["id"],
        "rationale": "The pinned notice invalidates this possible proof attempt.",
    }
    with TestClient(app) as client:
        denied = client.post(
            "/api/qualification/invalidation-decisions",
            json={**request, "target_attempt_id": outside["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert denied.status_code == 422
        assert "not selected" in denied.json()["detail"]
        assert repository.get_current_use_binding(
            context.workspace_id, claim["id"], PROFILE_ID
        )["state"] == CurrentUseState.CURRENT
        accepted = client.post(
            "/api/qualification/invalidation-decisions",
            json={**request, "target_attempt_id": (first, second)[target_index]["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()["affected_binding_ids"] == [binding["id"]]
    assert repository.get_current_use_binding(
        context.workspace_id, claim["id"], PROFILE_ID
    )["state"] == CurrentUseState.STALE
