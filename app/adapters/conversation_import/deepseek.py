"""Importer for DeepSeek conversation exports with fragment preservation."""

from __future__ import annotations

from typing import Any

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
)


class DeepSeekConversationImporter:
    importer_id = "deepseek.export.v1"
    version = 1
    display_name = "DeepSeek export"
    description = "DeepSeek mapping/fragments export with search evidence preservation."

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
                fragments = message.get("fragments")
                fragments = fragments if isinstance(fragments, list) else []
                fragment_types = [
                    str(fragment.get("type") or "").upper()
                    for fragment in fragments
                    if isinstance(fragment, dict)
                ]
                role = str(message.get("role") or "")
                if not role:
                    role = (
                        "user"
                        if fragment_types and all(item == "REQUEST" for item in fragment_types)
                        else "assistant"
                    )
                content_parts: list[str] = []
                citations: list[dict[str, Any]] = []
                attachments: list[dict[str, Any]] = []
                for fragment in fragments:
                    if not isinstance(fragment, dict):
                        continue
                    fragment_type = str(fragment.get("type") or "").upper()
                    text = fragment.get("content")
                    if isinstance(text, str) and text:
                        content_parts.append(
                            f"<think>{text}</think>" if fragment_type == "THINK" else text
                        )
                    if fragment_type == "SEARCH":
                        citations.extend(object_list(fragment.get("results")))
                    attachments.extend(object_list(fragment.get("attachments")))
                created_at = message.get("inserted_at", message.get("create_time"))
                external_message_id = node_to_message[node_id]
                messages.append(
                    ImportedMessage(
                        external_id=external_message_id,
                        parent_external_id=nearest_parent_message_id(
                            mapping, node_to_message, node.get("parent")
                        ),
                        role=role,
                        content_type="deepseek.fragments",
                        content="\n\n---\n\n".join(content_parts),
                        original_created_at=original_time(created_at),
                        normalized_created_at=normalized_time(created_at),
                        model=(
                            str(message["model"])
                            if message.get("model") is not None
                            else None
                        ),
                        citations=tuple(citations),
                        attachments=tuple(attachments),
                        metadata={
                            "node_id": node_id,
                            "fragment_types": fragment_types,
                        },
                        is_canonical=(not canonical or node_id in canonical),
                        ordinal=ordinal,
                    )
                )
            current_node = value.get("current_node")
            created_at = value.get("inserted_at", value.get("create_time"))
            updated_at = value.get("updated_at", value.get("update_time"))
            conversations.append(
                ImportedConversation(
                    external_id=external_id,
                    title=str(value.get("title") or external_id),
                    messages=tuple(messages),
                    original_created_at=original_time(created_at),
                    normalized_created_at=normalized_time(created_at),
                    original_updated_at=original_time(updated_at),
                    normalized_updated_at=normalized_time(updated_at),
                    current_message_external_id=(
                        node_to_message.get(current_node)
                        if isinstance(current_node, str)
                        else None
                    ),
                    metadata={"current_node_id": current_node},
                    ordinal=conversation_ordinal,
                )
            )
        return ConversationImportResult(tuple(conversations), tuple(warnings))
