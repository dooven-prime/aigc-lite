"""Importer for the ChatGPT data-export conversation graph."""

from __future__ import annotations

from ...core.conversation_imports import (
    ConversationImportResult,
    ImportedConversation,
    ImportedMessage,
)
from .common import (
    canonical_nodes,
    conversation_values,
    nearest_parent_message_id,
    normalized_time,
    object_list,
    original_time,
    stable_external_id,
    text_parts,
)


class ChatGPTConversationImporter:
    importer_id = "chatgpt.export.v1"
    version = 1
    display_name = "ChatGPT export"
    description = "OpenAI ChatGPT conversations.json with full branch preservation."

    def parse(self, payload: object) -> ConversationImportResult:
        values = conversation_values(payload)
        warnings: list[str] = []
        conversations: list[ImportedConversation] = []
        for conversation_ordinal, value in enumerate(values):
            external_id = stable_external_id(
                "conversation", value.get("id"), conversation_ordinal
            )
            if not value.get("id"):
                warnings.append(f"{external_id}: missing conversation id; generated a stable id")
            mapping = value.get("mapping")
            if not isinstance(mapping, dict):
                warnings.append(f"{external_id}: mapping is missing or invalid")
                continue
            node_to_message: dict[str, str] = {}
            for node_ordinal, (node_id, node) in enumerate(mapping.items()):
                message = node.get("message") if isinstance(node, dict) else None
                if isinstance(message, dict):
                    node_to_message[str(node_id)] = stable_external_id(
                        "message", message.get("id") or node_id, node_ordinal
                    )
            canonical = canonical_nodes(mapping, value.get("current_node"))
            if node_to_message and not canonical:
                warnings.append(
                    f"{external_id}: current_node is unavailable; all message nodes are marked canonical"
                )
            messages: list[ImportedMessage] = []
            for ordinal, (node_id_value, node) in enumerate(mapping.items()):
                node_id = str(node_id_value)
                message = node.get("message") if isinstance(node, dict) else None
                if not isinstance(message, dict):
                    continue
                external_message_id = node_to_message[node_id]
                author = message.get("author")
                role = (
                    str(author.get("role") or "unknown")
                    if isinstance(author, dict)
                    else "unknown"
                )
                if role == "unknown":
                    warnings.append(f"{external_id}/{external_message_id}: missing author role")
                content = message.get("content")
                content_type, rendered = text_parts(content)
                metadata = message.get("metadata")
                metadata = metadata if isinstance(metadata, dict) else {}
                content_mapping = content if isinstance(content, dict) else {}
                citations = (
                    object_list(metadata.get("citations"))
                    + object_list(metadata.get("content_references"))
                    + object_list(content_mapping.get("citations"))
                )
                attachments = object_list(metadata.get("attachments")) + object_list(
                    content_mapping.get("attachments")
                )
                created_at = message.get("create_time")
                messages.append(
                    ImportedMessage(
                        external_id=external_message_id,
                        parent_external_id=nearest_parent_message_id(
                            mapping, node_to_message, node.get("parent")
                        ),
                        role=role,
                        content_type=content_type,
                        content=rendered,
                        original_created_at=original_time(created_at),
                        normalized_created_at=normalized_time(created_at),
                        model=(
                            str(metadata["model_slug"])
                            if metadata.get("model_slug") is not None
                            else None
                        ),
                        citations=citations,
                        attachments=attachments,
                        metadata={
                            "node_id": node_id,
                            "author": author if isinstance(author, dict) else {},
                            "recipient": message.get("recipient"),
                            "status": message.get("status"),
                            "end_turn": message.get("end_turn"),
                            "message_metadata": metadata,
                        },
                        is_canonical=(not canonical or node_id in canonical),
                        ordinal=ordinal,
                    )
                )
            current_node = value.get("current_node")
            conversations.append(
                ImportedConversation(
                    external_id=external_id,
                    title=str(value.get("title") or external_id),
                    messages=tuple(messages),
                    original_created_at=original_time(value.get("create_time")),
                    normalized_created_at=normalized_time(value.get("create_time")),
                    original_updated_at=original_time(value.get("update_time")),
                    normalized_updated_at=normalized_time(value.get("update_time")),
                    current_message_external_id=(
                        node_to_message.get(current_node)
                        if isinstance(current_node, str)
                        else None
                    ),
                    metadata={
                        "current_node_id": current_node,
                        "is_archived": value.get("is_archived"),
                        "default_model_slug": value.get("default_model_slug"),
                    },
                    ordinal=conversation_ordinal,
                )
            )
        return ConversationImportResult(tuple(conversations), tuple(warnings))
