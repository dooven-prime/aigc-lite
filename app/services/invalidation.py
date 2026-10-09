"""Authenticate one pinned withdrawal source, then decide local invalidation.

The first source policy verifies bytes served by GitHub over HTTPS at an exact
commit. This is origin-and-byte binding, not independent Git-tree/signature
verification or an automatic assessment of mathematical truth.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import httpx

from ..core.artifacts import ArtifactDraft, ArtifactKind
from ..core.contracts import RequestContext
from ..core.errors import InvalidEvidenceError, ResourceNotFoundError, UpstreamRequestError
from ..core.invalidation import (
    INVALIDATION_POLICY_ID,
    NOTICE_VERIFICATION_METHOD,
    InvalidationDecisionDraft,
    InvalidationReason,
    InvalidationScope,
    NoticeVerificationDraft,
)
from ..core.qualification import canonical_hash, claim_semantic_hash
from ..database import get_repository
from .artifacts import ArtifactService

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_MAX_NOTICE_BYTES = 256_000
_POLICY_HASH = canonical_hash(
    {
        "policy_id": INVALIDATION_POLICY_ID,
        "authority": "workspace_admin_with_qualification_invalidate_scope",
        "source": "openai/math/history.md@40-char-commit",
        "scopes": {
            "claim_revision": "WITHDRAWN_UPSTREAM_CONSTRUCTION",
            "receipt": "RECEIPT_DEFECT",
            "proof_attempt": "PROOF_INVALIDATED",
        },
    }
)


def _artifact_bytes_match(artifact: dict) -> bool:
    payload = artifact.get("content_text") or artifact.get("uri") or ""
    return bool(
        payload
        and hashlib.sha256(payload.encode("utf-8")).hexdigest() == artifact.get("content_hash")
    )


def _fetch_pinned_notice(url: str) -> bytes:
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise UpstreamRequestError("Pinned withdrawal notice is unavailable")
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > _MAX_NOTICE_BYTES:
                        raise InvalidEvidenceError("notice", "Withdrawal notice exceeds size limit")
                return bytes(payload)
    except httpx.HTTPError as exc:
        raise UpstreamRequestError("Pinned withdrawal notice fetch failed") from exc


class InvalidationService:
    def __init__(
        self,
        repository_provider: Callable = get_repository,
        *,
        artifact_service: ArtifactService | None = None,
        source_fetcher: Callable[[str], bytes] = _fetch_pinned_notice,
    ) -> None:
        self._repository_provider = repository_provider
        self._artifacts = artifact_service or ArtifactService(
            repository_provider=repository_provider
        )
        self._source_fetcher = source_fetcher

    @staticmethod
    def _require_authority(context: RequestContext) -> None:
        if not context.principal_id or "qualification:invalidate" not in context.scopes:
            raise InvalidEvidenceError(
                "authority", "Invalidation requires an authenticated workspace administrator"
            )

    def verify_notice(self, context: RequestContext, draft: NoticeVerificationDraft) -> dict:
        self._require_authority(context)
        if not _COMMIT.fullmatch(draft.source_commit):
            raise InvalidEvidenceError("source_commit", "An exact 40-character commit is required")
        url = f"https://raw.githubusercontent.com/openai/math/{draft.source_commit}/history.md"
        payload = self._source_fetcher(url)
        if not payload or len(payload) > _MAX_NOTICE_BYTES:
            raise InvalidEvidenceError("notice", "Withdrawal notice is empty or too large")
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidEvidenceError("notice", "Withdrawal notice must be UTF-8") from exc
        source_hash = hashlib.sha256(payload).hexdigest()
        artifact = self._artifacts.create_artifact(
            context,
            ArtifactDraft(
                name=f"openai-math-history-{draft.source_commit}.md",
                kind=ArtifactKind.TEXT,
                media_type="text/markdown",
                content_text=content,
                metadata={
                    "role": "withdrawal_notice",
                    "source_repository": "openai/math",
                    "source_commit": draft.source_commit,
                    "source_path": "history.md",
                },
            ),
        )
        if artifact["content_hash"] != source_hash:
            raise InvalidEvidenceError(
                "notice", "Stored Artifact bytes differ from the pinned source snapshot"
            )
        verified_at = datetime.now(UTC).isoformat()
        verification = {
            "id": str(uuid4()),
            "source_repository": "openai/math",
            "source_commit": draft.source_commit,
            "source_path": "history.md",
            "source_artifact_id": artifact["id"],
            "source_hash": source_hash,
            "verification_method": NOTICE_VERIFICATION_METHOD,
            "requested_by": context.principal_id,
            "verified_at": verified_at,
        }
        verification["verification_hash"] = canonical_hash(verification)
        return self._repository_provider().record_notice_verification(
            context.workspace_id, verification
        )

    def decide(self, context: RequestContext, draft: InvalidationDecisionDraft) -> dict:
        self._require_authority(context)
        repository = self._repository_provider()
        verification = repository.get_notice_verification(
            context.workspace_id, draft.notice_verification_id
        )
        if verification is None:
            raise ResourceNotFoundError("notice_verification", draft.notice_verification_id)
        notice = repository.get_artifact(context.workspace_id, verification["source_artifact_id"])
        if (
            notice is None
            or notice["content_hash"] != verification["source_hash"]
            or not _artifact_bytes_match(notice)
        ):
            raise InvalidEvidenceError("notice", "Verified notice Artifact is missing or changed")
        claim = repository.get_research_claim(context.workspace_id, draft.target_claim_revision_id)
        if claim is None:
            raise ResourceNotFoundError("research_claim", draft.target_claim_revision_id)
        if (
            claim["semantic_hash"] != draft.target_claim_semantic_hash
            or claim_semantic_hash(claim) != draft.target_claim_semantic_hash
        ):
            raise InvalidEvidenceError(
                "target_claim_semantic_hash", "Target ClaimRevision semantic hash differs"
            )
        try:
            target_scope = InvalidationScope(draft.target_scope)
            reason = InvalidationReason(draft.reason_code)
        except ValueError as exc:
            raise InvalidEvidenceError(
                "invalidation", "Unsupported target scope or reason"
            ) from exc
        if target_scope is InvalidationScope.CLAIM_REVISION:
            if (
                draft.target_receipt_id is not None
                or draft.target_attempt_id is not None
                or reason is not InvalidationReason.WITHDRAWN_UPSTREAM_CONSTRUCTION
            ):
                raise InvalidEvidenceError("target_scope", "Invalid claim-level target")
        elif (
            draft.target_receipt_id is None
            or (
                target_scope is InvalidationScope.RECEIPT
                and (
                    reason is not InvalidationReason.RECEIPT_DEFECT
                    or draft.target_attempt_id is not None
                )
            )
            or (
                target_scope is InvalidationScope.PROOF_ATTEMPT
                and (
                    reason is not InvalidationReason.PROOF_INVALIDATED
                    or not draft.target_attempt_id
                )
            )
        ):
            raise InvalidEvidenceError("target_scope", "Receipt/attempt target is incomplete")
        receipt = None
        if draft.target_receipt_id is not None:
            receipt = repository.get_qualification_receipt(
                context.workspace_id, draft.target_receipt_id
            )
            if receipt is None:
                raise ResourceNotFoundError("qualification_receipt", draft.target_receipt_id)
            if (
                receipt["claim_revision_id"] != claim["id"]
                or receipt["claim_semantic_hash"] != draft.target_claim_semantic_hash
            ):
                raise InvalidEvidenceError(
                    "target_receipt_id", "Receipt belongs to another revision"
                )
        if draft.target_attempt_id is not None:
            attempts = repository.list_research_verification_attempts(
                context.workspace_id, claim["id"]
            )
            attempt = next(
                (item for item in attempts if item["id"] == draft.target_attempt_id), None
            )
            if attempt is None:
                raise InvalidEvidenceError(
                    "target_attempt_id", "Attempt belongs to another revision"
                )
            if attempt.get("validation_modality") != "kernel_check":
                raise InvalidEvidenceError(
                    "target_attempt_id", "Only a bound kernel attempt may be targeted"
                )
            evaluations = repository.list_qualification_evaluations(
                context.workspace_id, claim["id"], receipt["profile_id"]
            )
            evaluation = next(
                (item for item in evaluations if item["id"] == receipt["evaluation_id"]), None
            )
            if evaluation is None or not any(
                item["node_type"] == "verification_attempt" and item["node_id"] == attempt["id"]
                for item in evaluation["evidence_closure"].get("nodes", [])
            ):
                raise InvalidEvidenceError(
                    "target_attempt_id", "Attempt is not in the target receipt's closure"
                )
            selected_artifact_ids = {
                artifact_id
                for criterion in receipt.get("criteria", [])
                if criterion.get("code") == "statement_identity"
                for artifact_id in criterion.get("evidence_node_ids", [])
            }
            if not selected_artifact_ids.intersection(attempt.get("artifact_ids") or []):
                raise InvalidEvidenceError(
                    "target_attempt_id", "Attempt was not selected by the target receipt"
                )
            matching_attempts = {
                node["node_id"]
                for node in evaluation["evidence_closure"].get("nodes", [])
                if node.get("node_type") == "verification_attempt"
                and node.get("payload", {}).get("validation_modality") == "kernel_check"
                and selected_artifact_ids.intersection(
                    node.get("payload", {}).get("artifact_ids") or []
                )
            }
            if matching_attempts != {attempt["id"]}:
                raise InvalidEvidenceError(
                    "target_attempt_id", "Selected proof attempt is ambiguous"
                )
        if not draft.rationale.strip() or len(draft.rationale) > 4_000:
            raise InvalidEvidenceError("rationale", "A bounded decision rationale is required")
        evidence_ids = tuple(
            dict.fromkeys((verification["source_artifact_id"], *draft.evidence_artifact_ids))
        )
        if len(evidence_ids) > 100:
            raise InvalidEvidenceError("evidence_artifact_ids", "Too many evidence Artifacts")
        evidence_hashes = {}
        for artifact_id in evidence_ids:
            artifact = repository.get_artifact(context.workspace_id, artifact_id)
            if artifact is None:
                raise ResourceNotFoundError("artifact", artifact_id)
            if not _artifact_bytes_match(artifact):
                raise InvalidEvidenceError(
                    "evidence_artifact_ids", "Evidence Artifact hash differs"
                )
            evidence_hashes[artifact_id] = artifact["content_hash"]
        decided_at = datetime.now(UTC).isoformat()
        decision = {
            "id": str(uuid4()),
            "notice_verification_id": verification["id"],
            "target_claim_revision_id": claim["id"],
            "target_claim_semantic_hash": draft.target_claim_semantic_hash,
            "target_receipt_id": draft.target_receipt_id,
            "target_attempt_id": draft.target_attempt_id,
            "target_scope": target_scope.value,
            "reason_code": reason.value,
            "evidence_artifact_ids": list(evidence_ids),
            "evidence_artifact_hashes": evidence_hashes,
            "policy_id": INVALIDATION_POLICY_ID,
            "policy_hash": _POLICY_HASH,
            "rationale": draft.rationale.strip(),
            "decided_by": context.principal_id,
            "effect_state": (
                "revoked" if target_scope is InvalidationScope.CLAIM_REVISION else "stale"
            ),
            "decided_at": decided_at,
        }
        decision["decision_hash"] = canonical_hash(decision)
        return repository.apply_invalidation_decision(context.workspace_id, decision)

    def list_decisions(self, context: RequestContext, claim_id: str) -> list[dict]:
        return self._repository_provider().list_invalidation_decisions(
            context.workspace_id, claim_id
        )

    def get_notice(self, context: RequestContext, verification_id: str) -> dict:
        value = self._repository_provider().get_notice_verification(
            context.workspace_id, verification_id
        )
        if value is None:
            raise ResourceNotFoundError("notice_verification", verification_id)
        return value
