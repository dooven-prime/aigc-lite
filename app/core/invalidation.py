"""Versioned inputs for verified, local qualification invalidation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

NOTICE_VERIFICATION_METHOD = "https_pinned_commit_sha256"
INVALIDATION_POLICY_ID = "invalidation.pinned-source-admin.v1"
PROOF_SELECTION_VERSION = "statement-identity-kernel-attempt.v1"


def selected_kernel_attempt_ids_v1(
    criteria: list[dict[str, Any]], evidence_closure: dict[str, Any], profile_id: str
) -> frozenset[str]:
    """Find the exact kernel attempt selected by frozen qualification criteria.

    Current math profiles freeze the chosen attempt in the ``kernel_check``
    criterion. Older receipts listed all passing attempts (or the certificate
    Artifact); replay the frozen profile's selection order for those receipts.
    """

    selected_artifacts = {
        artifact_id
        for criterion in criteria
        if isinstance(criterion, dict) and criterion.get("code") == "statement_identity"
        for artifact_id in criterion.get("evidence_node_ids", [])
        if isinstance(artifact_id, str)
    }
    if not selected_artifacts or profile_id not in {
        "math.formal.v1", "math.formal.project.v2"
    }:
        return frozenset()
    matches = [
        node["node_id"]
        for node in evidence_closure.get("nodes", [])
        if isinstance(node, dict)
        and node.get("node_type") == "verification_attempt"
        and isinstance(node.get("node_id"), str)
        and isinstance(node.get("payload"), dict)
        and node["payload"].get("validation_modality") == "kernel_check"
        and selected_artifacts.intersection(node["payload"].get("artifact_ids") or [])
    ]
    if not matches:
        return frozenset()
    frozen_kernel_ids = {
        node_id
        for criterion in criteria
        if isinstance(criterion, dict) and criterion.get("code") == "kernel_check"
        for node_id in criterion.get("evidence_node_ids", [])
        if isinstance(node_id, str)
    }
    if len(frozen_kernel_ids) == 1 and next(iter(frozen_kernel_ids)) in matches:
        return frozenset(frozen_kernel_ids)
    # Historical math.formal.v1 chose the newest valid certificate, whereas
    # math.formal.project.v2 chose the first passing Comparator receipt.
    selected = matches[-1] if profile_id == "math.formal.v1" else matches[0]
    return frozenset({selected})


class InvalidationScope(StrEnum):
    CLAIM_REVISION = "claim_revision"
    RECEIPT = "receipt"
    PROOF_ATTEMPT = "proof_attempt"


class InvalidationReason(StrEnum):
    WITHDRAWN_UPSTREAM_CONSTRUCTION = "WITHDRAWN_UPSTREAM_CONSTRUCTION"
    RECEIPT_DEFECT = "RECEIPT_DEFECT"
    PROOF_INVALIDATED = "PROOF_INVALIDATED"


@dataclass(frozen=True, slots=True)
class NoticeVerificationDraft:
    source_commit: str


@dataclass(frozen=True, slots=True)
class InvalidationDecisionDraft:
    notice_verification_id: str
    target_claim_revision_id: str
    target_claim_semantic_hash: str
    target_scope: InvalidationScope
    reason_code: InvalidationReason
    rationale: str
    evidence_artifact_ids: tuple[str, ...] = ()
    target_receipt_id: str | None = None
    target_attempt_id: str | None = None
