"""Password authentication and bearer session helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Header, HTTPException, Request

from .config import settings
from .database import get_repository
from .tenancy import Tenant, _role_scopes


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256 and a random salt."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.urlsafe_b64decode(salt), int(rounds)
        )
        return hmac.compare_digest(base64.urlsafe_b64decode(expected), digest)
    except (TypeError, ValueError):
        return False


def token_hash(token: str) -> str:
    return hmac.new(settings.auth_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def create_login_session(user: dict) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)
    get_repository().save_auth_session(token_hash(token), user["id"], user["tenant_id"], expires.isoformat())
    return token


def _bearer(authorization: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


async def current_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    token = _bearer(authorization)
    session = get_repository().get_auth_session(token_hash(token)) if token else None
    if not session:
        raise HTTPException(status_code=401, detail="Login required")
    user = get_repository().get_user(session["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="User session is invalid")
    request.state.user_id = user["id"]
    request.state.tenant_id = user["tenant_id"]
    request.state.scopes = _role_scopes(user["role"])
    return user


async def current_admin_user(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required")
    return user


def user_tenant(user: dict) -> Tenant:
    return Tenant(user["tenant_id"], user["tenant_id"])


def ensure_bootstrap_admin() -> None:
    """Create the configured first tenant administrator once."""
    if not settings.admin_email or not settings.admin_password:
        return
    repository = get_repository()
    if repository.count_users() > 0:
        return
    try:
        repository.create_tenant("Default", "default")
    except Exception:
        # A concurrent worker may have created the same tenant.
        pass
    repository.create_user(
        "default",
        settings.admin_email,
        "Administrator",
        hash_password(settings.admin_password),
        role="admin",
    )
