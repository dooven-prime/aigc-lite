"""A Goal criterion observes qualification without acquiring its authority."""

from __future__ import annotations

import copy
import json
import os
from collections import Counter
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from authority_support import provision_admin

import app.repositories.goal_progress_snapshot as snapshot_module
import app.repository as repository_module
import app.services.goal_progress_evidence as bridge_module
from app.core.artifacts import ArtifactDraft, ArtifactKind
from app.core.contracts import NativeGoalDraft, RequestContext
from app.core.errors import ResourceNotFoundError
from app.core.goal_binding import protocol_goal_snapshot
from app.core.invalidation import (
    InvalidationDecisionDraft,
    InvalidationReason,
    InvalidationScope,
    NoticeVerificationDraft,
)
from app.core.kernel_verification import KERNEL_EXECUTION_CONTRACT_VERSION
from app.core.qualification import (
    KnowledgeAdmissionDraft,
    MathTheoremCandidateDraft,
    QualificationVerdict,
    ValidationModality,
)
from app.core.research import (
    VerificationAttemptDraft,
    VerificationKind,
    VerificationOutcome,
)
from app.profiles.math_theorem import (
    KERNEL_CERTIFICATE_VERSION,
    MATH_FORMAL_PROFILE,
    PROFILE_ID,
)
from app.repository import PostgresRepository, SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.evidence import EvidenceService
from app.services.goal_progress_evidence import (
    GoalProgressEvidenceBridgeService,
    _Decision,
)
from app.services.invalidation import InvalidationService
from app.services.qualification import (
    DEFAULT_KNOWLEDGE_ADMISSION_POLICY,
    QualificationService,
)
from app.services.research_registry import ResearchRegistryService

RULE_ID = "goal.current_knowledge_qualification.v0"
_AUTHORITY_TABLES = (
    "qualification_evaluations", "qualification_receipts",
    "knowledge_admission_receipts", "current_use_bindings",
    "authorization_grants", "invalidation_decisions",
)


def _repository(backend: str, tmp_path: Path):
    if backend == "postgres":
        url = os.getenv("AIGC_LITE_TEST_POSTGRES_URL", "")
        if not url:
            pytest.skip("AIGC_LITE_TEST_POSTGRES_URL is not configured")
        repository = PostgresRepository(url)
    else:
        repository = SQLiteRepository(tmp_path / "goal-progress.db")
    repository.init()
    return repository


def _setup(backend: str, tmp_path: Path):
    repository = _repository(backend, tmp_path)
    context = RequestContext(str(uuid4()), str(uuid4()), "reviewer-a")
    context, authority = provision_admin(repository, context)
    provider = lambda: repository  # noqa: E731
    artifacts = ArtifactService(repository_provider=provider)
    evidence = EvidenceService(provider)
    qualification = QualificationService(
        provider, artifact_service=artifacts, evidence_service=evidence,
    )
    research = ResearchRegistryService(
        provider, artifact_service=artifacts, evidence_service=evidence,
    )
    bridge = GoalProgressEvidenceBridgeService(repository_provider=provider)
    return repository, context, authority, artifacts, qualification, research, bridge


def _register_claim(qualification, context, *, dependency_ids=()):
    key = f"THM-{uuid4().hex[:12].upper()}"
    return qualification.register_math_theorem(
        context,
        MathTheoremCandidateDraft(
            claim_key=key,
            name="Goal bridge theorem",
            statement=f"For all integers n, n = n. ({key})",
            scope="Integers.",
            dependency_claim_ids=tuple(dependency_ids),
        ),
    )["claim"]


