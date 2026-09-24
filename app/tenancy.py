"""Tenant authentication and request-scoped tenant identity."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request

from .config import settings
from .database import get_repository
from .rate_limit import check_rate_limit


@dataclass(frozen=True, slots=True)
class Tenant:
    id: str
    name: str


def _role_scopes(role: str) -> frozenset[str]:
    """Map the current coarse roles onto Tool Catalog scopes."""
    if role == "admin":
        return frozenset({"tools:write", "tools:high-risk"})
    return frozenset()


def _configured_tenants() -> dict[str, tuple[Tenant, str]]:
    raw = settings.tenants_json.strip()
    if not raw and settings.api_key:
        raw = json.dumps([{"id": "default", "name": "Default", "api_key": settings.api_key}])
    if not raw:
        return {"": (Tenant("default", "Default"), "")}
    try:
        values = json.loads(raw)
        return {
            item["id"]: (Tenant(item["id"], item.get("name", item["id"])), item["api_key"])
            for item in values
            if item.get("id") and item.get("api_key")
        }
    except (TypeError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError("AIGC_LITE_TENANTS_JSON must be a JSON array with id and api_key") from exc


def _same_secret(left: str, right: str) -> bool:
    return hmac.compare_digest(hashlib.sha256(left.encode()).digest(), hashlib.sha256(right.encode()).digest())


async def current_tenant(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Tenant:
    token = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else ""
    if token:
        import hashlib
        import hmac

        session_hash = hmac.new(settings.auth_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
        session = get_repository().get_auth_session(session_hash)
        if session:
            user = get_repository().get_user(session["user_id"])
            if user:
                check_rate_limit(user["tenant_id"])
                request.state.user_id = user["id"]
                request.state.tenant_id = user["tenant_id"]
                request.state.scopes = _role_scopes(user["role"])
                return Tenant(user["tenant_id"], user["tenant_id"])
    configured = _configured_tenants()
    if list(configured) == [""]:
        check_rate_limit("default")
        tenant = configured[""][0]
        request.state.tenant_id = tenant.id
        request.state.scopes = frozenset()
        return tenant
    token = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else ""
    for tenant, secret in configured.values():
        if _same_secret(token, secret):
            check_rate_limit(tenant.id)
            request.state.tenant_id = tenant.id
            request.state.scopes = frozenset({"tools:write", "tools:high-risk"})
            return tenant
    raise HTTPException(status_code=401, detail="Invalid API key")
