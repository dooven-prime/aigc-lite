"""Workspace-bound encrypted database credential references."""

from __future__ import annotations

import re
from collections.abc import Callable
from uuid import UUID

from ...core.errors import CredentialNotConfiguredError
from ...database import get_repository
from ...repository import Repository
from ...secrets import decrypt

RepositoryProvider = Callable[[], Repository]
_REFERENCE = re.compile(r"^encrypted-db://credential/([0-9a-fA-F-]{36})$")


class EncryptedCredentialProvider:
    """Resolve an encrypted reference inside exactly one workspace."""

    def __init__(
        self,
        workspace_id: str,
        repository_provider: RepositoryProvider = get_repository,
    ) -> None:
        self._workspace_id = workspace_id
        self._repository_provider = repository_provider

    def resolve(self, reference: str) -> str:
        match = _REFERENCE.fullmatch(reference)
        if match is None:
            raise CredentialNotConfiguredError("Unsupported credential reference")
        try:
            credential_id = str(UUID(match.group(1)))
        except ValueError as exc:
            raise CredentialNotConfiguredError(
                "Unsupported credential reference"
            ) from exc
        record = self._repository_provider().get_credential(
            self._workspace_id, credential_id
        )
        if record is None or record.get("revoked_at"):
            raise CredentialNotConfiguredError("Credential is not configured")
        return decrypt(record["secret_value"])