def _qualify_claim(
    artifacts, qualification, research, context, authority, claim, *, admit=True,
):
    proof = artifacts.create_artifact(
        context,
        ArtifactDraft(
            name=f"{claim['claim_key']}.lean", kind=ArtifactKind.TEXT,
            media_type="text/plain", content_text="theorem identity (n : Int) : n = n := rfl",
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
            "backend": "lean4", "name": "lean-kernel", "version": "4.23.0",
            "executable_hash": "a" * 64, "toolchain_hash": "a" * 64,
        },
        "status": "passed", "axioms": [], "sorry_present": False,
        "dependencies": [],
        "invocation": {"command": ["lean", "Main.lean"], "exit_code": 0},
        "isolation": {"shell": False, "request_controls_command": False},
    }
    certificate = artifacts.create_artifact(
        context,
        ArtifactDraft(
            name=f"{claim['claim_key']}.certificate.json", kind=ArtifactKind.JSON,
            media_type="application/json",
            content_text=json.dumps(certificate_payload, sort_keys=True),
            metadata={"role": "kernel_certificate"},
        ),
    )
    verifier = RequestContext(
        request_id=str(uuid4()), workspace_id=context.workspace_id,
        principal_id="system:verifier:lean-kernel",
    )
    research.record_verification_attempt(
        verifier, claim["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.CALCULATION, outcome=VerificationOutcome.PASSED,
            validation_modality=ValidationModality.KERNEL_CHECK,
            method="Replay exact proof with the declared kernel.", scope="Frozen theorem.",
            input_digest=claim["semantic_hash"], output_digest=certificate["content_hash"],
            artifact_ids=(proof["id"], certificate["id"]),
        ),
    )
    research.record_verification_attempt(
        context, claim["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.REVIEW, outcome=VerificationOutcome.PASSED,
            validation_modality=ValidationModality.EXPERT_REVIEW,
            method="Check quantifier and definition alignment.", scope="Frozen theorem.",
            input_digest=claim["semantic_hash"], output_digest=proof["content_hash"],
            artifact_ids=(proof["id"],),
        ),
    )
    evaluated = qualification.evaluate(context, claim["id"], PROFILE_ID)
    assert evaluated["evaluation"]["verdict"] == QualificationVerdict.ADMITTED
    receipt = evaluated["qualification_receipt"]
    assert receipt is not None
    if not admit:
        return receipt, None
    admitted = qualification.admit_knowledge(
        context,
        KnowledgeAdmissionDraft(
            qualification_receipt_id=receipt["id"],
            admission_policy_id="knowledge.default.v1",
            rationale="Select the verified theorem for current knowledge use.",
        ),
        authority=authority,
    )
    assert admitted["current_use_binding"]["state"] == "current"
    return receipt, admitted


def _goal_run(repository, context, claim, *, criterion_overrides=None, policy_overrides=None):
    protocol = repository.create_evidence_protocol(context.workspace_id, {
        "profile": "goal.progress.test", "name": "Goal progress source",
        "version": 1, "status": "frozen", "purpose": "Assess current knowledge evidence.",
        "scope": "One claim revision in one workspace.",
        "completion_predicate": "Current evidence is independently checked.",
        "stop_conditions": ["Evidence unavailable"], "budget": {"calls": 1},
        "content_hash": sha256(uuid4().bytes).hexdigest(),
    })
    source_protocol = repository.get_evidence_protocol_row(
        context.workspace_id, protocol["id"],
    )
    criterion = {
        "rule_id": RULE_ID,
        "claim_revision_id": claim["id"],
        "claim_semantic_hash": claim["semantic_hash"],
        "profile_id": PROFILE_ID,
        **(criterion_overrides or {}),
    }
    goal = {
        "contract": "LongHorizonGoal.v0",
        "protocol": protocol_goal_snapshot(source_protocol, context.workspace_id),
        "target": claim["claim_key"],
        "policy": {
            "evidence_criteria": [criterion],
            **(policy_overrides or {}),
        },
    }
    session = repository.create_session(context.workspace_id, "goal bridge test")
    run = repository.create_run(
        context.workspace_id, session["id"], str(uuid4()), None, "test-model",
        goal_binding=NativeGoalDraft(protocol["id"], protocol["version"], goal),
    )
    return run


