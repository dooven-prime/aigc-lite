"""Shared pre-auth budgets and versioned login-session lifecycle."""

from fastapi.testclient import TestClient

from app import database
from app.main import app, run


def _settings(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(database.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(database.settings, "database_url", "")
    monkeypatch.setattr(database.settings, "host", "127.0.0.1")
    monkeypatch.setattr(database.settings, "allow_signup", True)
    monkeypatch.setattr(database.settings, "admin_email", "")
    monkeypatch.setattr(database.settings, "admin_password", "")
    monkeypatch.setattr(database.settings, "scheduler_enabled", False)
    monkeypatch.setattr(database.settings, "auth_rate_window_seconds", 300)
    monkeypatch.setattr(database.settings, "auth_login_account_limit", 10)
    monkeypatch.setattr(database.settings, "auth_login_source_limit", 60)
    monkeypatch.setattr(database.settings, "auth_register_source_limit", 10)
    monkeypatch.setattr(database.settings, "auth_password_change_limit", 5)


def _register(client: TestClient, email: str = "user@example.test") -> str:
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "correct-password",
            "name": "User",
            "workspace_name": "Workspace",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _login(client: TestClient, password: str = "correct-password") -> str:
    response = client.post(
        "/api/auth/login",
        json={"email": "user@example.test", "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_logout_all_revokes_every_session_and_permits_fresh_login(tmp_path, monkeypatch) -> None:
    _settings(tmp_path, monkeypatch)
    with TestClient(app) as client:
        first = _register(client)
        second = _login(client)
        assert client.post("/api/auth/logout-all", headers=_headers(first)).json() == {
            "revoked": True
        }
        assert client.get("/api/auth/me", headers=_headers(first)).status_code == 401
        assert client.get("/api/auth/me", headers=_headers(second)).status_code == 401
        assert client.get("/api/auth/me", headers=_headers(_login(client))).status_code == 200


def test_password_change_revokes_all_sessions_and_old_password(tmp_path, monkeypatch) -> None:
    _settings(tmp_path, monkeypatch)
    with TestClient(app) as client:
        first = _register(client)
        second = _login(client)
        denied = client.post(
            "/api/auth/password", headers=_headers(first),
            json={"current_password": "wrong-password", "new_password": "new-correct-password"},
        )
        assert denied.status_code == 401
        assert client.get("/api/auth/me", headers=_headers(first)).status_code == 200
        changed = client.post(
            "/api/auth/password", headers=_headers(first),
            json={"current_password": "correct-password", "new_password": "new-correct-password"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json() == {"changed": True, "sessions_revoked": True}
        assert client.get("/api/auth/me", headers=_headers(first)).status_code == 401
        assert client.get("/api/auth/me", headers=_headers(second)).status_code == 401
        assert client.post(
            "/api/auth/login",
            json={"email": "user@example.test", "password": "correct-password"},
        ).status_code == 401
        assert client.get(
            "/api/auth/me", headers=_headers(_login(client, "new-correct-password"))
        ).status_code == 200


def test_login_limit_is_shared_and_forwarded_header_cannot_choose_bucket(
    tmp_path, monkeypatch,
) -> None:
    _settings(tmp_path, monkeypatch)
    monkeypatch.setattr(database.settings, "auth_login_account_limit", 2)
    with TestClient(app) as first_client:
        _register(first_client)
        for forwarded in ("192.0.2.1", "192.0.2.2"):
            response = first_client.post(
                "/api/auth/login",
                json={"email": "user@example.test", "password": "wrong-password"},
                headers={"X-Forwarded-For": forwarded},
            )
            assert response.status_code == 401
    with TestClient(app) as second_client:
        limited = second_client.post(
            "/api/auth/login",
            json={"email": "USER@example.test", "password": "correct-password"},
            headers={"X-Forwarded-For": "192.0.2.3"},
        )
        assert limited.status_code == 429
        assert limited.headers["Retry-After"] == "300"


def test_registration_limit_is_shared_between_clients(tmp_path, monkeypatch) -> None:
    _settings(tmp_path, monkeypatch)
    monkeypatch.setattr(database.settings, "auth_register_source_limit", 1)
    with TestClient(app) as first_client:
        _register(first_client)
    with TestClient(app) as second_client:
        limited = second_client.post(
            "/api/auth/register",
            json={
                "email": "another@example.test", "password": "correct-password",
                "name": "Another", "workspace_name": "Other",
            },
            headers={"X-Forwarded-For": "192.0.2.100"},
        )
        assert limited.status_code == 429


def test_packaged_server_does_not_rewrite_auth_source_from_proxy_headers(monkeypatch) -> None:
    import uvicorn

    observed = {}
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: observed.update(kwargs))
    run()
    assert observed["proxy_headers"] is False


def test_password_change_check_has_shared_attempt_limit(tmp_path, monkeypatch) -> None:
    _settings(tmp_path, monkeypatch)
    monkeypatch.setattr(database.settings, "auth_password_change_limit", 2)
    with TestClient(app) as client:
        token = _register(client)
        for _ in range(2):
            denied = client.post(
                "/api/auth/password", headers=_headers(token),
                json={"current_password": "wrong-password", "new_password": "new-password"},
            )
            assert denied.status_code == 401
    with TestClient(app) as client:
        limited = client.post(
            "/api/auth/password", headers=_headers(token),
            json={"current_password": "correct-password", "new_password": "new-password"},
        )
        assert limited.status_code == 429
        assert client.get("/api/auth/me", headers=_headers(token)).status_code == 200
