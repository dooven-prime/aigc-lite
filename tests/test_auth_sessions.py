"""Login session revocation and tenant binding contracts."""

from fastapi.testclient import TestClient

from app import database, tenancy
from app.auth import token_hash
from app.main import app


def _local_client_settings(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(database.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(database.settings, "database_url", "")
    monkeypatch.setattr(database.settings, "host", "127.0.0.1")
    monkeypatch.setattr(database.settings, "allow_signup", True)
    monkeypatch.setattr(database.settings, "admin_email", "")
    monkeypatch.setattr(database.settings, "admin_password", "")
    monkeypatch.setattr(database.settings, "scheduler_enabled", False)


def test_logout_revokes_only_the_presented_session(tmp_path, monkeypatch) -> None:
    _local_client_settings(tmp_path, monkeypatch)
    with TestClient(app) as client:
        created = client.post(
            "/api/auth/register",
            json={
                "email": "user@example.test",
                "password": "correct-password",
                "name": "User",
                "workspace_name": "Workspace",
            },
        )
        assert created.status_code == 200
        first_token = created.json()["access_token"]
        logged_in = client.post(
            "/api/auth/login",
            json={"email": "user@example.test", "password": "correct-password"},
        )
        assert logged_in.status_code == 200
        second_token = logged_in.json()["access_token"]
        first_headers = {"Authorization": f"Bearer {first_token}"}
        second_headers = {"Authorization": f"Bearer {second_token}"}

        assert client.get("/api/auth/me", headers=first_headers).status_code == 200
        assert client.post("/api/auth/logout", headers=first_headers).json() == {
            "revoked": True
        }
        assert client.get("/api/auth/me", headers=first_headers).status_code == 401
        assert client.post("/api/auth/logout", headers=first_headers).status_code == 401
        assert client.get("/api/auth/me", headers=second_headers).status_code == 200


def test_session_cannot_cross_workspace_after_its_user_changes(tmp_path, monkeypatch) -> None:
    _local_client_settings(tmp_path, monkeypatch)
    with TestClient(app) as client:
        created = client.post(
            "/api/auth/register",
            json={
                "email": "tenant@example.test",
                "password": "correct-password",
                "name": "User",
                "workspace_name": "Workspace",
            },
        )
        assert created.status_code == 200
        user_id = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {created.json()['access_token']}"},
        ).json()["id"]
        mismatched_token = "mismatched-tenant-session"
        database.get_repository().save_auth_session(
            token_hash(mismatched_token), user_id, "different-workspace", "2999-01-01T00:00:00+00:00"
        )
        headers = {"Authorization": f"Bearer {mismatched_token}"}
        assert client.get("/api/auth/me", headers=headers).status_code == 401
        monkeypatch.setattr(tenancy, "is_loopback_binding", lambda _host: False)
        assert client.get("/api/sessions", headers=headers).status_code == 401
