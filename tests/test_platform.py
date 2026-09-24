import sqlite3

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import database, main
from app.auth import hash_password, verify_password
from app.repository import SQLiteRepository
from app.secrets import decrypt


def test_password_round_trip() -> None:
    encoded = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)


def test_model_key_is_encrypted_and_usage_is_summarized(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "master_key", Fernet.generate_key().decode())
    repository = SQLiteRepository(tmp_path / "platform.db")
    repository.init()
    saved = repository.save_model_config(
        "tenant-a",
        {"name": "local", "base_url": "http://localhost/v1", "model": "demo", "api_key": "secret"},
    )
    assert "api_key" not in saved
    with sqlite3.connect(tmp_path / "platform.db") as db:
        raw = db.execute("SELECT api_key FROM model_configs").fetchone()[0]
    assert raw.startswith("enc:v1:")
    assert decrypt(raw) == "secret"
    repository.usage("tenant-a", "demo", 100, 50, 0.25)
    assert repository.usage_summary("tenant-a")["cost"] == 0.25


def test_registration_and_admin_model_api(tmp_path, monkeypatch) -> None:
    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "api.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    monkeypatch.setattr(main.settings, "allow_signup", True)
    monkeypatch.setattr(main.settings, "master_key", Fernet.generate_key().decode())
    with TestClient(main.app) as client:
        registration = client.post(
            "/api/auth/register",
            json={"email": "admin@example.com", "password": "long-enough-password", "name": "Admin"},
        )
        assert registration.status_code == 200
        token = registration.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/auth/me", headers=headers).json()["role"] == "admin"
        model = client.post(
            "/api/models",
            headers=headers,
            json={"name": "demo", "base_url": "http://localhost/v1", "model": "demo", "api_key": "secret"},
        )
        assert model.status_code == 200
        assert "api_key" not in model.json()
        mcp_server = client.post(
            "/api/mcp-servers",
            headers=headers,
            json={
                "provider_id": "research",
                "url": "https://mcp.example.test/mcp",
                "header_credentials": {
                    "Authorization": "env://RESEARCH_MCP_AUTH"
                },
                "risk": "medium",
                "timeout_seconds": 15,
            },
        )
        assert mcp_server.status_code == 200
        assert mcp_server.json()["provider_id"] == "research"
        assert client.get("/api/mcp-servers", headers=headers).json() == [
            mcp_server.json()
        ]
        literal_secret = client.post(
            "/api/mcp-servers",
            headers=headers,
            json={
                "provider_id": "unsafe",
                "url": "https://mcp.example.test/mcp",
                "header_credentials": {"Authorization": "Bearer literal-secret"},
            },
        )
        assert literal_secret.status_code == 422
        assert client.delete(
            f"/api/mcp-servers/{mcp_server.json()['id']}", headers=headers
        ).json() == {"deleted": True}
        tenants = client.get("/api/admin/tenants", headers=headers)
        assert tenants.status_code == 200
        assert [item["id"] for item in tenants.json()] == [
            registration.json()["tenant_id"]
        ]
        assert client.get(
            "/api/admin/tenants/another-workspace/users", headers=headers
        ).status_code == 404
        assert client.post(
            "/api/admin/tenants/another-workspace/users",
            headers=headers,
            json={
                "email": "member@example.com",
                "password": "long-enough-password",
                "name": "Member",
                "role": "member",
            },
        ).status_code == 404
