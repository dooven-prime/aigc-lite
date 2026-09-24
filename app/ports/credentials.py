"""Credential reference resolution boundary."""

from __future__ import annotations

from typing import Protocol


class CredentialProvider(Protocol):
    """Resolve a secret reference at the operation boundary."""

    def resolve(self, reference: str) -> str: ...
