"""Environment-backed credential references."""

from __future__ import annotations

import os
import re

from ...core.errors import CredentialNotConfiguredError

_ENV_REFERENCE = re.compile(r"^env://([A-Za-z_][A-Za-z0-9_]*)$")


class EnvCredentialProvider:
    """Resolve ``env://NAME`` without caching the secret value."""

    def resolve(self, reference: str) -> str:
        match = _ENV_REFERENCE.fullmatch(reference)
        if match is None:
            raise CredentialNotConfiguredError("Unsupported credential reference")
        value = os.getenv(match.group(1))
        if not value:
            raise CredentialNotConfiguredError("Credential is not configured")
        return value
