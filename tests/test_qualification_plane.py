import io
import json
import sqlite3
import zipfile

import pytest

from app.core.artifacts import ArtifactDraft, ArtifactKind
from app.core.contracts import RequestContext, ToolHints, ToolRisk, ToolSource, ToolSpec
from app.core.errors import InvalidEvidenceError
from app.core.kernel_verification import KERNEL_EXECUTION_CONTRACT_VERSION
from app.core.qualification import (
    AuthorizationGrantDraft,
    KnowledgeAdmissionDraft,
    MathTheoremCandidateDraft,
    QualificationVerdict,
    ValidationModality,
    claim_semantic_hash,
)
from app.core.research import (
    VerificationAttemptDraft,
    VerificationKind,
    VerificationOutcome,
)
from app.profiles.math_theorem import KERNEL_CERTIFICATE_VERSION, PROFILE_ID
from app.repository import SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.assurance import AssuranceBundleService, AssuranceBundleVerifier
from app.services.authorization import ToolAuthorizationGate
from app.services.evidence import EvidenceService
from app.services.qualification import QualificationService
from app.services.research_registry import ResearchRegistryService


def _services(tmp_path):
    repository = SQLiteRepository(tmp_path / "qualification.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    artifacts = ArtifactService(repository_provider=provider)
    evidence = EvidenceService(provider)
    qualification = QualificationService(
        provider,
        artifact_service=artifacts,
        evidence_service=evidence,
    )
    research = ResearchRegistryService(
        provider,
        artifact_service=artifacts,
        evidence_service=evidence,
    )
    return repository, artifacts, research, qualification


def test_math_formal_gate_earns_receipt_and_keeps_authority_separate(tmp_path) -> None:
    repository, artifacts, research, qualification = _services(tmp_path)
    context = RequestContext(
        request_id="qualification-test",
        workspace_id="workspace-a",
        principal_id="reviewer-a",
    )
    registered = qualification.register_math_theorem(
        context,
        MathTheoremCandidateDraft(
            claim_key="THM-SUM-EVEN",
            name="Sum of two even integers",
            statement="For all integers a and b, if a and b are even, a + b is even.",
            scope="Integers with the usual addition operation.",
            definitions=("even(n) iff there exists k in Z with n = 2*k",),
            negative_boundaries=("No claim is made for non-integer domains.",),
        ),
    )
    claim = registered["claim"]

    assert claim["semantic_hash"] == claim_semantic_hash(claim)
    assert qualification.list_profiles()[0]["profile_id"] == PROFILE_ID
    unresolved = qualification.evaluate(context, claim["id"], PROFILE_ID)
    assert unresolved["evaluation"]["verdict"] == QualificationVerdict.UNRESOLVED
    assert unresolved["qualification_receipt"] is None

    proof = artifacts.create_artifact(
        context,
        ArtifactDraft(
            name="sum-even.lean",
            kind=ArtifactKind.TEXT,
            media_type="text/plain",
            content_text=(
                "theorem sum_even (a b : Int) (ha : Even a) (hb : Even b) : "
                "Even (a + b) := by omega"
            ),
            metadata={"role": "formal_proof", "language": "Lean"},
        ),
    )
    certificate_payload = {
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
            "version": "4.23.0",
            "executable_hash": "a" * 64,
            "toolchain_hash": "a" * 64,
        },
        "status": "passed",
        "axioms": [],
        "sorry_present": False,
        "dependencies": [],
        "invocation": {"command": ["lean", "Main.lean"], "exit_code": 0},
        "isolation": {"shell": False, "request_controls_command": False},
    }
    certificate = artifacts.create_artifact(
        context,
        ArtifactDraft(
            name="sum-even.kernel-certificate.json",
            kind=ArtifactKind.JSON,
            media_type="application/json",
            content_text=json.dumps(certificate_payload, sort_keys=True),
            metadata={"role": "kernel_certificate"},
        ),
    )
    untrusted_attempt = research.record_verification_attempt(
        context,
        claim["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.CALCULATION,
            outcome=VerificationOutcome.PASSED,
            validation_modality=ValidationModality.KERNEL_CHECK,
            method="An administrator cannot appoint itself as the kernel verifier.",
            scope="Transport-bound negative test.",
            input_digest=claim["semantic_hash"],
            output_digest=certificate["content_hash"],
            artifact_ids=(proof["id"], certificate["id"]),
        ),
    )
    assert untrusted_attempt["independent"] is False
    still_unresolved = qualification.evaluate(context, claim["id"], PROFILE_ID)
    assert still_unresolved["evaluation"]["verdict"] == QualificationVerdict.UNRESOLVED
    assert still_unresolved["qualification_receipt"] is None
    kernel_context = RequestContext(
        request_id="kernel-verifier",
        workspace_id=context.workspace_id,
        principal_id="system:verifier:lean-kernel",
    )
    invalid_certificate = artifacts.create_artifact(
        context,
        ArtifactDraft(
            name="sum-even.invalid-kernel-certificate.json",
            kind=ArtifactKind.JSON,
            media_type="application/json",
            content_text=json.dumps(
                {**certificate_payload, "axioms": ["Classical.choice"]},
                sort_keys=True,
            ),
            metadata={"role": "kernel_certificate"},
        ),
    )
    research.record_verification_attempt(
        kernel_context,
        claim["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.CALCULATION,
            outcome=VerificationOutcome.PASSED,
            validation_modality=ValidationModality.KERNEL_CHECK,
            method="Replay an older proof that imports an undeclared axiom.",
            scope="Negative certificate selection fixture.",
            input_digest=claim["semantic_hash"],
            output_digest=invalid_certificate["content_hash"],
            artifact_ids=(proof["id"], invalid_certificate["id"]),
        ),
    )
    kernel_attempt = research.record_verification_attempt(
        kernel_context,
        claim["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.CALCULATION,
            outcome=VerificationOutcome.PASSED,
            validation_modality=ValidationModality.KERNEL_CHECK,
            method="Replay the formal proof in the declared Lean kernel.",
            scope="Exact frozen theorem revision and proof artifact.",
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
            method="Compare the natural-language and Lean theorem quantifiers.",
            scope="Statement and definition alignment only.",
            input_digest=claim["semantic_hash"],
            output_digest=proof["content_hash"],
            artifact_ids=(proof["id"],),
        ),
    )

    assert kernel_attempt["independence"]["derived"] is True
    assert (
        kernel_attempt["verifier_lineage"]["principal_id"]
        == "system:verifier:lean-kernel"
    )
    assert kernel_attempt["verifier_lineage"]["toolchain_hash"] == "a" * 64
    admitted = qualification.evaluate(context, claim["id"], PROFILE_ID)
    receipt = admitted["qualification_receipt"]
    assert admitted["evaluation"]["verdict"] == QualificationVerdict.ADMITTED
    assert receipt["claim_revision_id"] == claim["id"]
    assert receipt["claim_semantic_hash"] == claim["semantic_hash"]
    assert len(receipt["receipt_hash"]) == 64
    assert receipt["independence_summary"]["basis"] == []
    assert receipt["independence_summary"]["qualified"] is False
    assert receipt["independence_summary"]["verification_properties"] == [
        "orthogonal_non_llm_checker"
    ]
    assert receipt["evidence_vector"]["orthogonal_verification"] == "satisfied"
    assert receipt["evidence_vector"]["independent_validation"] == "undetermined"
    assert {
        item["same_agent"]
        for item in receipt["independence_summary"]["relationships"]
    } == {False, True}
    assert admitted["current_use_binding"] is None
    assert admitted["knowledge_admission_required"] is True
    assert qualification.qualified_search(context, "even integers", PROFILE_ID) == []

    admission = qualification.admit_knowledge(
        context,
        KnowledgeAdmissionDraft(
            qualification_receipt_id=receipt["id"],
            admission_policy_id="knowledge.default.v1",
            rationale="Admit the reviewed theorem to the workspace knowledge view.",
        ),
    )
    assert (
        admission["knowledge_admission_receipt"]["contract_version"]
        == "knowledge.admission.v1"
    )
    assert admission["knowledge_admission_receipt"]["approved_by"] == "reviewer-a"
    assert admission["current_use_binding"]["state"] == "current"
    assert (
        admission["current_use_binding"]["knowledge_admission_receipt_id"]
        == admission["knowledge_admission_receipt"]["id"]
    )
    with pytest.raises(sqlite3.IntegrityError, match="requires admission receipt"):
        repository.upsert_current_use_binding(
            context.workspace_id,
            {
                "claim_revision_id": claim["id"],
                "profile_id": PROFILE_ID,
                "use_scope": "knowledge",
                "qualification_receipt_id": receipt["id"],
                "state": "current",
                "bound_by": "bypass-attempt",
            },
        )
    with pytest.raises(sqlite3.IntegrityError, match="matching qualification"):
        repository.admit_knowledge(
            context.workspace_id,
            {
                **admission["knowledge_admission_receipt"],
                "id": "forged-admission",
                "qualification_receipt_hash": "0" * 64,
                "receipt_hash": "1" * 64,
            },
        )
    with repository._connect() as connection, pytest.raises(
        sqlite3.IntegrityError, match="immutable"
    ):
        connection.execute(
            "UPDATE knowledge_admission_receipts SET rationale = ? WHERE id = ?",
            ("silently changed", admission["knowledge_admission_receipt"]["id"]),
        )

    qualified = qualification.qualified_search(context, "even integers", PROFILE_ID)
    assert [item["id"] for item in qualified] == [claim["id"]]
    assert qualified[0]["currentness"] == "current"

    grant = qualification.create_authorization(
        context,
        "runtime:math-publisher",
        AuthorizationGrantDraft(
            qualification_receipt_id=receipt["id"],
            action="publish",
            target="knowledge/math",
            scope={"claim_revision_id": claim["id"]},
            max_calls=1,
        ),
    )
    assert grant["qualification_receipt_id"] == receipt["id"]
    assert grant["state"] == "active"
    assert grant["calls_used"] == 0
    assert repository.get_qualification_receipt(context.workspace_id, receipt["id"]) is not None
    archive, _manifest = AssuranceBundleService(lambda: repository).export_zip(
        context, registered["case"]["id"]
    )
    bundle_path = tmp_path / "qualified-assurance.zip"
    bundle_path.write_bytes(archive)
    offline = AssuranceBundleVerifier().verify(bundle_path)
    assert offline["valid"] is True
    assert offline["assurance"]["epistemic_state"]["status"] == "supported"
    assert offline["assurance"]["authority_state"]["status"] == "authorized"
    assert offline["assurance"]["orthogonal_verification"]["status"] == "satisfied"
    assert offline["assurance"]["independence"]["status"] == "not_qualified"

    consumed = repository.consume_authorization_grant(
        context.workspace_id,
        "runtime:math-publisher",
        "publish",
        "knowledge/math",
        "2026-09-29T00:00:00+00:00",
    )
    assert consumed is not None
    assert consumed["id"] == grant["id"]
    assert consumed["calls_used"] == 1
    assert (
        repository.consume_authorization_grant(
            context.workspace_id,
            "runtime:math-publisher",
            "publish",
            "knowledge/math",
            "2026-09-29T00:00:01+00:00",
        )
        is None
    )

    superseding = qualification.evaluate(context, claim["id"], PROFILE_ID)
    current_receipt = superseding["qualification_receipt"]
    assert current_receipt["id"] != receipt["id"]
    assert superseding["current_use_binding"] is None
    assert repository.get_current_use_binding(
        context.workspace_id, claim["id"], PROFILE_ID
    )["qualification_receipt_id"] == receipt["id"]
    second_admission = qualification.admit_knowledge(
        context,
        KnowledgeAdmissionDraft(
            qualification_receipt_id=current_receipt["id"],
            admission_policy_id="knowledge.default.v1",
            rationale="Select the newer qualification receipt for current use.",
        ),
    )
    assert second_admission["current_use_binding"]["qualification_receipt_id"] == (
        current_receipt["id"]
    )
    with pytest.raises(InvalidEvidenceError, match="current binding"):
        qualification.create_authorization(
            context,
            "runtime:math-publisher",
            AuthorizationGrantDraft(
                qualification_receipt_id=receipt["id"],
                action="publish",
                target="knowledge/math",
                scope={"claim_revision_id": claim["id"]},
            ),
        )
    qualification.create_authorization(
        context,
        "runtime:math-publisher",
        AuthorizationGrantDraft(
            qualification_receipt_id=current_receipt["id"],
            action="publish-current",
            target="knowledge/math",
            scope={"claim_revision_id": claim["id"]},
        ),
    )
    restrictive_physical_grant = qualification.create_authorization(
        context,
        context.principal_id,
        AuthorizationGrantDraft(
            qualification_receipt_id=current_receipt["id"],
            action="robot_navigate_to",
            target="provider:robot/robot:sim-mcp",
            scope={"provider_id": "robot", "required_scopes": ["robot:motion"]},
            conditions={
                "arguments": {
                    "frame_id": "map",
                    "x": {"$gte": 0, "$lte": 1},
                }
            },
            budget={"arguments": {"action_timeout_seconds": 60}},
        ),
    )
    physical_grant = qualification.create_authorization(
        context,
        context.principal_id,
        AuthorizationGrantDraft(
            qualification_receipt_id=current_receipt["id"],
            action="robot_navigate_to",
            target="provider:robot/robot:sim-mcp",
            scope={"provider_id": "robot", "required_scopes": ["robot:motion"]},
            conditions={
                "arguments": {
                    "frame_id": "map",
                    "x": {"$gte": 0, "$lte": 5},
                }
            },
            budget={"arguments": {"action_timeout_seconds": 60}},
        ),
    )
    authorization_gate = ToolAuthorizationGate(lambda: repository)
    physical_spec = ToolSpec(
        name="robot__robot_navigate_to",
        native_name="robot_navigate_to",
        description="Navigate.",
        input_schema={
            "type": "object",
            "properties": {
                "frame_id": {"type": "string", "default": "map"},
                "action_timeout_seconds": {"type": "number", "default": 60},
            },
        },
        source=ToolSource.MCP,
        provider_id="robot",
        risk=ToolRisk.HIGH,
        required_scopes=frozenset({"robot:motion"}),
        hints=ToolHints(read_only=False),
        extensions={
            "capability": {
                "execution_class": "physical",
                "effect_class": "physical_motion",
            },
            "authority_requirement": {
                "required": True,
                "action": "robot_navigate_to",
                "target": "robot:sim-mcp",
            },
        },
    )
    denied_physical = authorization_gate.authorize_and_consume(
        context,
        physical_spec,
        {
            "idempotency_key": "qualification-navigation-denied",
            "x": 6,
            "y": 3,
            "yaw": 0,
            "frame_id": "map",
            "action_timeout_seconds": 30,
        },
    )
    assert denied_physical is None
    assert repository.list_authorization_grants(context.workspace_id)[-1]["calls_used"] == 0
    consumed_physical = authorization_gate.authorize_and_consume(
        context,
        physical_spec,
        {
            "idempotency_key": "qualification-navigation-1",
            "x": 2,
            "y": 3,
            "yaw": 0,
            "frame_id": "map",
            "action_timeout_seconds": 30,
        },
    )
    assert consumed_physical is not None
    assert consumed_physical["id"] == physical_grant["id"]
    assert consumed_physical["calls_used"] == 1
    grants_by_id = {
        item["id"]: item
        for item in repository.list_authorization_grants(context.workspace_id)
    }
    assert grants_by_id[restrictive_physical_grant["id"]]["calls_used"] == 0
    with repository._connect() as connection:
        connection.execute(
            "UPDATE research_claim_revisions SET scope = ? WHERE tenant_id = ? AND id = ?",
            ("A silently widened domain.", context.workspace_id, claim["id"]),
        )
    stale_archive, _manifest = AssuranceBundleService(lambda: repository).export_zip(
        context, registered["case"]["id"]
    )
    with zipfile.ZipFile(io.BytesIO(stale_archive)) as bundle:
        qualification_document = json.loads(bundle.read("qualification.json"))
    exported_binding = next(
        item
        for item in qualification_document["current_use_bindings"]
        if item["claim_revision_id"] == claim["id"]
    )
    assert exported_binding["state"] == "stale"
    stale_binding = repository.get_current_use_binding(
        context.workspace_id, claim["id"], PROFILE_ID
    )
    assert stale_binding["state"] == "stale"
    assert "claim_semantic_hash_changed" in stale_binding["stale_reason"]
    with pytest.raises(InvalidEvidenceError, match="stale qualification evidence"):
        qualification.admit_knowledge(
            context,
            KnowledgeAdmissionDraft(
                qualification_receipt_id=current_receipt["id"],
                admission_policy_id="knowledge.default.v1",
                rationale="This stale receipt must not be re-admitted.",
            ),
        )
    assert qualification.qualified_search(context, "even integers", PROFILE_ID) == []
    assert qualification.get_receipt(context, receipt["id"])["receipt_hash"] == receipt[
        "receipt_hash"
    ]
    assert {item["id"] for item in qualification_document["receipts"]} == {
        receipt["id"],
        current_receipt["id"],
    }
    assert {
        item["qualification_receipt_id"]
        for item in qualification_document["knowledge_admissions"]
    } == {receipt["id"], current_receipt["id"]}
    stale_path = tmp_path / "stale-assurance.zip"
    stale_path.write_bytes(stale_archive)
    stale_offline = AssuranceBundleVerifier().verify(stale_path)
    assert stale_offline["valid"] is True
    assert stale_offline["assurance"]["authority_state"]["status"] == "blocked"


