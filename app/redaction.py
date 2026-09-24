"""Central, deterministic redaction for persisted execution and audit data."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "***"

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "cookie",
    "credential",
    "password",
    "passwd",
    "private_key",
    "secret",
    "set_cookie",
    "token",
}
_SENSITIVE_QUERY_KEYS = {
    "access_key",
    "access_token",
    "api_key",
    "apikey",
    "key",
    "sig",
    "signature",
    "token",
    "x_amz_credential",
    "x_amz_security_token",
    "x_amz_signature",
    "x_goog_credential",
    "x_goog_signature",
}
_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+")
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:access[_-]?token|api[_-]?key|key|sig(?:nature)?|token|"
    r"x-amz-(?:credential|security-token|signature)|"
    r"x-goog-(?:credential|signature))=)[^&#\s]+"
)


def _normalized_key(value: str) -> str:
    return value.casefold().replace("-", "_").replace(" ", "_")


def is_sensitive_key(key: str) -> bool:
    normalized = _normalized_key(key)
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith("_token")
        or normalized.endswith("_secret")
        or normalized.endswith("_password")
        or normalized.endswith("_credential")
    )


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return value
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if not pairs:
        return value
    changed = False
    redacted_pairs: list[tuple[str, str]] = []
    for key, item in pairs:
        if _normalized_key(key) in _SENSITIVE_QUERY_KEYS:
            redacted_pairs.append((key, REDACTED))
            changed = True
        else:
            redacted_pairs.append((key, item))
    if not changed:
        return value
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(redacted_pairs, safe="*"),
            parsed.fragment,
        )
    )


def redact_text(value: str) -> str:
    """Redact bearer values and signed-URL query parameters in arbitrary text."""

    value = _URL_PATTERN.sub(lambda match: _redact_url(match.group(0)), value)
    value = _BEARER_PATTERN.sub(rf"\1{REDACTED}", value)
    return _QUERY_SECRET_PATTERN.sub(rf"\1{REDACTED}", value)


def redact(value: Any) -> Any:
    """Recursively redact dictionaries, sequences, and credential-shaped strings."""

    if isinstance(value, dict):
        return {
            key: REDACTED if is_sensitive_key(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_record_text(content: str, *, max_chars: int | None = None) -> str:
    """Redact JSON structurally, falling back to safe plain-text redaction."""

    try:
        rendered = json.dumps(
            redact(json.loads(content)), ensure_ascii=False, default=str
        )
    except (json.JSONDecodeError, TypeError):
        rendered = redact_text(content)
    return rendered if max_chars is None else rendered[:max_chars]


def safe_json_text(
    content: str,
    *,
    hide_errors: bool = False,
    max_chars: int | None = None,
) -> str:
    """Return redacted JSON for ledger storage, omitting malformed structures."""

    try:
        decoded = json.loads(content or "{}")
    except json.JSONDecodeError:
        return "[invalid JSON omitted]"
    if hide_errors and isinstance(decoded, dict) and "error" in decoded:
        rendered = json.dumps({"error": "tool_execution_failed"})
    else:
        rendered = json.dumps(redact(decoded), ensure_ascii=False, default=str)
    return rendered if max_chars is None else rendered[:max_chars]
