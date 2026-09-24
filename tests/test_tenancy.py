import asyncio
import json

from starlette.requests import Request

from app.tenancy import _configured_tenants, current_tenant


def test_single_api_key_becomes_default_tenant(monkeypatch) -> None:
    from app import tenancy

    monkeypatch.setattr(tenancy.settings, "api_key", "local-secret")
    monkeypatch.setattr(tenancy.settings, "tenants_json", "")
    configured = _configured_tenants()
    assert configured["default"][0].id == "default"


def test_multiple_tenants_are_loaded(monkeypatch) -> None:
    from app import tenancy

    values = [{"id": "a", "name": "Alpha", "api_key": "a-secret"}]
    monkeypatch.setattr(tenancy.settings, "api_key", "")
    monkeypatch.setattr(tenancy.settings, "tenants_json", json.dumps(values))
    assert _configured_tenants()["a"][0].name == "Alpha"


def test_configured_tenant_key_receives_tool_admin_scopes(monkeypatch) -> None:
    from app import tenancy

    values = [{"id": "a", "name": "Alpha", "api_key": "a-secret"}]
    monkeypatch.setattr(tenancy.settings, "api_key", "")
    monkeypatch.setattr(tenancy.settings, "tenants_json", json.dumps(values))
    monkeypatch.setattr(tenancy, "check_rate_limit", lambda _tenant_id: None)
    request = Request({"type": "http", "headers": []})

    tenant = asyncio.run(current_tenant(request, "Bearer a-secret"))

    assert tenant.id == "a"
    assert request.state.scopes == frozenset(
        {"tools:write", "tools:high-risk"}
    )
