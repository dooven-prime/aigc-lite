"""Relational persistence for immutable conversation import batches."""

from __future__ import annotations

import json
import uuid
from typing import Any

from ..core.qualification import canonical_hash
from .common import decode_list as _decode_list
from .common import decode_metadata as _decode_metadata
from .common import utc_now


def _decode_batch(row: Any) -> dict[str, Any]:
    value = dict(row)
    for field in ("importer_version", "conversation_count", "message_count", "warning_count"):
        value[field] = int(value.get(field) or 0)
    value["warnings"] = _decode_list(value.get("warnings"))
    return value


def _decode_conversation(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["ordinal"] = int(value.get("ordinal") or 0)
    value["metadata"] = _decode_metadata(value.get("metadata"))
    return value


def _decode_message(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["ordinal"] = int(value.get("ordinal") or 0)
    value["is_canonical"] = bool(value.get("is_canonical"))
    value["citations"] = _decode_list(value.get("citations"))
    value["attachments"] = _decode_list(value.get("attachments"))
    value["metadata"] = _decode_metadata(value.get("metadata"))
    return value


def _prepare_rows(
    tenant_id: str, values: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    created_at = utc_now()
    artifact_values = values["artifact"]
    artifact = {
        "id": artifact_values["id"],
        "tenant_id": tenant_id,
        "series_id": artifact_values["series_id"],
        "version": int(artifact_values["version"]),
        "run_id": artifact_values.get("run_id"),
        "step_id": artifact_values.get("step_id"),
        "name": artifact_values["name"],
        "kind": artifact_values["kind"],
        "media_type": artifact_values["media_type"],
        "content_text": artifact_values.get("content_text") or "",
        "uri": artifact_values.get("uri"),
        "content_hash": artifact_values["content_hash"],
        "size_bytes": int(artifact_values.get("size_bytes") or 0),
        "metadata": json.dumps(artifact_values.get("metadata") or {}, ensure_ascii=False),
        "created_at": created_at,
    }
    batch = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "contract_version": values["contract_version"],
        "importer_id": values["importer_id"],
        "importer_version": int(values["importer_version"]),
        "source_name": values["source_name"],
        "source_artifact_id": artifact["id"],
        "source_content_hash": values["source_content_hash"],
        "preview_hash": values["preview_hash"],
        "status": values["status"],
        "admission_state": values["admission_state"],
        "conversation_count": int(values["conversation_count"]),
        "message_count": int(values["message_count"]),
        "warning_count": int(values["warning_count"]),
        "warnings": json.dumps(values.get("warnings") or [], ensure_ascii=False),
        "requested_by": values.get("requested_by"),
        "created_at": created_at,
    }
    conversations: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    for conversation in values["conversations"]:
        conversation_id = str(uuid.uuid4())
        conversations.append(
            {
                "id": conversation_id,
                "tenant_id": tenant_id,
                "batch_id": batch["id"],
                "importer_id": batch["importer_id"],
                "external_id": conversation["external_id"],
                "title": conversation["title"],
                "original_created_at": conversation.get("original_created_at"),
                "normalized_created_at": conversation.get("normalized_created_at"),
                "original_updated_at": conversation.get("original_updated_at"),
                "normalized_updated_at": conversation.get("normalized_updated_at"),
                "current_message_external_id": conversation.get(
                    "current_message_external_id"
                ),
                "metadata": json.dumps(
                    conversation.get("metadata") or {}, ensure_ascii=False
                ),
                "ordinal": int(conversation["ordinal"]),
                "created_at": created_at,
            }
        )
        for message in conversation["messages"]:
            record_snapshot = {
                key: message.get(key)
                for key in (
                    "external_id",
                    "parent_external_id",
                    "role",
                    "content_type",
                    "content",
                    "original_created_at",
                    "normalized_created_at",
                    "model",
                    "citations",
                    "attachments",
                    "metadata",
                    "is_canonical",
                    "ordinal",
                )
            }
            messages.append(
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "batch_id": batch["id"],
                    "conversation_id": conversation_id,
                    "importer_id": batch["importer_id"],
                    "external_id": message["external_id"],
                    "parent_external_id": message.get("parent_external_id"),
                    "role": message["role"],
                    "content_type": message["content_type"],
                    "content": message["content"],
                    "original_created_at": message.get("original_created_at"),
                    "normalized_created_at": message.get("normalized_created_at"),
                    "model": message.get("model"),
                    "citations": json.dumps(
                        message.get("citations") or [], ensure_ascii=False
                    ),
                    "attachments": json.dumps(
                        message.get("attachments") or [], ensure_ascii=False
                    ),
                    "metadata": json.dumps(
                        message.get("metadata") or {}, ensure_ascii=False
                    ),
                    "record_hash": canonical_hash(record_snapshot),
                    "is_canonical": int(bool(message.get("is_canonical"))),
                    "ordinal": int(message["ordinal"]),
                    "created_at": created_at,
                }
            )
    return artifact, batch, conversations, messages


class SQLiteImportRepositoryMixin:
    """SQLite transaction spanning raw Artifact, batch, graph, and FTS triggers."""

    def create_conversation_import(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        artifact, batch, conversations, messages = _prepare_rows(tenant_id, values)
        with self._connect() as db:
            db.execute(
                "INSERT INTO artifacts(id, tenant_id, series_id, version, run_id, step_id, "
                "name, kind, media_type, content_text, uri, content_hash, size_bytes, metadata, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(artifact.values()),
            )
            db.execute(
                "INSERT INTO conversation_import_batches(id, tenant_id, contract_version, "
                "importer_id, importer_version, source_name, source_artifact_id, "
                "source_content_hash, preview_hash, status, admission_state, "
                "conversation_count, message_count, warning_count, warnings, requested_by, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(batch.values()),
            )
            db.executemany(
                "INSERT INTO conversation_import_conversations(id, tenant_id, batch_id, "
                "importer_id, external_id, title, original_created_at, normalized_created_at, "
                "original_updated_at, normalized_updated_at, current_message_external_id, "
                "metadata, ordinal, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [tuple(item.values()) for item in conversations],
            )
            if messages:
                db.executemany(
                    "INSERT INTO conversation_import_messages(id, tenant_id, batch_id, "
                    "conversation_id, importer_id, external_id, parent_external_id, role, "
                    "content_type, content, original_created_at, normalized_created_at, model, "
                    "citations, attachments, metadata, record_hash, is_canonical, ordinal, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [tuple(item.values()) for item in messages],
                )
        result = self.get_conversation_import(tenant_id, batch["id"])
        if result is None:  # pragma: no cover - committed row is read back immediately
            raise RuntimeError("Conversation import commit was not persisted")
        return result

    def get_conversation_import(
        self, tenant_id: str, batch_id: str
    ) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM conversation_import_batches WHERE tenant_id = ? AND id = ?",
                (tenant_id, batch_id),
            ).fetchone()
            if row is None:
                return None
            conversation_rows = db.execute(
                "SELECT * FROM conversation_import_conversations WHERE tenant_id = ? "
                "AND batch_id = ? ORDER BY ordinal, id",
                (tenant_id, batch_id),
            ).fetchall()
            message_rows = db.execute(
                "SELECT * FROM conversation_import_messages WHERE tenant_id = ? "
                "AND batch_id = ? ORDER BY conversation_id, ordinal, id",
                (tenant_id, batch_id),
            ).fetchall()
        messages_by_conversation: dict[str, list[dict]] = {}
        for message_row in message_rows:
            message = _decode_message(message_row)
            messages_by_conversation.setdefault(message["conversation_id"], []).append(
                message
            )
        conversations = []
        for conversation_row in conversation_rows:
            conversation = _decode_conversation(conversation_row)
            conversation["messages"] = messages_by_conversation.get(
                conversation["id"], []
            )
            conversations.append(conversation)
        result = _decode_batch(row)
        result["conversations"] = conversations
        return result

    def get_conversation_import_by_source(
        self, tenant_id: str, importer_id: str, source_content_hash: str
    ) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT id FROM conversation_import_batches WHERE tenant_id = ? "
                "AND importer_id = ? AND source_content_hash = ?",
                (tenant_id, importer_id, source_content_hash),
            ).fetchone()
        return (
            self.get_conversation_import(tenant_id, row["id"])
            if row is not None
            else None
        )

    def list_conversation_imports(
        self, tenant_id: str, limit: int = 50
    ) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM conversation_import_batches WHERE tenant_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (tenant_id, max(1, min(limit, 200))),
            ).fetchall()
        return [_decode_batch(row) for row in rows]


