"""Read-only, conditional evidence for one natively bound Goal criterion.

This bridge does not admit knowledge, refresh a stored binding, or authorize a
recovery action.  It interprets a caller's Goal criterion only through the
single, host-owned rule below and evaluates one repository-owned SQL snapshot.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.contracts import RequestContext
from ..core.errors import LedgerProjectionTooLargeError, ResourceNotFoundError
from ..core.goal_binding import assess_native_goal_binding
from ..core.qualification import (
    EVIDENCE_CLOSURE_VERSION,
    KNOWLEDGE_ADMISSION_CONTRACT_VERSION,
    KnowledgeAdmissionPolicy,
    QualificationProfile,
    canonical_hash,
    claim_semantic_hash,
)
from ..database import get_repository
from ..repository import Repository
from .ledger_projection import _hash as _row_hash
from .qualification import (
    DEFAULT_KNOWLEDGE_ADMISSION_POLICY,
    DomainVerifierRegistry,
    create_default_verifier_registry,
)

RepositoryProvider = Callable[[], Repository]

CONTRACT_VERSION = "GoalProgressEvidenceBridge.v0"
RULE_ID = "goal.current_knowledge_qualification.v0"
_RULE = {
    "rule_id": RULE_ID,
    "version": 0,
    "criterion_fields": [
        "rule_id", "claim_revision_id", "claim_semantic_hash", "profile_id",
    ],
    "use_scope": "knowledge",
    "requires": [
        "exact_claim_revision_and_semantic_hash",
        "current_profile_and_policy",
        "admitted_qualification_receipt_and_frozen_closure",
        "matching_knowledge_admission_receipt",
        "same_snapshot_current_use_and_recursive_dependency_currentness",
        "no_applicable_invalidation_decision",
    ],
}
RULE_SHA256 = canonical_hash(_RULE)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_SOURCE_BYTES = 2_000_000
_CRITERION_FIELDS = frozenset(_RULE["criterion_fields"])
_RECEIPT_FIELDS = (
    "claim_revision_id", "claim_semantic_hash", "profile_version",
    "evidence_closure_hash", "policy_version", "policy_hash", "verdict",
    "criteria", "blockers", "evidence_vector", "independence_summary",
    "issued_at",
)
_ADMISSION_FIELDS = (
    "contract_version", "qualification_receipt_id",
    "qualification_receipt_hash", "claim_revision_id", "claim_semantic_hash",
    "profile_id", "profile_version", "use_scope", "admission_policy_id",
    "admission_policy_version", "admission_policy_hash", "approved_by",
    "rationale", "issued_at",
)
_INVALIDATION_HASH_FIELDS = (
    "id", "notice_verification_id", "target_claim_revision_id",
    "target_claim_semantic_hash", "target_receipt_id", "target_attempt_id",
    "target_scope", "reason_code", "evidence_artifact_ids",
    "evidence_artifact_hashes", "policy_id", "policy_hash", "rationale",
    "decided_by", "effect_state", "decided_at",
)


@dataclass(frozen=True, slots=True)
class _Decision(Exception):
    status: str
    reason: str


def _blocked(reason: str) -> None:
    raise _Decision("BLOCKED", reason)


def _unresolved(reason: str) -> None:
    raise _Decision("UNRESOLVED", reason)


def _one(items: Any, missing_reason: str) -> dict[str, Any]:
    if not isinstance(items, list) or len(items) > 1:
        _unresolved("ambiguous_or_malformed_snapshot_selection")
    if not items:
        _unresolved(missing_reason)
    if not isinstance(items[0], dict):
        _unresolved("malformed_snapshot_row")
    return items[0]


def _criterion_from_run(run: dict[str, Any]) -> dict[str, str]:
    raw = run.get("goal_json")
    if not isinstance(raw, str):
        _unresolved("native_goal_missing")
    try:
        if len(raw.encode("utf-8")) > 32_768:
            _unresolved("native_goal_exceeds_byte_limit")
    except UnicodeError:
        _unresolved("native_goal_malformed")
    try:
        goal = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        _unresolved("native_goal_malformed")
    if not isinstance(goal, dict) or not isinstance(goal.get("policy"), dict):
        _unresolved("native_goal_malformed")
    criteria = goal["policy"].get("evidence_criteria")
    if not isinstance(criteria, list) or len(criteria) != 1:
        _unresolved("exactly_one_evidence_criterion_required")
    item = criteria[0]
    if not isinstance(item, dict) or set(item) != _CRITERION_FIELDS:
        _unresolved("evidence_criterion_shape_invalid")
    if item.get("rule_id") != RULE_ID:
        _unresolved("host_rule_not_registered")
    for field in ("claim_revision_id", "profile_id"):
        if not isinstance(item.get(field), str) or not item[field].strip():
            _unresolved("evidence_criterion_identity_invalid")
    if not isinstance(item.get("claim_semantic_hash"), str) or not _SHA256.fullmatch(
        item["claim_semantic_hash"]
    ):
        _unresolved("evidence_criterion_semantic_hash_invalid")
    return item


def _source_refs(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Bind the exact host-read rows, not a caller's summary of them."""
    grouped: dict[str, list[dict[str, str]]] = {}

    def add(table: str, row: Any) -> None:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError("Malformed source row identity")
        grouped.setdefault(table, []).append({"id": row["id"], "sha256": _row_hash(row)})

    add("agent_runs", snapshot["run"])
    for row in snapshot["goal_protocols"]:
        add("evidence_protocols", row)
    for subject in snapshot["subjects"].values():
        if subject.get("claim") is not None:
            add("research_claim_revisions", subject["claim"])
        if subject.get("binding") is not None:
            add("current_use_bindings", subject["binding"])
        for key, table in (
            ("sources", "research_sources"),
            ("receipts", "qualification_receipts"),
            ("evaluations", "qualification_evaluations"),
            ("admissions", "knowledge_admission_receipts"),
            ("invalidations", "invalidation_decisions"),
            ("artifacts", "artifacts"),
        ):
            for row in subject[key]:
                add(table, row)
    for rows in grouped.values():
        rows.sort(key=lambda item: (item["id"], item["sha256"]))
    missing = {
        "snapshot": snapshot.get("missing_references", []),
        "subjects": {
            claim_id: subject.get("missing_references", [])
            for claim_id, subject in sorted(snapshot["subjects"].items())
        },
    }
    return {
        "rows": dict(sorted(grouped.items())),
        "missing_references": missing,
        "snapshot_sha256": _row_hash({
            "contract_version": snapshot["contract_version"],
            "tenant_id": snapshot["tenant_id"],
            "run_id": snapshot["run_id"],
            "root_claim_revision_id": snapshot["root_claim_revision_id"],
            "profile_id": snapshot["profile_id"],
            "rows": grouped,
            "missing_references": missing,
        }),
        "identity_scope": "SINGLE_DATABASE_READ_SNAPSHOT",
    }