def test_semantic_change_invalidates_prior_hash_instead_of_inheriting(tmp_path) -> None:
    repository, _artifacts, _research, qualification = _services(tmp_path)
    context = RequestContext("semantic-firewall", "workspace-a", "reviewer-a")
    registered = qualification.register_math_theorem(
        context,
        MathTheoremCandidateDraft(
            claim_key="THM-BOUND",
            name="Frozen boundary",
            statement="P holds for every x in S.",
            scope="Only x in S.",
        ),
    )
    claim = registered["claim"]
    with repository._connect() as connection:
        connection.execute(
            "UPDATE research_claim_revisions SET scope = ? WHERE tenant_id = ? AND id = ?",
            ("Silently widened to every x.", context.workspace_id, claim["id"]),
        )

    result = qualification.evaluate(context, claim["id"], PROFILE_ID)

    assert result["evaluation"]["verdict"] == QualificationVerdict.STALE
    assert result["evaluation"]["blockers"] == ["statement_drift"]
    assert result["qualification_receipt"] is None


def test_duplicate_dependencies_are_normalized_before_evidence_edges(tmp_path) -> None:
    _repository, _artifacts, _research, qualification = _services(tmp_path)
    context = RequestContext("dependency-dedup", "workspace-a", "reviewer-a")
    dependency = qualification.register_math_theorem(
        context,
        MathTheoremCandidateDraft(
            claim_key="THM-DEPENDENCY",
            name="Dependency",
            statement="P.",
            scope="Fixture.",
        ),
    )["claim"]
    dependent = qualification.register_math_theorem(
        context,
        MathTheoremCandidateDraft(
            claim_key="THM-DEPENDENT",
            name="Dependent",
            statement="P implies Q.",
            scope="Fixture.",
            dependency_claim_ids=(dependency["id"], dependency["id"]),
        ),
    )["claim"]

    assert dependent["dependency_claim_ids"] == [dependency["id"]]
    evaluated = qualification.evaluate(context, dependent["id"], PROFILE_ID)
    dependency_nodes = [
        item
        for item in evaluated["evaluation"]["evidence_closure"]["nodes"]
        if item["node_type"] == "dependency_binding"
    ]
    assert [item["node_id"] for item in dependency_nodes] == [dependency["id"]]