class PostgresImportRepositoryMixin:
    """PostgreSQL implementation with one database transaction per commit."""

    def create_conversation_import(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        from sqlalchemy import text

        artifact, batch, conversations, messages = _prepare_rows(tenant_id, values)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO artifacts(id, tenant_id, series_id, version, run_id, step_id, "
                    "name, kind, media_type, content_text, uri, content_hash, size_bytes, metadata, "
                    "created_at) VALUES (:id, :tenant_id, :series_id, :version, :run_id, :step_id, "
                    ":name, :kind, :media_type, :content_text, :uri, :content_hash, :size_bytes, "
                    ":metadata, :created_at)"
                ),
                artifact,
            )
            connection.execute(
                text(
                    "INSERT INTO conversation_import_batches(id, tenant_id, contract_version, "
                    "importer_id, importer_version, source_name, source_artifact_id, "
                    "source_content_hash, preview_hash, status, admission_state, conversation_count, "
                    "message_count, warning_count, warnings, requested_by, created_at) VALUES "
                    "(:id, :tenant_id, :contract_version, :importer_id, :importer_version, "
                    ":source_name, :source_artifact_id, :source_content_hash, :preview_hash, "
                    ":status, :admission_state, :conversation_count, :message_count, "
                    ":warning_count, :warnings, :requested_by, :created_at)"
                ),
                batch,
            )
            if conversations:
                connection.execute(
                    text(
                        "INSERT INTO conversation_import_conversations(id, tenant_id, batch_id, "
                        "importer_id, external_id, title, original_created_at, "
                        "normalized_created_at, original_updated_at, normalized_updated_at, "
                        "current_message_external_id, metadata, ordinal, created_at) VALUES "
                        "(:id, :tenant_id, :batch_id, :importer_id, :external_id, :title, "
                        ":original_created_at, :normalized_created_at, :original_updated_at, "
                        ":normalized_updated_at, :current_message_external_id, :metadata, "
                        ":ordinal, :created_at)"
                    ),
                    conversations,
                )
            if messages:
                connection.execute(
                    text(
                        "INSERT INTO conversation_import_messages(id, tenant_id, batch_id, "
                        "conversation_id, importer_id, external_id, parent_external_id, role, "
                        "content_type, content, original_created_at, normalized_created_at, model, "
                        "citations, attachments, metadata, record_hash, is_canonical, ordinal, "
                        "created_at) VALUES (:id, :tenant_id, :batch_id, :conversation_id, "
                        ":importer_id, :external_id, :parent_external_id, :role, :content_type, "
                        ":content, :original_created_at, :normalized_created_at, :model, "
                        ":citations, :attachments, :metadata, :record_hash, :is_canonical, "
                        ":ordinal, :created_at)"
                    ),
                    messages,
                )
        result = self.get_conversation_import(tenant_id, batch["id"])
        if result is None:  # pragma: no cover
            raise RuntimeError("Conversation import commit was not persisted")
        return result

    def get_conversation_import(
        self, tenant_id: str, batch_id: str
    ) -> dict | None:
        row = self._one(
            "SELECT * FROM conversation_import_batches WHERE tenant_id = :tenant_id "
            "AND id = :id",
            {"tenant_id": tenant_id, "id": batch_id},
        )
        if row is None:
            return None
        conversations = [
            _decode_conversation(item)
            for item in self._many(
                "SELECT * FROM conversation_import_conversations WHERE tenant_id = :tenant_id "
                "AND batch_id = :id ORDER BY ordinal, id",
                {"tenant_id": tenant_id, "id": batch_id},
            )
        ]
        messages_by_conversation: dict[str, list[dict]] = {}
        for item in self._many(
            "SELECT * FROM conversation_import_messages WHERE tenant_id = :tenant_id "
            "AND batch_id = :id ORDER BY conversation_id, ordinal, id",
            {"tenant_id": tenant_id, "id": batch_id},
        ):
            message = _decode_message(item)
            messages_by_conversation.setdefault(message["conversation_id"], []).append(
                message
            )
        for conversation in conversations:
            conversation["messages"] = messages_by_conversation.get(
                conversation["id"], []
            )
        result = _decode_batch(row)
        result["conversations"] = conversations
        return result

    def get_conversation_import_by_source(
        self, tenant_id: str, importer_id: str, source_content_hash: str
    ) -> dict | None:
        row = self._one(
            "SELECT id FROM conversation_import_batches WHERE tenant_id = :tenant_id "
            "AND importer_id = :importer_id AND source_content_hash = :source_content_hash",
            {
                "tenant_id": tenant_id,
                "importer_id": importer_id,
                "source_content_hash": source_content_hash,
            },
        )
        return (
            self.get_conversation_import(tenant_id, row["id"])
            if row is not None
            else None
        )

    def list_conversation_imports(
        self, tenant_id: str, limit: int = 50
    ) -> list[dict]:
        return [
            _decode_batch(row)
            for row in self._many(
                "SELECT * FROM conversation_import_batches WHERE tenant_id = :tenant_id "
                "ORDER BY created_at DESC, id DESC LIMIT :limit",
                {"tenant_id": tenant_id, "limit": max(1, min(limit, 200))},
            )
        ]
