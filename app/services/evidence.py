"""Application service for protocol, claim, receipt, review, and freeze records."""

from __future__ import annotations

import re
from collections.abc import Callable

from ..core.contracts import RequestContext
from ..core.errors import InvalidEvidenceError, ResourceNotFoundError
from ..core.evidence import (
    ClaimDraft,
    ExecutionReceiptDraft,
    FreezeManifestDraft,
    ProtocolDraft,
    ReviewDraft,
)
from ..database import get_repository
from ..redaction import redact, redact_record_text, redact_text
from ..repository import Repository

RepositoryProvider = Callable[[], Repository]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class EvidenceService:
    def __init__(self, repository_provider: RepositoryProvider = get_repository) -> None:
        self._repository_provider = repository_provider

    def create_protocol(
        self, context: RequestContext, draft: ProtocolDraft, *, content_hash: str
    ) -> dict:
        self._digest("content_hash", content_hash)
        values = {
            "profile": self._text("profile", draft.profile, 64),
            "name": self._text("name", draft.name, 200),
            "version": draft.version,
            "status": draft.status.value,
            "purpose": self._text("purpose", draft.purpose, 4_000),
            "scope": self._text("scope", draft.scope, 8_000),
            "completion_predicate": self._text(
                "completion_predicate", draft.completion_predicate, 4_000
            ),
            "stop_conditions": [
                self._text("stop_condition", item, 1_000)
                for item in draft.stop_conditions
            ],
            "budget": self._mapping("budget", draft.budget),
            "source_uri": self._optional_text("source_uri", draft.source_uri, 2_000),
            "content_hash": content_hash,
        }
        return self._repository_provider().create_evidence_protocol(
            context.workspace_id, values
        )

    def create_claim(
        self, context: RequestContext, protocol_id: str, draft: ClaimDraft
    ) -> dict:
        return self._repository_provider().create_evidence_claim(
            context.workspace_id,
            {
                "protocol_id": protocol_id,
                "statement": self._text("statement", draft.statement, 8_000),
                "resolution": draft.resolution.value,
                "scope": self._text("scope", draft.scope, 8_000),
                "evidence_refs": [
                    self._text("evidence_ref", item, 200)
                    for item in draft.evidence_refs
                ],
                "prohibited_upgrades": [
                    self._text("prohibited_upgrade", item, 1_000)
                    for item in draft.prohibited_upgrades
                ],
            },
        )

    def create_receipt(
        self, context: RequestContext, protocol_id: str, draft: ExecutionReceiptDraft
    ) -> dict:
        self._digest("input_digest", draft.input_digest)
        self._digest("output_digest", draft.output_digest)
        return self._repository_provider().create_execution_receipt(
            context.workspace_id,
            {
                "protocol_id": protocol_id,
                "run_id": draft.run_id,
                "status": self._text("status", draft.status, 64),
                "input_digest": draft.input_digest,
                "output_digest": draft.output_digest,
                "runtime": self._mapping("runtime", draft.runtime),
                "budget": self._mapping("budget", draft.budget),
                "artifact_ids": list(draft.artifact_ids),
                "metadata": self._mapping("metadata", draft.metadata),
            },
        )

    def create_review(
        self, context: RequestContext, receipt_id: str, draft: ReviewDraft
    ) -> dict:
        return self._repository_provider().create_evidence_review(
            context.workspace_id,
            {
                "receipt_id": receipt_id,
                "reviewer_kind": draft.reviewer_kind.value,
                "reviewer_id": self._optional_text(
                    "reviewer_id", draft.reviewer_id, 200
                ),
                "independent": draft.independent,
                "status": draft.status.value,
                "finding": self._text("finding", draft.finding, 8_000),
                "evidence_refs": [
                    self._text("evidence_ref", item, 200)
                    for item in draft.evidence_refs
                ],
            },
        )

    def create_freeze(
        self, context: RequestContext, protocol_id: str, draft: FreezeManifestDraft
    ) -> dict:
        self._digest("content_hash", draft.content_hash)
        return self._repository_provider().create_freeze_manifest(
            context.workspace_id,
            {
                "protocol_id": protocol_id,
                "name": self._text("name", draft.name, 200),
                "version": draft.version,
                "members": list(draft.members),
                "content_hash": draft.content_hash,
            },
        )

    def list_protocols(
        self,
        context: RequestContext,
        *,
        profile: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return self._repository_provider().list_evidence_protocols(
            context.workspace_id, limit, profile
        )

    def get_protocol(self, context: RequestContext, protocol_id: str) -> dict:
        value = self._repository_provider().get_evidence_protocol(
            context.workspace_id, protocol_id
        )
        if value is None:
            raise ResourceNotFoundError("evidence_protocol", protocol_id)
        return value

    @staticmethod
    def _text(field: str, value: str, maximum: int) -> str:
        cleaned = redact_record_text(value.strip(), max_chars=maximum)
        if not cleaned:
            raise InvalidEvidenceError(field, f"{field} is required")
        return cleaned

    @staticmethod
    def _optional_text(field: str, value: str | None, maximum: int) -> str | None:
        if value is None:
            return None
        cleaned = redact_text(value.strip())
        if len(cleaned) > maximum:
            raise InvalidEvidenceError(field, f"{field} exceeds {maximum} characters")
        return cleaned or None

    @staticmethod
    def _mapping(field: str, value: object) -> dict:
        cleaned = redact(value)
        if not isinstance(cleaned, dict):
            raise InvalidEvidenceError(field, f"{field} must be an object")
        return cleaned

    @staticmethod
    def _digest(field: str, value: str) -> None:
        if not _SHA256.fullmatch(value):
            raise InvalidEvidenceError(field, f"{field} must be a SHA-256 digest")
