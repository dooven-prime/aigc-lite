"""Shared serialization and time primitives for repository domains."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def authorization_not_expired(expires_at: str | None, used_at: str) -> bool:
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        current = datetime.fromisoformat(used_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False
    return (
        expiry.tzinfo is not None
        and current.tzinfo is not None
        and expiry > current
    )


def decode_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def decode_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        decoded = json.loads(value or "[]")
        return decoded if isinstance(decoded, list) else []
    except (TypeError, json.JSONDecodeError):
        return []
