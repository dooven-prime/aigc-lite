"""Credential control-plane service with write-only secret values."""

from __future__ import annotations

from collections.abc import Callable

from ..core.contracts import RequestContext
from ..core.errors import ResourceConflictError, ResourceNotFoundError
from ..database import get_repository
from ..repository import Repository
from ..secrets import encrypt

RepositoryProvider = Callable[[], Repository]


def credential_reference(credential_id: str) -> str:
    return f"encrypted-db://credential/{credential_id}"


def _public(record: dict) -> dict:
    revoked = bool(record.get("revoked_at"))
    return {
        "id": record["id"],
        "name": record["name"],
        "reference": credential_reference(record["id"]),
        "configured": not revoked,
        "source": "encrypted-db",
        "writable": True,
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "revoked_at": record.get("revoked_at"),
    }


class CredentialService:
    """Create, replace, list, and revoke secrets without a plaintext read API."""

    def __init__(self, repository_provider: RepositoryProvider = get_repository) -> None:
        self._repository_provider = repository_provider

    def create(self, context: RequestContext, name: str, secret: str) -> dict:
        try:
            record = self._repository_provider().create_credential(
                context.workspace_id, name, encrypt(secret)
            )
        except ValueError as exc:
            if str(exc) != "credential_name_conflict":
                raise
            raise ResourceConflictError("credential", "name") from exc
        return _public(record)

    def list(self, context: RequestContext) -> list[dict]:
        return [
            _public(record)
            for record in self._repository_provider().list_credentials(
                context.workspace_id
            )
        ]

    def replace(
        self, context: RequestContext, credential_id: str, secret: str
    ) -> dict:
        record = self._repository_provider().replace_credential(
            context.workspace_id, credential_id, encrypt(secret)
        )
        if record is None:
            raise ResourceNotFoundError("credential", credential_id)
        return _public(record)

    def revoke(self, context: RequestContext, credential_id: str) -> dict:
        record = self._repository_provider().revoke_credential(
            context.workspace_id, credential_id
        )
        if record is None:
            raise ResourceNotFoundError("credential", credential_id)
        return _public(record)
