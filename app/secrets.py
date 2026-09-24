"""Versioned authenticated encryption for credentials stored at rest."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import settings
from .core.errors import CredentialDecryptionError, CredentialKeyNotConfiguredError

_V1_PREFIX = "enc:v1:"


def _legacy_cipher() -> Fernet:
    """Read pre-v1 ciphertext without ever accepting plaintext as a credential."""

    key = base64.urlsafe_b64encode(hashlib.sha256(settings.auth_secret.encode()).digest())
    return Fernet(key)


def _cipher() -> Fernet:
    value = settings.master_key.strip()
    if not value:
        raise CredentialKeyNotConfiguredError()
    try:
        return Fernet(value.encode())
    except (TypeError, ValueError) as exc:
        raise CredentialKeyNotConfiguredError(
            "Credential master key must be a valid Fernet key"
        ) from exc


def encrypt(value: str) -> str:
    if not value:
        return ""
    return f"{_V1_PREFIX}{_cipher().encrypt(value.encode()).decode()}"


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        if value.startswith(_V1_PREFIX):
            token = value.removeprefix(_V1_PREFIX)
            return _cipher().decrypt(token.encode()).decode()
        return _legacy_cipher().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise CredentialDecryptionError() from exc