def test_refresh_binding_propagates_transitive_dependency_staleness() -> None:
    def claim(claim_id: str) -> dict:
        value = {
            "id": claim_id,
            "claim_key": claim_id,
            "revision_number": 1,
            "claim_type": "mathematical_theorem",
            "statement": claim_id,
            "scope": "Fixture.",
            "definitions": [],
            "negative_boundaries": [],
            "dependency_claim_ids": [],
            "parent_revision_id": None,
        }
        return {**value, "semantic_hash": claim_semantic_hash(value)}

    claims = {claim_id: claim(claim_id) for claim_id in ("A", "B", "C")}
    receipts = {
        "receipt-A": {
            "id": "receipt-A",
            "claim_revision_id": "A",
            "claim_semantic_hash": claims["A"]["semantic_hash"],
            "profile_id": PROFILE_ID,
            "evaluation_id": "evaluation-A",
        },
        "receipt-B": {
            "id": "receipt-B",
            "claim_revision_id": "B",
            "claim_semantic_hash": claims["B"]["semantic_hash"],
            "profile_id": PROFILE_ID,
            "evaluation_id": "evaluation-B",
        },
        "receipt-C2": {
            "id": "receipt-C2",
            "claim_revision_id": "C",
            "claim_semantic_hash": claims["C"]["semantic_hash"],
            "profile_id": PROFILE_ID,
            "evaluation_id": "evaluation-C2",
        },
    }
    bindings = {
        claim_id: {
            "id": f"binding-{claim_id}",
            "tenant_id": "workspace-a",
            "claim_revision_id": claim_id,
            "profile_id": PROFILE_ID,
            "use_scope": "knowledge",
            "qualification_receipt_id": receipt_id,
            "state": "current",
            "stale_reason": None,
        }
        for claim_id, receipt_id in {
            "A": "receipt-A",
            "B": "receipt-B",
            "C": "receipt-C2",
        }.items()
    }
    evaluations = {
        "A": {
            "id": "evaluation-A",
            "evidence_closure": {
                "nodes": [
                    {
                        "node_type": "dependency_binding",
                        "node_id": "B",
                        "payload": {"qualification_receipt_id": "receipt-B"},
                    }
                ]
            },
        },
        "B": {
            "id": "evaluation-B",
            "evidence_closure": {
                "nodes": [
                    {
                        "node_type": "dependency_binding",
                        "node_id": "C",
                        "payload": {"qualification_receipt_id": "receipt-C1"},
                    }
                ]
            },
        },
        "C": {"id": "evaluation-C2", "evidence_closure": {"nodes": []}},
    }

    class DependencyRepository:
        def get_qualification_receipt(self, tenant_id, receipt_id):
            assert tenant_id == "workspace-a"
            return receipts.get(receipt_id)

        def get_research_claim(self, tenant_id, claim_id):
            assert tenant_id == "workspace-a"
            return claims.get(claim_id)

        def list_qualification_evaluations(self, tenant_id, claim_id, profile_id):
            assert tenant_id == "workspace-a"
            assert profile_id == PROFILE_ID
            return [evaluations[claim_id]]

        def get_current_use_binding(
            self, tenant_id, claim_id, profile_id, use_scope="knowledge"
        ):
            assert tenant_id == "workspace-a"
            assert profile_id == PROFILE_ID
            assert use_scope == "knowledge"
            value = bindings.get(claim_id)
            return dict(value) if value is not None else None

        def upsert_current_use_binding(self, tenant_id, values):
            assert tenant_id == "workspace-a"
            bindings[values["claim_revision_id"]] = dict(values)
            return dict(values)

    service = QualificationService(lambda: DependencyRepository())
    refreshed = service._refresh_binding(
        RequestContext("transitive-refresh", "workspace-a", "reviewer-a"),
        dict(bindings["A"]),
    )

    assert refreshed["state"] == "stale"
    assert refreshed["stale_reason"] == "dependency_binding_changed:B"
    assert bindings["B"]["state"] == "stale"
    assert bindings["B"]["stale_reason"] == "dependency_binding_changed:C"
    assert bindings["C"]["state"] == "current"
