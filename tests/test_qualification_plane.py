import io
import json
import zipfile

from app.core.artifacts import ArtifactDraft, ArtifactKind
from app.core.contracts import RequestContext
from app.core.kernel_verification import KERNEL_EXECUTION_CONTRACT_VERSION
from app.core.qualification import (
    AuthorizationGrantDraft,
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
    assert receipt["independence_summary"]["basis"] == [
        "orthogonal_non_llm_checker"
    ]
    assert {
        item["same_agent"]
        for item in receipt["independence_summary"]["relationships"]
    } == {False, True}
    assert admitted["current_use_binding"]["state"] == "current"

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
    with repository._connect() as connection:
        connection.execute(
            "UPDATE research_claim_revisions SET scope = ? WHERE tenant_id = ? AND id = ?",
            ("A silently widened domain.", context.workspace_id, claim["id"]),
        )
    assert qualification.qualified_search(context, "even integers", PROFILE_ID) == []
    stale_binding = repository.get_current_use_binding(
        context.workspace_id, claim["id"], PROFILE_ID
    )
    assert stale_binding["state"] == "stale"
    assert "claim_semantic_hash_changed" in stale_binding["stale_reason"]
    assert qualification.get_receipt(context, receipt["id"])["receipt_hash"] == receipt[
        "receipt_hash"
    ]
    stale_archive, _manifest = AssuranceBundleService(lambda: repository).export_zip(
        context, registered["case"]["id"]
    )
    with zipfile.ZipFile(io.BytesIO(stale_archive)) as bundle:
        qualification_document = json.loads(bundle.read("qualification.json"))
    assert [item["id"] for item in qualification_document["receipts"]] == [
        receipt["id"]
    ]
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
