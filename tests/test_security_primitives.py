import base64
import hashlib
import json

import pytest
from cryptography.fernet import Fernet

from app.config import settings
from app.core.errors import (
    CredentialDecryptionError,
    CredentialKeyNotConfiguredError,
)
from app.redaction import redact, redact_record_text
from app.repository import SQLiteRepository
from app.secrets import decrypt, encrypt


def test_versioned_encryption_requires_independent_master_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "master_key", "")
    with pytest.raises(CredentialKeyNotConfiguredError):
        encrypt("secret")

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "master_key", key)
    ciphertext = encrypt("secret")
    assert ciphertext.startswith("enc:v1:")
    assert decrypt(ciphertext) == "secret"
    prefixed_plaintext = encrypt("enc:v1:not-ciphertext")
    assert prefixed_plaintext != "enc:v1:not-ciphertext"
    assert decrypt(prefixed_plaintext) == "enc:v1:not-ciphertext"

    monkeypatch.setattr(settings, "master_key", Fernet.generate_key().decode())
    with pytest.raises(CredentialDecryptionError):
        decrypt(ciphertext)


def test_legacy_ciphertext_is_read_but_plaintext_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_secret", "legacy-auth-secret")
    legacy_key = base64.urlsafe_b64encode(
        hashlib.sha256(b"legacy-auth-secret").digest()
    )
    legacy = Fernet(legacy_key).encrypt(b"legacy-value").decode()

    assert decrypt(legacy) == "legacy-value"
    with pytest.raises(CredentialDecryptionError):
        decrypt("legacy-value")


def test_redaction_covers_nested_secrets_signed_urls_and_audit(tmp_path) -> None:
    signed_url = (
        "https://bucket.example/file?X-Amz-Algorithm=AWS4-HMAC-SHA256&"
        "X-Amz-Credential=private%2Fcredential&X-Amz-Signature=top-secret"
    )
    value = {
        "nested": [{"client-secret": "hidden", "download": signed_url}],
        "message": f"use Bearer private-token then {signed_url}",
    }
    cleaned = redact(value)
    rendered = json.dumps(cleaned)
    assert "hidden" not in rendered
    assert "private-token" not in rendered
    assert "private%2Fcredential" not in rendered
    assert "top-secret" not in rendered
    assert "X-Amz-Signature=***" in cleaned["nested"][0]["download"]

    repository = SQLiteRepository(tmp_path / "redaction.db")
    repository.init()
    repository.write_audit("workspace-a", "test", "/test", value)
    audit = repository.list_audit("workspace-a")[0]
    assert "top-secret" not in json.dumps(audit)
    assert redact_record_text(json.dumps(value)) == json.dumps(
        cleaned, ensure_ascii=False
    )