def _authority_rows(repository, workspace_id):
    """Compare all authority records, including update timestamps, across a bridge call."""
    result = {}
    for table in _AUTHORITY_TABLES:
        statement = f"SELECT * FROM {table} WHERE tenant_id = :tenant_id ORDER BY id"
        if isinstance(repository, SQLiteRepository):
            with repository._connect() as db:
                rows = db.execute(statement, {"tenant_id": workspace_id}).fetchall()
                result[table] = [dict(row) for row in rows]
        else:
            result[table] = repository._many(statement, {"tenant_id": workspace_id})
    return result


def _verified_invalidation(
    repository, artifacts, context, authority, claim, *,
    scope, receipt_id=None, attempt_id=None,
):
    """Stage a real host decision from deterministic pinned bytes, without network I/O."""
    notice = b"# History\n## Withdrawals\nA source defect was confirmed.\n"
    service = InvalidationService(
        lambda: repository,
        artifact_service=artifacts,
        source_fetcher=lambda _url: notice,
    )
    verified = service.verify_notice(
        context, NoticeVerificationDraft(source_commit="a" * 40),
        authority=authority,
    )
    return service.decide(
        context,
        InvalidationDecisionDraft(
            notice_verification_id=verified["id"],
            target_claim_revision_id=claim["id"],
            target_claim_semantic_hash=claim["semantic_hash"],
            target_scope=scope,
            target_receipt_id=receipt_id,
            target_attempt_id=attempt_id,
            reason_code=(
                InvalidationReason.RECEIPT_DEFECT
                if scope is InvalidationScope.RECEIPT
                else InvalidationReason.PROOF_INVALIDATED
                if scope is InvalidationScope.PROOF_ATTEMPT
                else InvalidationReason.WITHDRAWN_UPSTREAM_CONSTRUCTION
            ),
            rationale="The pinned notice identifies the exact obsolete evidence.",
        ),
        authority=authority,
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_real_admitted_knowledge_is_supported_as_of_snapshot_without_writes(
    backend, tmp_path,
) -> None:
    repository, context, authority, artifacts, qualification, research, bridge = _setup(
        backend, tmp_path,
    )
    claim = _register_claim(qualification, context)
    receipt, admission = _qualify_claim(
        artifacts, qualification, research, context, authority, claim,
    )
    run = _goal_run(repository, context, claim)
    before = _authority_rows(repository, context.workspace_id)

    if isinstance(repository, SQLiteRepository):
        # data_version changes on commits from the bridge's other connections,
        # even if the write did not touch one of the authority tables below.
        with closing(repository._connect()) as observer:
            before_version = observer.execute("PRAGMA data_version").fetchone()[0]
            result = bridge.evaluate(context, run["id"])
            assert observer.execute("PRAGMA data_version").fetchone()[0] == before_version
    else:
        result = bridge.evaluate(context, run["id"])

    assert result["status"] == "SUPPORTED_AS_OF_SNAPSHOT"
    assert result["rule_id"] == RULE_ID
    assert result["run_id"] == run["id"]
    assert result["criterion"]["claim_revision_id"] == claim["id"]
    assert result["source"]["snapshot_sha256"]
    assert result["qualification_profile_identity"] == {
        "profile_id": MATH_FORMAL_PROFILE.profile_id,
        "version": MATH_FORMAL_PROFILE.version,
        "content_hash": MATH_FORMAL_PROFILE.content_hash,
        "policy_version": MATH_FORMAL_PROFILE.policy_version,
    }
    assert result["knowledge_admission_policy_identity"] == {
        "policy_id": DEFAULT_KNOWLEDGE_ADMISSION_POLICY.policy_id,
        "version": DEFAULT_KNOWLEDGE_ADMISSION_POLICY.version,
        "content_hash": DEFAULT_KNOWLEDGE_ADMISSION_POLICY.content_hash,
    }
    assert result["implementation_source_sha256"] == {
        "app/services/goal_progress_evidence.py": sha256(
            Path(bridge_module.__file__).read_bytes()
        ).hexdigest(),
        "app/repositories/goal_progress_snapshot.py": sha256(
            Path(snapshot_module.__file__).read_bytes()
        ).hexdigest(),
        "app/repository.py": sha256(
            Path(repository_module.__file__).read_bytes()
        ).hexdigest(),
    }
    assert result["authority_effects"] and not any(result["authority_effects"].values())
    assert _authority_rows(repository, context.workspace_id) == before
    assert bridge.evaluate(context, run["id"])["source"]["snapshot_sha256"] == (
        result["source"]["snapshot_sha256"]
    )
    assert repository.get_current_use_binding(
        context.workspace_id, claim["id"], PROFILE_ID,
    )["qualification_receipt_id"] == receipt["id"]
    assert admission["knowledge_admission_receipt"]["qualification_receipt_id"] == receipt["id"]


def test_candidate_and_receipt_without_admission_cannot_self_grant(tmp_path) -> None:
    repository, context, authority, artifacts, qualification, research, bridge = _setup(
        "sqlite", tmp_path,
    )
    claim = _register_claim(qualification, context)
    run = _goal_run(repository, context, claim, policy_overrides={
        "qualification_granted": True,
        "current_use": "current",
        "authorized": True,
    })
    before = _authority_rows(repository, context.workspace_id)
    candidate = bridge.evaluate(context, run["id"])
    assert candidate["status"] != "SUPPORTED_AS_OF_SNAPSHOT"
    assert _authority_rows(repository, context.workspace_id) == before

    first_receipt, _ = _qualify_claim(
        artifacts, qualification, research, context, authority, claim, admit=False,
    )
    without_admission = bridge.evaluate(context, run["id"])
    assert without_admission["status"] == "UNRESOLVED"
    assert "current_use_binding_missing" in without_admission["reasons"]
    qualification.admit_knowledge(
        context,
        KnowledgeAdmissionDraft(
            qualification_receipt_id=first_receipt["id"],
            admission_policy_id="knowledge.default.v1",
            rationale="Choose the verified receipt for current use.",
        ),
        authority=authority,
    )
    assert bridge.evaluate(context, run["id"])["status"] == "SUPPORTED_AS_OF_SNAPSHOT"
    # A newer qualifying receipt exists, but it is not selected by a new
    # admission. The stored old selection is the only authority source.
    new_receipt = qualification.evaluate(context, claim["id"], PROFILE_ID)[
        "qualification_receipt"
    ]
    assert new_receipt is not None
    current = repository.get_current_use_binding(context.workspace_id, claim["id"], PROFILE_ID)
    assert current["qualification_receipt_id"] != new_receipt["id"]
    assert bridge.evaluate(context, run["id"])["status"] == "SUPPORTED_AS_OF_SNAPSHOT"


def test_selected_dependency_receipt_change_blocks_stored_current_root(tmp_path) -> None:
    repository, context, authority, artifacts, qualification, research, bridge = _setup(
        "sqlite", tmp_path,
    )
    dependency = _register_claim(qualification, context)
    old_receipt, _ = _qualify_claim(
        artifacts, qualification, research, context, authority, dependency,
    )
    root = _register_claim(qualification, context, dependency_ids=(dependency["id"],))
    _qualify_claim(artifacts, qualification, research, context, authority, root)
    run = _goal_run(repository, context, root)
    assert bridge.evaluate(context, run["id"])["status"] == "SUPPORTED_AS_OF_SNAPSHOT"

    second = qualification.evaluate(context, dependency["id"], PROFILE_ID)
    new_receipt = second["qualification_receipt"]
    assert new_receipt and new_receipt["id"] != old_receipt["id"]
    qualification.admit_knowledge(
        context,
        KnowledgeAdmissionDraft(
            qualification_receipt_id=new_receipt["id"],
            admission_policy_id="knowledge.default.v1",
            rationale="Select newer qualification evidence.",
        ),
        authority=authority,
    )
    assert repository.get_current_use_binding(
        context.workspace_id, root["id"], PROFILE_ID,
    )["state"] == "current"
    before = _authority_rows(repository, context.workspace_id)
    stale = bridge.evaluate(context, run["id"])
    assert stale["status"] == "BLOCKED"
    assert _authority_rows(repository, context.workspace_id) == before


def test_shared_dependency_diamond_is_checked_once_per_subject(
    tmp_path, monkeypatch,
) -> None:
    repository, context, authority, artifacts, qualification, research, bridge = _setup(
        "sqlite", tmp_path,
    )
    bottom = _register_claim(qualification, context)
    _qualify_claim(artifacts, qualification, research, context, authority, bottom)
    left = _register_claim(qualification, context, dependency_ids=(bottom["id"],))
    _qualify_claim(artifacts, qualification, research, context, authority, left)
    right = _register_claim(qualification, context, dependency_ids=(bottom["id"],))
    _qualify_claim(artifacts, qualification, research, context, authority, right)
    root = _register_claim(
        qualification, context, dependency_ids=(left["id"], right["id"]),
    )
    _qualify_claim(artifacts, qualification, research, context, authority, root)
    run = _goal_run(repository, context, root)
    calls = Counter()
    original = bridge._check_qualification

    def counted(profile, receipt, evaluation, workspace_id):
        calls[receipt["claim_revision_id"]] += 1
        return original(profile, receipt, evaluation, workspace_id)

    monkeypatch.setattr(bridge, "_check_qualification", counted)
    before = _authority_rows(repository, context.workspace_id)
    result = bridge.evaluate(context, run["id"])
    assert result["status"] == "SUPPORTED_AS_OF_SNAPSHOT"
    assert calls == Counter({
        bottom["id"]: 1, left["id"]: 1, right["id"]: 1, root["id"]: 1,
    })
    assert _authority_rows(repository, context.workspace_id) == before


def test_cycle_guard_precedes_success_memo() -> None:
    service = GoalProgressEvidenceBridgeService(repository_provider=lambda: None)
    with pytest.raises(_Decision) as error:
        service._check_subject(
            {}, "reachable-cycle", {}, MATH_FORMAL_PROFILE,
            DEFAULT_KNOWLEDGE_ADMISSION_POLICY, frozenset({"reachable-cycle"}),
            {"reachable-cycle"},
        )
    assert error.value.status == "BLOCKED"
    assert error.value.reason == "dependency_cycle"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_claim_invalidation_blocks_root_while_root_binding_is_still_current(
    backend, tmp_path,
) -> None:
    repository, context, authority, artifacts, qualification, research, bridge = _setup(
        backend, tmp_path,
    )
    dependency = _register_claim(qualification, context)
    _qualify_claim(artifacts, qualification, research, context, authority, dependency)
    root = _register_claim(qualification, context, dependency_ids=(dependency["id"],))
    _qualify_claim(artifacts, qualification, research, context, authority, root)
    run = _goal_run(repository, context, root)
    assert bridge.evaluate(context, run["id"])["status"] == "SUPPORTED_AS_OF_SNAPSHOT"

    decision = _verified_invalidation(
        repository, artifacts, context, authority, dependency,
        scope=InvalidationScope.CLAIM_REVISION,
    )
    assert repository.get_current_use_binding(
        context.workspace_id, dependency["id"], PROFILE_ID,
    )["state"] == "revoked"
    assert repository.get_current_use_binding(
        context.workspace_id, root["id"], PROFILE_ID,
    )["state"] == "current"
    before = _authority_rows(repository, context.workspace_id)
    result = bridge.evaluate(context, run["id"])
    assert result["status"] == "BLOCKED"
    assert "dependency_binding_changed" in result["reasons"]
    assert decision["target_claim_revision_id"] == dependency["id"]
    assert _authority_rows(repository, context.workspace_id) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_invalidated_historical_receipt_does_not_taint_new_selected_receipt(
    backend, tmp_path,
) -> None:
    repository, context, authority, artifacts, qualification, research, bridge = _setup(
        backend, tmp_path,
    )
    claim = _register_claim(qualification, context)
    old_receipt, _ = _qualify_claim(
        artifacts, qualification, research, context, authority, claim,
    )
    run = _goal_run(repository, context, claim)
    new_receipt = qualification.evaluate(context, claim["id"], PROFILE_ID)[
        "qualification_receipt"
    ]
    assert new_receipt and new_receipt["id"] != old_receipt["id"]
    qualification.admit_knowledge(
        context,
        KnowledgeAdmissionDraft(
            qualification_receipt_id=new_receipt["id"],
            admission_policy_id="knowledge.default.v1",
            rationale="Use newer reviewed qualification.",
        ),
        authority=authority,
    )
    decision = _verified_invalidation(
        repository, artifacts, context, authority, claim,
        scope=InvalidationScope.RECEIPT, receipt_id=old_receipt["id"],
    )
    assert repository.get_current_use_binding(
        context.workspace_id, claim["id"], PROFILE_ID,
    )["qualification_receipt_id"] == new_receipt["id"]
    before = _authority_rows(repository, context.workspace_id)
    result = bridge.evaluate(context, run["id"])
    assert result["status"] == "SUPPORTED_AS_OF_SNAPSHOT"
    assert decision["id"] in {
        item["id"] for item in repository.list_invalidation_decisions(
            context.workspace_id, claim["id"],
        )
    }
    assert _authority_rows(repository, context.workspace_id) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_proof_attempt_invalidation_follows_selected_attempt_across_receipts(
    backend, tmp_path,
) -> None:
    repository, context, authority, artifacts, qualification, research, bridge = _setup(
        backend, tmp_path,
    )
    claim = _register_claim(qualification, context)
    r1, _ = _qualify_claim(
        artifacts, qualification, research, context, authority, claim,
    )
    attempts = repository.list_research_verification_attempts(
        context.workspace_id, claim["id"],
    )
    selected_a = next(
        item for item in attempts if item["validation_modality"] == "kernel_check"
    )
    r2 = qualification.evaluate(context, claim["id"], PROFILE_ID)[
        "qualification_receipt"
    ]
    assert r2 and r2["id"] != r1["id"]
    qualification.admit_knowledge(
        context,
        KnowledgeAdmissionDraft(
            qualification_receipt_id=r2["id"],
            admission_policy_id="knowledge.default.v1",
            rationale="Select second receipt using the same kernel attempt.",
        ),
        authority=authority,
    )
    run = _goal_run(repository, context, claim)
    assert bridge.evaluate(context, run["id"])["status"] == "SUPPORTED_AS_OF_SNAPSHOT"
    root = _register_claim(qualification, context, dependency_ids=(claim["id"],))
    _qualify_claim(artifacts, qualification, research, context, authority, root)
    root_run = _goal_run(repository, context, root)
    assert bridge.evaluate(context, root_run["id"])["status"] == "SUPPORTED_AS_OF_SNAPSHOT"

    decision = _verified_invalidation(
        repository, artifacts, context, authority, claim,
        scope=InvalidationScope.PROOF_ATTEMPT,
        receipt_id=r1["id"], attempt_id=selected_a["id"],
    )
    assert repository.get_current_use_binding(
        context.workspace_id, claim["id"], PROFILE_ID,
    )["state"] == "current"
    before = _authority_rows(repository, context.workspace_id)
    invalidated_r2 = bridge.evaluate(context, run["id"])
    assert invalidated_r2["status"] == "BLOCKED"
    assert invalidated_r2["reasons"] == [f"invalidation_decision:{decision['id']}"]
    assert repository.get_current_use_binding(
        context.workspace_id, root["id"], PROFILE_ID,
    )["state"] == "current"
    invalidated_root = bridge.evaluate(context, root_run["id"])
    assert invalidated_root["status"] == "BLOCKED"
    assert invalidated_root["reasons"] == [f"invalidation_decision:{decision['id']}"]
    assert _authority_rows(repository, context.workspace_id) == before

    r3, _ = _qualify_claim(
        artifacts, qualification, research, context, authority, claim,
    )
    assert r3["id"] not in {r1["id"], r2["id"]}
    frozen = next(
        item for item in repository.list_qualification_evaluations(
            context.workspace_id, claim["id"], PROFILE_ID,
        ) if item["id"] == r3["evaluation_id"]
    )["evidence_closure"]
    frozen_attempt_ids = {
        item["node_id"] for item in frozen["nodes"]
        if item["node_type"] == "verification_attempt"
    }
    assert selected_a["id"] in frozen_attempt_ids
    selected_certificate_ids = {
        artifact_id
        for criterion in r3["criteria"]
        if criterion["code"] == "statement_identity"
        for artifact_id in criterion["evidence_node_ids"]
    }
    assert selected_certificate_ids
    selected_attempt_ids = {
        item["node_id"] for item in frozen["nodes"]
        if item["node_type"] == "verification_attempt"
        and item["payload"].get("validation_modality") == "kernel_check"
        and selected_certificate_ids.intersection(
            item["payload"].get("artifact_ids") or []
        )
    }
    assert len(selected_attempt_ids) == 1
    assert selected_a["id"] not in selected_attempt_ids
    assert bridge.evaluate(context, run["id"])["status"] == "SUPPORTED_AS_OF_SNAPSHOT"


@pytest.mark.parametrize("mismatch", ["revision", "profile", "hash"])
def test_frozen_identity_mismatch_never_supports(mismatch, tmp_path) -> None:
    repository, context, authority, artifacts, qualification, research, bridge = _setup(
        "sqlite", tmp_path,
    )
    claim = _register_claim(qualification, context)
    _qualify_claim(artifacts, qualification, research, context, authority, claim)
    wrong = {
        "revision": {"claim_revision_id": str(uuid4())},
        "profile": {"profile_id": "math.unknown.v1"},
        "hash": {"claim_semantic_hash": "0" * 64},
    }[mismatch]
    run = _goal_run(repository, context, claim, criterion_overrides=wrong)
    before = _authority_rows(repository, context.workspace_id)
    result = bridge.evaluate(context, run["id"])
    assert result["status"] != "SUPPORTED_AS_OF_SNAPSHOT"
    if mismatch == "profile":
        assert result["status"] == "UNRESOLVED"
        assert result["reasons"] == ["qualification_profile_unavailable"]
        assert result["qualification_profile_identity"] is None
    assert _authority_rows(repository, context.workspace_id) == before


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("profile_hash", "qualification_profile_or_policy_changed"),
        ("receipt_hash", "qualification_receipt_hash_mismatch"),
        ("admission_hash", "knowledge_admission_receipt_hash_mismatch"),
        ("claim_hash", "claim_revision_semantic_drift"),
    ],
)
def test_changed_source_hashes_fail_closed(mutation, reason, tmp_path) -> None:
    repository, context, authority, artifacts, qualification, research, _bridge = _setup(
        "sqlite", tmp_path,
    )
    claim = _register_claim(qualification, context)
    _qualify_claim(artifacts, qualification, research, context, authority, claim)
    run = _goal_run(repository, context, claim)

    class ChangedSnapshot:
        def read_goal_progress_run_hint(self, *args):
            return repository.read_goal_progress_run_hint(*args)

        def read_goal_progress_snapshot(self, *args):
            snapshot = copy.deepcopy(repository.read_goal_progress_snapshot(*args))
            subject = snapshot["subjects"][claim["id"]]
            if mutation == "profile_hash":
                subject["evaluations"][0]["profile_hash"] = "0" * 64
            elif mutation == "receipt_hash":
                subject["receipts"][0]["receipt_hash"] = "0" * 64
            elif mutation == "admission_hash":
                subject["admissions"][0]["receipt_hash"] = "0" * 64
            else:
                subject["claim"]["semantic_hash"] = "0" * 64
            return snapshot

    before = _authority_rows(repository, context.workspace_id)
    result = GoalProgressEvidenceBridgeService(
        repository_provider=lambda: ChangedSnapshot(),
    ).evaluate(context, run["id"])
    assert result["status"] == "BLOCKED"
    assert reason in result["reasons"]
    assert _authority_rows(repository, context.workspace_id) == before


