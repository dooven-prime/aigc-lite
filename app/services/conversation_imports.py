"""Preview and atomically commit external conversations as candidate memory."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError as SQLAlchemyIntegrityError

from ..core.contracts import RequestContext
from ..core.conversation_imports import (
    CANDIDATE_ADMISSION_STATE,
    CONVERSATION_IMPORT_CONTRACT_VERSION,
    ConversationImportResult,
)
from ..core.errors import InvalidConversationImportError, ResourceNotFoundError
from ..core.qualification import canonical_hash
from ..database import get_repository
from ..repository import Repository
from .conversation_import_registry import ConversationImportRegistry

RepositoryProvider = Callable[[], Repository]

_MAX_CONVERSATIONS = 20_000
_MAX_MESSAGES = 200_000
_MAX_CONTENT_CHARS = 500_000
_MAX_ID_CHARS = 1_000
_MAX_TITLE_CHARS = 2_000
_MAX_MODEL_CHARS = 500
_MAX_METADATA_CHARS = 250_000
_MAX_WARNINGS = 2_000


class ConversationImportService:
    def __init__(
        self,
        *,
        registry: ConversationImportRegistry,
        repository_provider: RepositoryProvider = get_repository,
    ) -> None:
        self._registry = registry
        self._repository_provider = repository_provider

    def list_importers(self) -> list[dict]:
        return self._registry.list()

    def preview(
        self,
        context: RequestContext,
        *,
        importer_id: str,
        source_name: str,
        source_bytes: bytes,
    ) -> dict:
        del context
        normalized = self._normalize(importer_id, source_name, source_bytes)
        return self._preview_projection(normalized)

    def commit(
        self,
        context: RequestContext,
        *,
        importer_id: str,
        source_name: str,
        source_bytes: bytes,
        expected_preview_hash: str,
    ) -> dict:
        normalized = self._normalize(importer_id, source_name, source_bytes)
        if not secrets.compare_digest(
            normalized["preview_hash"], expected_preview_hash.strip()
        ):
            raise InvalidConversationImportError(
                "expected_preview_hash",
                "Import source does not match the reviewed preview",
            )
        repository = self._repository_provider()
        existing = repository.get_conversation_import_by_source(
            context.workspace_id,
            normalized["importer_id"],
            normalized["source_content_hash"],
        )
        if existing is not None:
            existing["deduplicated"] = True
            return existing
        source_artifact_id = normalized["source_artifact_id"]
        try:
            batch = repository.create_conversation_import(
                context.workspace_id,
                {
                    **normalized,
                    "requested_by": context.principal_id,
                    "artifact": {
                        "id": source_artifact_id,
                        "series_id": source_artifact_id,
                        "version": 1,
                        "run_id": None,
                        "step_id": None,
                        "name": normalized["source_name"],
                        "kind": "json",
                        "media_type": "application/json",
                        "content_text": normalized["source_text"],
                        "uri": None,
                        "content_hash": normalized["source_content_hash"],
                        "size_bytes": len(source_bytes),
                        "metadata": {
                            "artifact_role": "conversation_import_source",
                            "contract_version": CONVERSATION_IMPORT_CONTRACT_VERSION,
                            "importer_id": normalized["importer_id"],
                            "importer_version": normalized["importer_version"],
                            "admission_state": CANDIDATE_ADMISSION_STATE,
                        },
                    },
                },
            )
        except (sqlite3.IntegrityError, SQLAlchemyIntegrityError):
            # A concurrent identical commit may win the unique source identity.
            # Only treat it as idempotent when that exact closure is now visible;
            # unrelated integrity errors retain their original failure.
            existing = repository.get_conversation_import_by_source(
                context.workspace_id,
                normalized["importer_id"],
                normalized["source_content_hash"],
            )
            if existing is None:
                raise
            existing["deduplicated"] = True
            return existing
        batch["deduplicated"] = False
        return batch

    def get(
        self, context: RequestContext, batch_id: str, *, include_messages: bool = False
    ) -> dict:
        value = self._repository_provider().get_conversation_import(
            context.workspace_id, batch_id
        )
        if value is None:
            raise ResourceNotFoundError("conversation_import", batch_id)
        return value if include_messages else self._summary_projection(value)

    def list(self, context: RequestContext, limit: int = 50) -> list[dict]:
        return self._repository_provider().list_conversation_imports(
            context.workspace_id, max(1, min(limit, 200))
        )

    def _normalize(
        self, importer_id: str, source_name: str, source_bytes: bytes
    ) -> dict[str, Any]:
        if not source_bytes:
            raise InvalidConversationImportError("file", "Import source is empty")
        try:
            source_text = source_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidConversationImportError(
                "file", "Import source must be UTF-8 JSON"
            ) from exc
        try:
            payload = json.loads(
                source_text.lstrip("\ufeff"), object_pairs_hook=self._unique_object
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidConversationImportError(
                "file", "Import source is not valid unambiguous JSON"
            ) from exc
        importer = self._registry.get(importer_id.strip())
        parsed = importer.parse(payload)
        self._validate(parsed)
        normalized_conversations = [item.as_dict() for item in parsed.conversations]
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        warnings = list(parsed.warnings)
        preview_hash = canonical_hash(
            {
                "contract_version": CONVERSATION_IMPORT_CONTRACT_VERSION,
                "importer_id": importer.importer_id,
                "importer_version": importer.version,
                "source_content_hash": source_hash,
                "warnings": warnings,
                "conversations": normalized_conversations,
            }
        )
        safe_name = Path(
            (source_name or "conversations.json").replace("\\", "/")
        ).name[:255]
        return {
            "contract_version": CONVERSATION_IMPORT_CONTRACT_VERSION,
            "importer_id": importer.importer_id,
            "importer_version": importer.version,
            "source_name": safe_name or "conversations.json",
            "source_text": source_text,
            "source_content_hash": source_hash,
            "source_artifact_id": self._new_id(),
            "preview_hash": preview_hash,
            "status": "committed",
            "admission_state": CANDIDATE_ADMISSION_STATE,
            "conversation_count": len(normalized_conversations),
            "message_count": sum(
                len(item["messages"]) for item in normalized_conversations
            ),
            "warning_count": len(warnings),
            "warnings": warnings,
            "conversations": normalized_conversations,
        }

    @staticmethod
    def _new_id() -> str:
        from uuid import uuid4

        return str(uuid4())

    @staticmethod
    def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"Duplicate JSON key: {key}")
            value[key] = item
        return value

    def _validate(self, parsed: ConversationImportResult) -> None:
        if not parsed.conversations:
            raise InvalidConversationImportError(
                "conversations", "Import source contains no supported conversations"
            )
        if len(parsed.conversations) > _MAX_CONVERSATIONS:
            raise InvalidConversationImportError(
                "conversations", f"Import exceeds {_MAX_CONVERSATIONS} conversations"
            )
        if len(parsed.warnings) > _MAX_WARNINGS:
            raise InvalidConversationImportError(
                "warnings", f"Import exceeds {_MAX_WARNINGS} normalization warnings"
            )
        conversation_ids: set[str] = set()
        message_count = 0
        for conversation in parsed.conversations:
            self._bounded("conversation.external_id", conversation.external_id, _MAX_ID_CHARS)
            self._bounded("conversation.title", conversation.title, _MAX_TITLE_CHARS)
            if conversation.external_id in conversation_ids:
                raise InvalidConversationImportError(
                    "conversation.external_id", "Conversation external ids must be unique"
                )
            conversation_ids.add(conversation.external_id)
            self._json_bounded("conversation.metadata", conversation.metadata)
            message_ids: set[str] = set()
            for message in conversation.messages:
                message_count += 1
                self._bounded("message.external_id", message.external_id, _MAX_ID_CHARS)
                self._bounded("message.role", message.role, 100)
                self._bounded("message.content_type", message.content_type, 200)
                self._bounded(
                    "message.content", message.content, _MAX_CONTENT_CHARS, required=False
                )
                if message.model is not None:
                    self._bounded("message.model", message.model, _MAX_MODEL_CHARS)
                if message.external_id in message_ids:
                    raise InvalidConversationImportError(
                        "message.external_id",
                        "Message external ids must be unique within a conversation",
                    )
                message_ids.add(message.external_id)
                self._json_bounded("message.citations", list(message.citations))
                self._json_bounded("message.attachments", list(message.attachments))
                self._json_bounded("message.metadata", message.metadata)
            for message in conversation.messages:
                if (
                    message.parent_external_id is not None
                    and message.parent_external_id not in message_ids
                ):
                    raise InvalidConversationImportError(
                        "message.parent_external_id",
                        "Message parent must reference another preserved message",
                    )
        if message_count > _MAX_MESSAGES:
            raise InvalidConversationImportError(
                "messages", f"Import exceeds {_MAX_MESSAGES} messages"
            )
        if message_count == 0:
            raise InvalidConversationImportError(
                "messages", "Import source contains no supported messages"
            )

    @staticmethod
    def _bounded(
        field: str, value: str, maximum: int, *, required: bool = True
    ) -> None:
        if required and not value.strip():
            raise InvalidConversationImportError(field, f"{field} is required")
        if len(value) > maximum:
            raise InvalidConversationImportError(
                field, f"{field} exceeds {maximum} characters"
            )

    @staticmethod
    def _json_bounded(field: str, value: object) -> None:
        try:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise InvalidConversationImportError(
                field, f"{field} must be JSON-compatible"
            ) from exc
        if len(rendered) > _MAX_METADATA_CHARS:
            raise InvalidConversationImportError(
                field, f"{field} exceeds {_MAX_METADATA_CHARS} characters"
            )

    @staticmethod
    def _preview_projection(value: dict[str, Any]) -> dict:
        return {
            key: value[key]
            for key in (
                "contract_version",
                "importer_id",
                "importer_version",
                "source_name",
                "source_content_hash",
                "preview_hash",
                "admission_state",
                "conversation_count",
                "message_count",
                "warning_count",
                "warnings",
            )
        } | {
            "sample": [
                {
                    "external_id": item["external_id"],
                    "title": item["title"],
                    "message_count": len(item["messages"]),
                    "canonical_message_count": sum(
                        1 for message in item["messages"] if message["is_canonical"]
                    ),
                }
                for item in value["conversations"][:10]
            ]
        }

    @staticmethod
    def _summary_projection(value: dict[str, Any]) -> dict:
        result = dict(value)
        result["conversations"] = [
            {
                key: item[key]
                for key in (
                    "id",
                    "external_id",
                    "title",
                    "original_created_at",
                    "original_updated_at",
                    "current_message_external_id",
                    "ordinal",
                    "created_at",
                )
            }
            | {"message_count": len(item.get("messages") or [])}
            for item in value.get("conversations") or []
        ]
        return result
