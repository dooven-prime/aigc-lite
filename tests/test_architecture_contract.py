import json
import sqlite3

from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import database, main, tenancy
from app.core.contracts import ChatResult


def test_current_http_surface_remains_available() -> None:
    paths = set(main.app.openapi()["paths"])
    assert {
        "/health",
        "/api/auth/register",
        "/api/auth/login",
        "/api/auth/me",
        "/api/sessions",
        "/api/sessions/{session_id}",
        "/api/chat",
        "/api/chat/stream",
        "/api/runs",
        "/api/runs/{run_id}",
        "/api/runs/{run_id}/cancel",
        "/api/search",
        "/api/knowledge/documents",
        "/api/knowledge/upload",
        "/api/knowledge/search",
        "/api/models",
        "/api/usage",
        "/api/audit",
        "/api/credentials",
        "/api/credentials/{credential_id}",
        "/api/credentials/{credential_id}/replace",
        "/api/mcp-servers/{server_id}/probe",
        "/mcp-legacy",
    } <= paths


def test_sessions_and_documents_are_isolated_across_tenant_api_keys(
    tmp_path, monkeypatch
) -> None:
    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "tenant-contract.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    tenants = [
        {"id": "tenant-a", "name": "Alpha", "api_key": "alpha-key"},
        {"id": "tenant-b", "name": "Beta", "api_key": "beta-key"},
    ]
    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    monkeypatch.setattr(tenancy.settings, "api_key", "")
    monkeypatch.setattr(tenancy.settings, "tenants_json", json.dumps(tenants))
    alpha = {"Authorization": "Bearer alpha-key"}
    beta = {"Authorization": "Bearer beta-key"}

    with TestClient(main.app) as client:
        session_id = client.post(
            "/api/sessions", headers=alpha, json={"title": "Alpha only"}
        ).json()["id"]
        document = client.post(
            "/api/knowledge/documents",
            headers=alpha,
            json={"name": "alpha.md", "content": "tenant isolation marker"},
        )
        assert document.status_code == 200

        assert client.get(f"/api/sessions/{session_id}", headers=alpha).status_code == 200
        assert client.get(f"/api/sessions/{session_id}", headers=beta).status_code == 404
        assert client.get("/api/sessions", headers=beta).json() == []
        assert client.get(
            "/api/knowledge/search?q=isolation", headers=beta
        ).json() == []
        alpha_results = client.get(
            "/api/knowledge/search?q=isolation", headers=alpha
        ).json()
        assert [item["name"] for item in alpha_results] == ["alpha.md"]


def test_mcp_mounts_fail_closed_when_transport_key_is_configured(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "mcp_api_key", "mcp-test-key")

    with TestClient(main.app) as client:
        assert client.post("/mcp", json={}).status_code == 401
        assert client.post(
            "/mcp", headers={"Authorization": "Bearer wrong-key"}, json={}
        ).status_code == 401
        assert client.get("/mcp-sse/sse").status_code == 401


def test_mcp_middleware_resolves_tenant_key_to_workspace_context(monkeypatch) -> None:
    async def context_view(request):
        context = request.state.mcp_context
        return JSONResponse(
            {
                "workspace_id": context.workspace_id,
                "scopes": sorted(context.scopes),
                "tool_hops": context.tool_hops,
            }
        )

    inner = Starlette(routes=[Route("/mcp", context_view)])
    wrapped = main.MCPAuthMiddleware(inner)
    monkeypatch.setattr(main.settings, "mcp_api_key", "")
    monkeypatch.setattr(main.settings, "api_key", "")
    monkeypatch.setattr(
        main.settings,
        "tenants_json",
        json.dumps(
            [{"id": "workspace-a", "name": "Alpha", "api_key": "alpha-key"}]
        ),
    )
    monkeypatch.setattr(tenancy, "check_rate_limit", lambda _tenant_id: None)

    with TestClient(wrapped) as client:
        response = client.get(
            "/mcp",
            headers={
                "Authorization": "Bearer alpha-key",
                "X-AIGC-Lite-MCP-Hop": "1",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "workspace_id": "workspace-a",
        "scopes": ["tools:high-risk", "tools:write"],
        "tool_hops": 1,
    }


def test_chat_route_delegates_to_gateway_service(monkeypatch) -> None:
    captured: dict = {}

    class FakeGatewayService:
        async def chat(self, command, context) -> ChatResult:
            captured["command"] = command
            captured["context"] = context
            return ChatResult(
                content="from service", session_id="session-1", run_id="run-1"
            )

    monkeypatch.setattr(main, "gateway_service", FakeGatewayService())

    with TestClient(main.app) as client:
        response = client.post(
            "/api/chat",
            json={"prompt": "hello", "system": "Be concise.", "model": "public-model"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "content": "from service",
        "session_id": "session-1",
        "run_id": "run-1",
    }
    assert captured["command"].prompt == "hello"
    assert captured["command"].requested_model == "public-model"
    assert captured["context"].workspace_id == "default"
    assert captured["context"].request_id


def test_chat_route_projects_stable_application_error(tmp_path, monkeypatch) -> None:
    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "chat-errors.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/chat",
            json={"prompt": "hello", "session_id": "missing-session"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Session not found",
        "error": {"code": "resource_not_found", "retryable": False},
    }