@pytest.mark.parametrize(
    ("mutation", "status", "reason"),
    [
        ("missing", "UNRESOLVED", "knowledge_admission_receipt_missing"),
        ("wrong_receipt", "BLOCKED", "knowledge_admission_identity_mismatch"),
    ],
)
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_current_binding_needs_exact_selected_admission(
    backend, mutation, status, reason, tmp_path,
) -> None:
    repository, context, authority, artifacts, qualification, research, _bridge = _setup(
        backend, tmp_path,
    )
    claim = _register_claim(qualification, context)
    receipt, _ = _qualify_claim(
        artifacts, qualification, research, context, authority, claim,
    )
    run = _goal_run(repository, context, claim)
    assert repository.get_current_use_binding(
        context.workspace_id, claim["id"], PROFILE_ID,
    )["qualification_receipt_id"] == receipt["id"]

    class AdmissionGap:
        def read_goal_progress_run_hint(self, *args):
            return repository.read_goal_progress_run_hint(*args)

        def read_goal_progress_snapshot(self, *args):
            snapshot = copy.deepcopy(repository.read_goal_progress_snapshot(*args))
            subject = snapshot["subjects"][claim["id"]]
            assert subject["binding"]["state"] == "current"
            assert subject["receipts"][0]["id"] == receipt["id"]
            if mutation == "missing":
                subject["admissions"] = []
            else:
                subject["admissions"][0]["qualification_receipt_id"] = str(uuid4())
            return snapshot

    before = _authority_rows(repository, context.workspace_id)
    result = GoalProgressEvidenceBridgeService(
        repository_provider=lambda: AdmissionGap(),
    ).evaluate(context, run["id"])
    assert result["status"] == status
    assert reason in result["reasons"]
    assert _authority_rows(repository, context.workspace_id) == before


