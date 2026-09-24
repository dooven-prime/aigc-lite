"""Scheme-dispatching credential provider."""

from __future__ import annotations

from ...core.errors import CredentialNotConfiguredError
from ...ports.credentials import CredentialProvider


class CompositeCredentialProvider:
    """Dispatch references without probing providers or exposing their failures."""

    def __init__(
        self,
        *,
        environment: CredentialProvider,
        encrypted_database: CredentialProvider,
    ) -> None:
        self._environment = environment
        self._encrypted_database = encrypted_database

    def resolve(self, reference: str) -> str:
        if reference.startswith("env://"):
            return self._environment.resolve(reference)
        if reference.startswith("encrypted-db://"):
            return self._encrypted_database.resolve(reference)
        raise CredentialNotConfiguredError("Unsupported credential reference")
