"""Credential reference syntax shared by API and runtime boundaries."""

from __future__ import annotations

import re
from uuid import UUID

_ENCRYPTED_REFERENCE = re.compile(
    r"^encrypted-db://credential/([0-9a-fA-F-]{36})$"
)


def encrypted_credential_id(reference: str) -> str:
    """Return the canonical UUID from a workspace-owned credential reference."""

    match = _ENCRYPTED_REFERENCE.fullmatch(reference)
    if match is None:
        raise ValueError(
            "credential reference must use encrypted-db://credential/UUID"
        )
    try:
        return str(UUID(match.group(1)))
    except ValueError as exc:
        raise ValueError(
            "credential reference must use encrypted-db://credential/UUID"
        ) from exc


def validate_workspace_credential_map(value: dict[str, str]) -> dict[str, str]:
    """Validate persisted workspace headers without resolving their secrets."""

    for header, reference in value.items():
        if not isinstance(header, str) or not header.strip():
            raise ValueError("header names must not be empty")
        if not isinstance(reference, str):
            raise ValueError("header credential references must be strings")
        encrypted_credential_id(reference)
    return value
