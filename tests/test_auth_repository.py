"""Storage-level authentication lifecycle and cross-worker throttling contracts."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

from app.repository import SQLiteRepository

FUTURE = "2999-01-01T00:00:00+00:00"


def _repository(tmp_path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "auth-lifecycle.db")
    repository.init()
    return repository


def test_session_version_fences_stale_login_and_all_session_revocation(tmp_path) -> None:
    repository = _repository(tmp_path)
    tenant = repository.create_tenant("Authentication test")
    user = repository.create_user(
        tenant["id"], "user@example.test", "User", "old-hash"
    )
    old_version = repository.get_user(user["id"])["auth_version"]
    assert old_version == 0
    assert repository.save_auth_session(
        "first-token", user["id"], tenant["id"], FUTURE,
        expected_auth_version=old_version,
    )
    assert repository.save_auth_session(
        "second-token", user["id"], tenant["id"], FUTURE,
        expected_auth_version=old_version,
    )
    assert repository.revoke_all_auth_sessions("first-token", user["id"])
    assert repository.get_auth_session("first-token") is None
    assert repository.get_auth_session("second-token") is None
    assert not repository.save_auth_session(
        "stale-login-token", user["id"], tenant["id"], FUTURE,
        expected_auth_version=old_version,
    )
    assert repository.get_auth_session("stale-login-token") is None
    assert repository.get_user(user["id"])["auth_version"] == old_version + 1
    assert not repository.revoke_all_auth_sessions("first-token", user["id"])


def test_password_change_is_atomic_with_session_revocation(tmp_path) -> None:
    repository = _repository(tmp_path)
    tenant = repository.create_tenant("Authentication test")
    user = repository.create_user(
        tenant["id"], "password@example.test", "User", "old-hash"
    )
    assert repository.save_auth_session(
        "first-token", user["id"], tenant["id"], FUTURE,
        expected_auth_version=0,
    )
    assert repository.save_auth_session(
        "second-token", user["id"], tenant["id"], FUTURE,
        expected_auth_version=0,
    )
    assert not repository.change_password_and_revoke_sessions(
        "first-token", user["id"], "wrong-hash", "new-hash"
    )
    assert repository.get_auth_session("second-token") is not None
    assert repository.change_password_and_revoke_sessions(
        "first-token", user["id"], "old-hash", "new-hash"
    )
    assert repository.get_user(user["id"])["password_hash"] == "new-hash"
    assert repository.get_auth_session("first-token") is None
    assert repository.get_auth_session("second-token") is None
    assert not repository.save_auth_session(
        "stale-login-token", user["id"], tenant["id"], FUTURE,
        expected_auth_version=0,
    )
    assert repository.save_auth_session(
        "new-login-token", user["id"], tenant["id"], FUTURE,
        expected_auth_version=1,
    )
    assert repository.get_auth_session("new-login-token") is not None


def test_session_creation_rejects_workspace_mismatch_and_stale_rows(tmp_path) -> None:
    repository = _repository(tmp_path)
    tenant = repository.create_tenant("Authentication test")
    user = repository.create_user(
        tenant["id"], "tenant@example.test", "User", "hash"
    )
    assert not repository.save_auth_session(
        "wrong-tenant", user["id"], "other-tenant", FUTURE,
        expected_auth_version=0,
    )
    with sqlite3.connect(repository.database_path) as db:
        db.execute(
            "INSERT INTO auth_sessions(token_hash, user_id, tenant_id, expires_at, "
            "created_at, auth_version) VALUES (?, ?, ?, ?, ?, ?)",
            ("forged-version", user["id"], tenant["id"], FUTURE, FUTURE, 12),
        )
    assert repository.get_auth_session("forged-version") is None


def test_pre_auth_throttle_is_atomic_across_repository_instances(tmp_path) -> None:
    path = tmp_path / "shared-auth.db"
    SQLiteRepository(path).init()
    key = sha256(b"opaque-login-identity").hexdigest()

    def consume(_: int) -> bool:
        return SQLiteRepository(path).consume_pre_auth_limit(
            "login:account", key, 3600, 4
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(consume, range(12)))
    assert results.count(True) == 4
    assert results.count(False) == 8
    other_key = sha256(b"other-identity").hexdigest()
    assert SQLiteRepository(path).consume_pre_auth_limit(
        "login:account", other_key, 3600, 4
    )


def test_expired_auth_state_cleanup_removes_only_expired_rows(tmp_path) -> None:
    repository = _repository(tmp_path)
    tenant = repository.create_tenant("Authentication test")
    user = repository.create_user(
        tenant["id"], "cleanup@example.test", "User", "hash"
    )
    assert repository.save_auth_session(
        "active", user["id"], tenant["id"], FUTURE, expected_auth_version=0
    )
    assert repository.save_auth_session(
        "expired", user["id"], tenant["id"], "2000-01-01T00:00:00+00:00",
        expected_auth_version=0,
    )
    key = sha256(b"cleanup").hexdigest()
    assert repository.consume_pre_auth_limit("login:source", key, 3600, 3)
    with sqlite3.connect(repository.database_path) as db:
        db.execute(
            "INSERT INTO pre_auth_rate_limits(kind, key_hash, window_start, "
            "window_seconds, expires_at, attempts) VALUES (?, ?, ?, ?, ?, ?)",
            ("register:source", key, 0, 300, 300, 1),
        )
    repository.cleanup_expired_auth_state()
    assert repository.get_auth_session("active") is not None
    with sqlite3.connect(repository.database_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM auth_sessions WHERE token_hash = 'expired'"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM pre_auth_rate_limits WHERE kind = 'register:source'"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM pre_auth_rate_limits WHERE kind = 'login:source'"
        ).fetchone()[0] == 1
