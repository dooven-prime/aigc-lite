"""Environment-backed credential references."""

from __future__ import annotations

import os
import re

from ...core.errors import CredentialNotConfiguredError

_ENV_REFERENCE = re.compile(r"^env://([A-Za-z_][A-Za-z0-9_]*)$")


class EnvCredentialProvider:
    """Resolve allowlisted deployment-owned ``env://NAME`` references."""

    def __init__(self, allowed_names: frozenset[str] = frozenset()) -> None:
        self._allowed_names = allowed_names

    def resolve(self, reference: str) -> str:
        match = _ENV_REFERENCE.fullmatch(reference)
        if match is None:
            raise CredentialNotConfiguredError("Unsupported credential reference")
        name = match.group(1)
        if name not in self._allowed_names:
            raise CredentialNotConfiguredError("Credential is not allowlisted")
        value = os.getenv(name)
        if not value:
            raise CredentialNotConfiguredError("Credential is not configured")
        return value