def _implementation_source_sha256() -> dict[str, str]:
    """Identify installed source bytes; this is not a correctness attestation."""
    app_root = Path(__file__).resolve().parents[1]
    paths = {
        "app/services/goal_progress_evidence.py": Path(__file__).resolve(),
        "app/repositories/goal_progress_snapshot.py": (
            app_root / "repositories" / "goal_progress_snapshot.py"
        ),
        "app/repository.py": app_root / "repository.py",
    }
    hashes: dict[str, str] = {}
    for label, path in paths.items():
        if path.stat().st_size > _MAX_SOURCE_BYTES:
            raise ValueError("Implementation source exceeds byte limit")
        source_bytes = path.read_bytes()
        if len(source_bytes) > _MAX_SOURCE_BYTES:
            raise ValueError("Implementation source exceeds byte limit")
        hashes[label] = hashlib.sha256(source_bytes).hexdigest()
    return hashes


class GoalProgressEvidenceBridgeService:
    """Assess exact current knowledge use as of one native database snapshot."""

    def __init__(
        self,
        repository_provider: RepositoryProvider = get_repository,
        *,
        registry: DomainVerifierRegistry | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._registry = registry or create_default_verifier_registry()

    def evaluate(self, context: RequestContext, run_id: str) -> dict[str, Any]:
        repository = self._repository_provider()
        criterion: dict[str, str] | None = None
        source: dict[str, Any] | None = None
        implementation_source_sha256: dict[str, str] | None = None
        qualification_profile_identity: dict[str, Any] | None = None
        knowledge_admission_policy_identity: dict[str, Any] | None = None
        try:
            admission_policy = DEFAULT_KNOWLEDGE_ADMISSION_POLICY
            knowledge_admission_policy_identity = {
                "policy_id": admission_policy.policy_id,
                "version": admission_policy.version,
                "content_hash": admission_policy.content_hash,
            }
            implementation_source_sha256 = _implementation_source_sha256()
            preflight = repository.read_goal_progress_run_hint(
                context.workspace_id, run_id
            )
            if preflight is None:
                raise ResourceNotFoundError("run", run_id)
            criterion = _criterion_from_run(preflight)
            try:
                profile = self._registry.get(criterion["profile_id"]).profile
            except ResourceNotFoundError:
                _unresolved("qualification_profile_unavailable")
            if profile.profile_id != criterion["profile_id"]:
                _unresolved("qualification_profile_identity_mismatch")
            qualification_profile_identity = {
                "profile_id": profile.profile_id,
                "version": profile.version,
                "content_hash": profile.content_hash,
                "policy_version": profile.policy_version,
            }
            snapshot = repository.read_goal_progress_snapshot(
                context.workspace_id,
                run_id,
                criterion["claim_revision_id"],
                criterion["profile_id"],
            )
            if snapshot is None:
                _unresolved("run_disappeared_before_snapshot")
            self._validate_snapshot_identity(snapshot, context, run_id, criterion)
            source = _source_refs(snapshot)
            self._check_subject(
                snapshot,
                criterion["claim_revision_id"],
                criterion,
                profile,
                admission_policy,
                frozenset(),
                set(),
            )
            status, reasons = "SUPPORTED_AS_OF_SNAPSHOT", []
        except _Decision as decision:
            status, reasons = decision.status, [decision.reason]
        except (KeyError, TypeError, ValueError, OSError, RecursionError,
                LedgerProjectionTooLargeError):
            status, reasons = "UNRESOLVED", ["snapshot_or_record_malformed"]
        return {
            "contract": CONTRACT_VERSION,
            "rule_id": RULE_ID,
            "rule_sha256": RULE_SHA256,
            "implementation_source_sha256": implementation_source_sha256,
            "qualification_profile_identity": qualification_profile_identity,
            "knowledge_admission_policy_identity": knowledge_admission_policy_identity,
            "run_id": run_id,
            "criterion": criterion,
            "status": status,
            "reasons": reasons,
            "source": source,
            "limitations": [
                "A snapshot result does not remain current after later writes.",
                "A qualification receipt does not prove the scientific claim is true.",
                "The caller's criterion may not establish progress toward the Goal target.",
                "Goal identity and this diagnosis grant no recovery or execution authority.",
            ],
            "authority_effects": {
                "qualification_receipt_created": False,
                "current_use_binding_created": False,
                "authorization_grant_created": False,
                "dispatch_performed": False,
            },
        }

    @staticmethod
    def _validate_snapshot_identity(
        snapshot: dict[str, Any], context: RequestContext, run_id: str,
        criterion: dict[str, str],
    ) -> None:
        if (
            snapshot.get("contract_version") != "goal.progress.snapshot.v0"
            or snapshot.get("tenant_id") != context.workspace_id
            or snapshot.get("run_id") != run_id
            or snapshot.get("root_claim_revision_id") != criterion["claim_revision_id"]
            or snapshot.get("profile_id") != criterion["profile_id"]
        ):
            _blocked("snapshot_identity_mismatch")
        run = snapshot.get("run")
        if not isinstance(run, dict) or (
            run.get("id") != run_id or run.get("tenant_id") != context.workspace_id
        ):
            _blocked("run_identity_mismatch")
        protocols = snapshot.get("goal_protocols")
        if not isinstance(protocols, list) or len(protocols) != 1:
            _unresolved("goal_protocol_missing_or_ambiguous")
        protocol = protocols[0]
        if not isinstance(protocol, dict) or protocol.get("tenant_id") != context.workspace_id:
            _blocked("goal_protocol_workspace_mismatch")
        binding_status, _binding = assess_native_goal_binding(
            run, protocol, context.workspace_id
        )
        if binding_status != "NATIVE_BOUND":
            _blocked("native_goal_binding_inconsistent")
        if _criterion_from_run(run) != criterion:
            _blocked("preflight_goal_differs_from_snapshot")
        subjects = snapshot.get("subjects")
        if not isinstance(subjects, dict) or criterion["claim_revision_id"] not in subjects:
            _unresolved("target_claim_not_in_snapshot")

    def _check_subject(
        self,
        snapshot: dict[str, Any], claim_id: str, criterion: dict[str, str],
        profile: QualificationProfile, admission_policy: KnowledgeAdmissionPolicy,
        path: frozenset[str], checked: set[str],
    ) -> None:
        if claim_id in path:
            _blocked("dependency_cycle")
        if claim_id in checked:
            return
        subjects = snapshot["subjects"]
        subject = subjects.get(claim_id)
        if not isinstance(subject, dict):
            _unresolved("dependency_subject_missing")
        workspace_id = snapshot["tenant_id"]
        claim = subject.get("claim")
        if not isinstance(claim, dict):
            _unresolved("claim_revision_missing")
        if claim.get("tenant_id") != workspace_id or claim.get("id") != claim_id:
            _blocked("claim_revision_identity_mismatch")
        semantic_hash = claim_semantic_hash(claim)
        if claim.get("semantic_hash") != semantic_hash:
            _blocked("claim_revision_semantic_drift")
        if claim_id == criterion["claim_revision_id"] and semantic_hash != criterion[
            "claim_semantic_hash"
        ]:
            _blocked("criterion_semantic_hash_mismatch")
        invalidations = subject.get("invalidations")
        if not isinstance(invalidations, list):
            _unresolved("invalidation_group_malformed")
        for decision in invalidations:
            self._check_invalidation_identity(decision, workspace_id, claim_id, semantic_hash)
            if decision["target_scope"] == "claim_revision":
                _blocked(f"invalidation_decision:{decision['id']}")
        profile_id = criterion["profile_id"]
        binding = subject.get("binding")
        if binding is None:
            _unresolved("current_use_binding_missing")
        if not isinstance(binding, dict) or (
            binding.get("tenant_id") != workspace_id
            or binding.get("claim_revision_id") != claim_id
            or binding.get("profile_id") != profile_id
            or binding.get("use_scope") != "knowledge"
        ):
            _blocked("current_use_binding_identity_mismatch")
        if binding.get("state") != "current":
            _blocked("current_use_binding_not_current")
        receipt = _one(subject.get("receipts"), "qualification_receipt_missing")
        if receipt.get("tenant_id") != workspace_id or (
            receipt.get("id") != binding.get("qualification_receipt_id")
            or receipt.get("claim_revision_id") != claim_id
            or receipt.get("claim_semantic_hash") != semantic_hash
            or receipt.get("profile_id") != profile_id
        ):
            _blocked("qualification_receipt_identity_mismatch")
        evaluation = _one(subject.get("evaluations"), "qualification_evaluation_missing")
        self._check_qualification(profile, receipt, evaluation, workspace_id)
        admission = _one(subject.get("admissions"), "knowledge_admission_receipt_missing")
        self._check_admission(
            admission, binding, receipt, workspace_id, admission_policy
        )
        for decision in invalidations:
            if (
                decision["target_scope"] == "receipt"
                and decision["target_receipt_id"] == receipt["id"]
            ):
                _blocked(f"invalidation_decision:{decision['id']}")
        closure = evaluation["evidence_closure"]
        nodes = closure["nodes"]
        artifacts = self._by_id(subject.get("artifacts"), workspace_id)
        sources = self._by_id(subject.get("sources"), workspace_id)
        dependencies: dict[str, dict[str, Any]] = {}
        for node in nodes:
            if not isinstance(node, dict):
                _unresolved("evidence_node_malformed")
            kind, node_id = node.get("node_type"), node.get("node_id")
            frozen = node.get("payload")
            if not isinstance(node_id, str) or not isinstance(frozen, dict):
                _unresolved("evidence_node_malformed")
            if kind == "artifact":
                artifact = artifacts.get(node_id)
                if artifact is None:
                    _unresolved("evidence_artifact_missing")
                content = artifact.get("content_text") or artifact.get("uri") or ""
                if not isinstance(content, str):
                    _unresolved("evidence_artifact_content_malformed")
                if (
                    artifact.get("content_hash") != node.get("content_hash")
                    or hashlib.sha256(content.encode("utf-8")).hexdigest()
                    != node.get("content_hash")
                    or any(artifact.get(key) != value for key, value in frozen.items())
                ):
                    _blocked("evidence_artifact_changed")
            elif kind == "source":
                source = sources.get(node_id)
                if source is None:
                    _unresolved("evidence_source_missing")
                if (
                    source.get("content_hash") != node.get("content_hash")
                    or any(source.get(key) != value for key, value in frozen.items())
                ):
                    _blocked("evidence_source_changed")
            elif kind == "dependency_binding":
                if node_id in dependencies:
                    _unresolved("duplicate_dependency_node")
                dependencies[node_id] = frozen
            elif kind == "claim_revision" and node_id == claim_id:
                if node.get("content_hash") != semantic_hash:
                    _blocked("closure_claim_semantic_hash_mismatch")
        proof_decisions = [
            decision for decision in invalidations
            if decision["target_scope"] == "proof_attempt"
        ]
        if proof_decisions:
            selected_attempt_id = self._selected_kernel_attempt_id(receipt, nodes)
            for decision in proof_decisions:
                if decision.get("target_attempt_id") == selected_attempt_id:
                    _blocked(f"invalidation_decision:{decision['id']}")
        declared = claim.get("dependency_claim_ids") or []
        if not isinstance(declared, list) or set(declared) != set(dependencies):
            _blocked("dependency_closure_mismatch")
        for dependency_id, frozen in sorted(dependencies.items()):
            dependent = subjects.get(dependency_id)
            if not isinstance(dependent, dict) or not isinstance(dependent.get("binding"), dict):
                _unresolved("dependency_binding_missing")
            current = dependent["binding"]
            if (
                current.get("state") != "current"
                or current.get("qualification_receipt_id")
                != frozen.get("qualification_receipt_id")
            ):
                _blocked("dependency_binding_changed")
            self._check_subject(
                snapshot, dependency_id, criterion, profile, admission_policy,
                path | {claim_id}, checked,
            )
        checked.add(claim_id)

    @staticmethod
    def _selected_kernel_attempt_id(
        receipt: dict[str, Any], nodes: list[dict[str, Any]],
    ) -> str:
        """Resolve the kernel attempt selected by statement identity evidence."""
        criteria = receipt.get("criteria")
        if not isinstance(criteria, list):
            _unresolved("selected_proof_attempt_ambiguous")
        selected_artifact_ids: set[str] = set()
        for criterion in criteria:
            if not isinstance(criterion, dict):
                _unresolved("selected_proof_attempt_ambiguous")
            if criterion.get("code") != "statement_identity":
                continue
            evidence_ids = criterion.get("evidence_node_ids")
            if not isinstance(evidence_ids, list) or any(
                not isinstance(item, str) or not item for item in evidence_ids
            ):
                _unresolved("selected_proof_attempt_ambiguous")
            selected_artifact_ids.update(evidence_ids)
        if not selected_artifact_ids:
            _unresolved("selected_proof_attempt_ambiguous")
        selected_attempt_ids: set[str] = set()
        for node in nodes:
            if node["node_type"] != "verification_attempt":
                continue
            payload = node["payload"]
            if payload.get("validation_modality") != "kernel_check":
                continue
            artifact_ids = payload.get("artifact_ids")
            if not isinstance(artifact_ids, list) or any(
                not isinstance(item, str) for item in artifact_ids
            ):
                _unresolved("selected_proof_attempt_ambiguous")
            if selected_artifact_ids.intersection(artifact_ids):
                selected_attempt_ids.add(node["node_id"])
        if len(selected_attempt_ids) != 1:
            _unresolved("selected_proof_attempt_ambiguous")
        return next(iter(selected_attempt_ids))

    @staticmethod
    def _check_invalidation_identity(
        decision: Any, workspace_id: str, claim_id: str, semantic_hash: str,
    ) -> None:
        if not isinstance(decision, dict) or decision.get("tenant_id") != workspace_id:
            _blocked("invalidation_identity_mismatch")
        if decision.get("target_claim_revision_id") != claim_id:
            _blocked("invalidation_target_mismatch")
        if decision.get("target_claim_semantic_hash") != semantic_hash:
            _blocked("invalidation_semantic_hash_mismatch")
        if decision.get("target_scope") not in {
            "claim_revision", "receipt", "proof_attempt",
        }:
            _unresolved("invalidation_scope_unknown")
        if decision.get("decision_hash") != canonical_hash({
            field: decision[field] for field in _INVALIDATION_HASH_FIELDS
        }):
            _blocked("invalidation_decision_hash_mismatch")

    @staticmethod
    def _by_id(rows: Any, workspace_id: str) -> dict[str, dict[str, Any]]:
        if not isinstance(rows, list):
            _unresolved("snapshot_row_group_malformed")
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                _unresolved("snapshot_row_malformed")
            if row.get("tenant_id") != workspace_id:
                _blocked("cross_workspace_source_row")
            if row["id"] in result:
                _unresolved("duplicate_snapshot_source_row")
            result[row["id"]] = row
        return result

    @staticmethod
    def _check_qualification(
        profile: Any, receipt: dict[str, Any], evaluation: dict[str, Any],
        workspace_id: str,
    ) -> None:
        if evaluation.get("tenant_id") != workspace_id or (
            evaluation.get("id") != receipt.get("evaluation_id")
            or evaluation.get("claim_revision_id") != receipt.get("claim_revision_id")
            or evaluation.get("claim_semantic_hash") != receipt.get("claim_semantic_hash")
            or evaluation.get("profile_id") != receipt.get("profile_id")
        ):
            _blocked("qualification_evaluation_identity_mismatch")
        policy_hash = canonical_hash({
            "profile_hash": profile.content_hash,
            "policy_version": profile.policy_version,
        })
        if (
            evaluation.get("profile_snapshot") != profile.as_dict()
            or evaluation.get("profile_hash") != profile.content_hash
            or evaluation.get("policy_version") != profile.policy_version
            or evaluation.get("policy_hash") != policy_hash
            or evaluation.get("profile_version") != profile.version
            or receipt.get("profile_version") != profile.version
            or receipt.get("policy_version") != profile.policy_version
            or receipt.get("policy_hash") != policy_hash
        ):
            _blocked("qualification_profile_or_policy_changed")
        closure = evaluation.get("evidence_closure")
        if not isinstance(closure, dict) or (
            closure.get("contract_version") != EVIDENCE_CLOSURE_VERSION
            or closure.get("claim_revision_id") != receipt["claim_revision_id"]
            or closure.get("claim_semantic_hash") != receipt["claim_semantic_hash"]
            or not isinstance(closure.get("nodes"), list)
            or not isinstance(closure.get("edges"), list)
            or canonical_hash(closure) != evaluation.get("evidence_closure_hash")
            or evaluation.get("evidence_closure_hash") != receipt.get("evidence_closure_hash")
        ):
            _blocked("qualification_evidence_closure_changed")
        if (
            evaluation.get("verdict") != "ADMITTED"
            or receipt.get("verdict") != "ADMITTED"
            or evaluation.get("criteria") != receipt.get("criteria")
            or evaluation.get("blockers") != receipt.get("blockers")
            or evaluation.get("evidence_vector") != receipt.get("evidence_vector")
            or evaluation.get("independence_summary") != receipt.get("independence_summary")
            or receipt.get("blockers") != []
        ):
            _blocked("qualification_decision_mismatch")
        portable = {
            "qualification_receipt_id": receipt["id"],
            "profile": receipt["profile_id"],
            **{field: receipt[field] for field in _RECEIPT_FIELDS},
        }
        if receipt.get("receipt_hash") != canonical_hash(portable):
            _blocked("qualification_receipt_hash_mismatch")

    @staticmethod
    def _check_admission(
        admission: dict[str, Any], binding: dict[str, Any],
        receipt: dict[str, Any], workspace_id: str,
        policy: KnowledgeAdmissionPolicy,
    ) -> None:
        if admission.get("tenant_id") != workspace_id or (
            admission.get("id") != binding.get("knowledge_admission_receipt_id")
            or admission.get("qualification_receipt_id") != receipt["id"]
            or admission.get("qualification_receipt_hash") != receipt["receipt_hash"]
            or admission.get("claim_revision_id") != receipt["claim_revision_id"]
            or admission.get("claim_semantic_hash") != receipt["claim_semantic_hash"]
            or admission.get("profile_id") != receipt["profile_id"]
            or admission.get("profile_version") != receipt["profile_version"]
            or admission.get("use_scope") != "knowledge"
        ):
            _blocked("knowledge_admission_identity_mismatch")
        if (
            admission.get("contract_version") != KNOWLEDGE_ADMISSION_CONTRACT_VERSION
            or admission.get("admission_policy_id") != policy.policy_id
            or admission.get("admission_policy_version") != policy.version
            or admission.get("admission_policy_hash") != policy.content_hash
        ):
            _blocked("knowledge_admission_policy_mismatch")
        portable = {
            "knowledge_admission_receipt_id": admission["id"],
            **{field: admission[field] for field in _ADMISSION_FIELDS},
        }
        if admission.get("receipt_hash") != canonical_hash(portable):
            _blocked("knowledge_admission_receipt_hash_mismatch")
