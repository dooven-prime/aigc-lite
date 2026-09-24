import sqlite3

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import database, main
from app.adapters.credentials import (
    CompositeCredentialProvider,
    EncryptedCredentialProvider,
    EnvCredentialProvider,
)
from app.config import settings
from app.core.contracts import RequestContext
from app.core.errors import (
    CredentialNotConfiguredError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.repository import SQLiteRepository
from app.services.credentials import CredentialService


def test_encrypted_store_is_write_only_workspace_scoped_and_revocable(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "master_key", Fernet.generate_key().decode())
    repository = SQLiteRepository(tmp_path / "credentials.db")
    repository.init()
    service = CredentialService(lambda: repository)
    alpha = RequestContext(request_id="r1", workspace_id="workspace-a")
    beta = RequestContext(request_id="r2", workspace_id="workspace-b")

    created = service.create(alpha, "research-token", "first-secret")
    assert created["configured"] is True
    assert created["source"] == "encrypted-db"
    assert "secret" not in created
    assert service.list(beta) == []
    with pytest.raises(ResourceConflictError):
        service.create(alpha, "research-token", "duplicate")

    provider = EncryptedCredentialProvider("workspace-a", lambda: repository)
    foreign_provider = EncryptedCredentialProvider("workspace-b", lambda: repository)
    assert provider.resolve(created["reference"]) == "first-secret"
    with pytest.raises(CredentialNotConfiguredError):
        foreign_provider.resolve(created["reference"])

    replaced = service.replace(alpha, created["id"], "second-secret")
    assert replaced["configured"] is True
    assert provider.resolve(created["reference"]) == "second-secret"
    with pytest.raises(ResourceNotFoundError):
        service.replace(beta, created["id"], "cross-workspace")

    revoked = service.revoke(alpha, created["id"])
    assert revoked["configured"] is False
    assert revoked["revoked_at"]
    with pytest.raises(CredentialNotConfiguredError):
        provider.resolve(created["reference"])

    with sqlite3.connect(tmp_path / "credentials.db") as connection:
        stored = connection.execute(
            "SELECT secret_value FROM credentials WHERE id = ?", (created["id"],)
        ).fetchone()[0]
    assert stored.startswith("enc:v1:")
    assert "first-secret" not in stored
    assert "second-secret" not in stored


def test_composite_provider_dispatches_without_caching(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "master_key", Fernet.generate_key().decode())
    repository = SQLiteRepository(tmp_path / "composite.db")
    repository.init()
    context = RequestContext(request_id="r1", workspace_id="workspace-a")
    created = CredentialService(lambda: repository).create(
        context, "stored", "database-secret"
    )
    provider = CompositeCredentialProvider(
        environment=EnvCredentialProvider(),
        encrypted_database=EncryptedCredentialProvider(
            "workspace-a", lambda: repository
        ),
    )

    monkeypatch.setenv("AIGC_LITE_TEST_CREDENTIAL", "first-env-value")
    assert provider.resolve("env://AIGC_LITE_TEST_CREDENTIAL") == "first-env-value"
    monkeypatch.setenv("AIGC_LITE_TEST_CREDENTIAL", "rotated-env-value")
    assert provider.resolve("env://AIGC_LITE_TEST_CREDENTIAL") == "rotated-env-value"
    assert provider.resolve(created["reference"]) == "database-secret"


def test_credential_api_never_returns_plaintext_and_mcp_accepts_reference(
    tmp_path, monkeypatch
) -> None:
    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "credential-api.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    monkeypatch.setattr(main.settings, "allow_signup", True)
    monkeypatch.setattr(main.settings, "master_key", Fernet.generate_key().decode())

    with TestClient(main.app) as client:
        registration = client.post(
            "/api/auth/register",
            json={
                "email": "credential-admin@example.com",
                "password": "long-enough-password",
                "name": "Credential Admin",
            },
        )
        token = registration.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        invalid_secret = "do-not-echo-this-secret-" * 500
        invalid = client.post(
            "/api/credentials",
            headers=headers,
            json={"name": "invalid", "secret": invalid_secret},
        )
        assert invalid.status_code == 422
        assert "do-not-echo-this-secret" not in invalid.text

        created = client.post(
            "/api/credentials",
            headers=headers,
            json={"name": "research", "secret": "api-private-value"},
        )
        assert created.status_code == 200
        credential = created.json()
        assert credential["configured"] is True
        assert credential["reference"].startswith("encrypted-db://credential/")
        assert "api-private-value" not in created.text
        assert "secret" not in credential

        duplicate = client.post(
            "/api/credentials",
            headers=headers,
            json={"name": "research", "secret": "duplicate-private-value"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "resource_conflict"
        assert "duplicate-private-value" not in duplicate.text

        listed = client.get("/api/credentials", headers=headers)
        assert listed.json() == [credential]
        assert "api-private-value" not in listed.text

        mcp_server = client.post(
            "/api/mcp-servers",
            headers=headers,
            json={
                "provider_id": "encrypted-research",
                "url": "https://mcp.example.test/mcp",
                "header_credentials": {
                    "Authorization": credential["reference"]
                },
            },
        )
        assert mcp_server.status_code == 200
        assert mcp_server.json()["header_credentials"] == {
            "Authorization": credential["reference"]
        }

        replaced = client.post(
            f"/api/credentials/{credential['id']}/replace",
            headers=headers,
            json={"secret": "replacement-private-value"},
        )
        assert replaced.status_code == 200
        assert "replacement-private-value" not in replaced.text

        revoked = client.delete(
            f"/api/credentials/{credential['id']}", headers=headers
        )
        assert revoked.status_code == 200
        assert revoked.json()["configured"] is False
