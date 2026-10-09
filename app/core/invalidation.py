"""Versioned inputs for verified, local qualification invalidation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

NOTICE_VERIFICATION_METHOD = "https_pinned_commit_sha256"
INVALIDATION_POLICY_ID = "invalidation.pinned-source-admin.v1"


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