def test_cross_workspace_run_is_not_projected(tmp_path) -> None:
    repository, context, _authority, _artifacts, qualification, _research, bridge = _setup(
        "sqlite", tmp_path,
    )
    claim = _register_claim(qualification, context)
    run = _goal_run(repository, context, claim)
    other = RequestContext(str(uuid4()), str(uuid4()), "reader-b")
    repository.create_tenant("Other workspace", other.workspace_id)
    with pytest.raises(ResourceNotFoundError):
        bridge.evaluate(other, run["id"])


def test_absent_sqlite_database_is_not_created_by_run_preflight(tmp_path) -> None:
    database_path = tmp_path / "absent-parent" / "never-created.db"
    repository = SQLiteRepository(database_path)
    bridge = GoalProgressEvidenceBridgeService(repository_provider=lambda: repository)
    context = RequestContext(str(uuid4()), str(uuid4()), "reader")
    try:
        result = bridge.evaluate(context, str(uuid4()))
    except ResourceNotFoundError:
        pass
    else:
        assert result["status"] == "UNRESOLVED"
    assert not database_path.exists()
    assert not database_path.parent.exists()


def test_result_is_bound_to_snapshot_when_source_changes_after_read(tmp_path) -> None:
    repository, context, authority, artifacts, qualification, research, _bridge = _setup(
        "sqlite", tmp_path,
    )
    claim = _register_claim(qualification, context)
    _qualify_claim(artifacts, qualification, research, context, authority, claim)
    run = _goal_run(repository, context, claim)
    original_snapshot = repository.read_goal_progress_snapshot

    class ChangeAfterRead:
        def read_goal_progress_run_hint(self, *args):
            return repository.read_goal_progress_run_hint(*args)

        def read_goal_progress_snapshot(self, *args):
            snapshot = original_snapshot(*args)
            binding = repository.get_current_use_binding(
                context.workspace_id, claim["id"], PROFILE_ID,
            )
            repository.upsert_current_use_binding(context.workspace_id, {
                **binding, "state": "stale", "stale_reason": "external_update",
            })
            return snapshot

    raced = GoalProgressEvidenceBridgeService(repository_provider=lambda: ChangeAfterRead())
    result = raced.evaluate(context, run["id"])
    assert result["status"] == "SUPPORTED_AS_OF_SNAPSHOT"
    assert GoalProgressEvidenceBridgeService(
        repository_provider=lambda: repository,
    ).evaluate(context, run["id"])["status"] == "BLOCKED"
