"""Shared normalization helpers for conversation import adapters."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ...core.qualification import canonical_hash


def conversation_values(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        nested = payload.get("conversations")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        if isinstance(payload.get("mapping"), dict):
            return [payload]
    return []


def original_time(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None

def normalized_time(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), UTC).isoformat()
        if isinstance(value, str) and value.strip():
            text = value.strip().replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None
    return None


def object_list(value: object) -> tuple[dict[str, Any], ...]:
    if isinstance(value, dict):
        return (json.loads(json.dumps(value, ensure_ascii=False, default=str)),)
    if not isinstance(value, list):
        return ()
    return tuple(
        json.loads(json.dumps(item, ensure_ascii=False, default=str))
        for item in value
        if isinstance(item, dict)
    )


def stable_external_id(prefix: str, value: object, ordinal: int) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"generated:{prefix}:{canonical_hash({'ordinal': ordinal, 'value': value})[:20]}"


def text_parts(content: object) -> tuple[str, str]:
    if not isinstance(content, dict):
        return "unknown", ""
    content_type = str(content.get("content_type") or "unknown")
    parts = content.get("parts")
    if not isinstance(parts, list):
        text = content.get("text")
        return content_type, str(text) if text is not None else ""
    rendered: list[str] = []
    for part in parts:
        if isinstance(part, str):
            rendered.append(part)
        elif isinstance(part, dict):
            candidate = part.get("text", part.get("content"))
            if isinstance(candidate, str):
                rendered.append(candidate)
            else:
                rendered.append(json.dumps(part, ensure_ascii=False, sort_keys=True))
        elif part is not None:
            rendered.append(str(part))
    return content_type, "\n\n---\n\n".join(item for item in rendered if item)


def canonical_nodes(mapping: dict[str, Any], current_node: object) -> set[str]:
    if not isinstance(current_node, str) or current_node not in mapping:
        return set()
    result: set[str] = set()
    node_id: str | None = current_node
    while node_id and node_id not in result:
        result.add(node_id)
        node = mapping.get(node_id)
        parent = node.get("parent") if isinstance(node, dict) else None
        node_id = parent if isinstance(parent, str) else None
    return result


def nearest_parent_message_id(
    mapping: dict[str, Any], node_to_message: dict[str, str], parent: object
) -> str | None:
    seen: set[str] = set()
    node_id = parent if isinstance(parent, str) else None
    while node_id and node_id not in seen:
        seen.add(node_id)
        if node_id in node_to_message:
            return node_to_message[node_id]
        node = mapping.get(node_id)
        next_parent = node.get("parent") if isinstance(node, dict) else None
        node_id = next_parent if isinstance(next_parent, str) else None
    return None
