import json
import sqlite3

from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from app import database, main, tenancy
from app.core.contracts import ChatResult
from app.ports.enforcement_repository import EnforcementRepository
from app.ports.import_repository import ImportRepository
from app.ports.qualification_repository import QualificationRepository
from app.ports.research_repository import ResearchRepository
from app.ports.review_repository import ReviewRepository
from app.repository import PostgresRepository, Repository, SQLiteRepository


def test_current_http_surface_remains_available() -> None:
    paths = set(main.app.openapi()["paths"])
    assert {
        "/health",
        "/ready",
        "/api/auth/register",
        "/api/auth/login",
        "/api/auth/me",
        "/api/sessions",
        "/api/sessions/{session_id}",
        "/api/chat",
        "/api/chat/stream",
        "/api/chat/capability-sets",
        "/api/conversation-importers",
        "/api/conversation-imports/preview",
        "/api/conversation-imports",
        "/api/conversation-imports/{batch_id}",
        "/api/runs",
        "/api/runs/{run_id}",
        "/api/runs/{run_id}/cancel",
        "/api/artifacts",
        "/api/artifacts/{artifact_id}",
        "/api/schedules",
        "/api/schedules/{task_id}",
        "/api/schedules/{task_id}/pause",
        "/api/schedules/{task_id}/resume",
        "/api/schedules/{task_id}/cancel",
        "/api/research-registry/claims/{claim_id}/verification-plans",
        "/api/research-registry/verification-plans/{plan_id}/runs",
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
        "/api/reviews/profiles",
        "/api/reviews/runs/{run_id}",
        "/api/reviews",
        "/api/reviews/{review_run_id}",
        "/api/review-findings",
        "/api/review-findings/{finding_id}",
        "/api/enforcement/issuers",
        "/api/enforcement/adapters",
        "/api/enforcement/tool-bindings",
        "/api/enforcement/dispatches",
        "/api/enforcement/dispatches/{dispatch_id}",
        "/api/enforcement/dispatches/{dispatch_id}/reconcile",
        "/api/enforcement/verification-keys",
        "/api/enforcement/policy-proposals",
        "/api/enforcement/policy-proposals/{proposal_id}",
        "/api/enforcement/receipts",
        "/api/enforcement/receipts/{receipt_id}",
        "/api/enforcement/receipts/{receipt_id}/signature-verification",
        "/api/qualification/knowledge-admission-policies",
        "/api/qualification/knowledge-admissions",
        "/api/qualification/receipts/{receipt_id}/knowledge-admissions",
        "/mcp-legacy",
    } <= paths


def test_chat_capability_registry_is_exposed_as_read_only_metadata() -> None:
    with TestClient(main.app) as client:
        response = client.get("/api/chat/capability-sets")

    assert response.status_code == 200
    values = {item["capability_set_id"]: item for item in response.json()}
    assert set(values) == {"chat.read-only.v1", "chat.delegated.v1"}
    assert values["chat.read-only.v1"]["preserve_caller_scopes"] is False
    assert values["chat.delegated.v1"]["required_caller_scopes"] == [
        "tools:write"
    ]
    assert all(len(item["capability_policy_hash"]) == 64 for item in values.values())


def test_research_control_plane_is_owned_by_domain_router() -> None:
    route_modules = {
        route.path: route.endpoint.__module__
        for route in main.research_router.routes
        if hasattr(route, "endpoint")
    }
    assert {
        route_modules[path]
        for path in {
            "/api/research-registry",
            "/api/research-registry/import/frontier",
            "/api/decision-lab",
            "/api/qualification/math-theorems",
            "/api/qualification/claims/{claim_id}/evaluations",
            "/api/qualification/receipts/{receipt_id}/knowledge-admissions",
            "/api/authorization-grants",
        }
    } == {"app.api.research"}


def test_review_workbench_is_owned_by_domain_router() -> None:
    route_modules = {
        route.path: route.endpoint.__module__
        for route in main.review_router.routes
        if hasattr(route, "endpoint")
    }
    assert {
        route_modules[path]
        for path in {
            "/api/reviews/profiles",
            "/api/reviews/runs/{run_id}",
            "/api/reviews/{review_run_id}",
            "/api/review-findings/{finding_id}",
        }
    } == {"app.api.reviews"}


def test_conversation_imports_are_owned_by_domain_router() -> None:
    route_modules = {
        route.path: route.endpoint.__module__
        for route in main.conversation_import_router.routes
        if hasattr(route, "endpoint")
    }
    assert {
        route_modules[path]
        for path in {
            "/api/conversation-importers",
            "/api/conversation-imports/preview",
            "/api/conversation-imports",
            "/api/conversation-imports/{batch_id}",
        }
    } == {"app.api.conversation_imports"}


def test_execution_authority_evidence_is_owned_by_domain_router() -> None:
    route_modules = {
        route.path: route.endpoint.__module__
        for route in main.enforcement_router.routes
        if hasattr(route, "endpoint")
    }
    assert {
        route_modules[path]
        for path in {
            "/api/enforcement/issuers",
            "/api/enforcement/adapters",
            "/api/enforcement/tool-bindings",
            "/api/enforcement/dispatches",
            "/api/enforcement/dispatches/{dispatch_id}",
            "/api/enforcement/dispatches/{dispatch_id}/reconcile",
            "/api/enforcement/verification-keys",
            "/api/enforcement/policy-proposals",
            "/api/enforcement/policy-proposals/{proposal_id}",
            "/api/enforcement/receipts",
            "/api/enforcement/receipts/{receipt_id}",
            "/api/enforcement/receipts/{receipt_id}/signature-verification",
        }
    } == {"app.api.enforcement"}


def test_research_repository_domains_are_composed_into_compatibility_facade() -> None:
    assert ResearchRepository in Repository.__mro__
    assert QualificationRepository in Repository.__mro__
    assert ReviewRepository in Repository.__mro__
    assert ImportRepository in Repository.__mro__
    assert EnforcementRepository in Repository.__mro__
    domains = (
        (ResearchRepository, "app.repositories.research"),
        (QualificationRepository, "app.repositories.qualification"),
        (ReviewRepository, "app.repositories.review"),
        (ImportRepository, "app.repositories.conversation_imports"),
        (EnforcementRepository, "app.repositories.enforcement"),
    )
    for port, module in domains:
        methods = {
            name
            for name, value in port.__dict__.items()
            if not name.startswith("_") and callable(value)
        }
        assert methods
        for implementation in (SQLiteRepository, PostgresRepository):
            assert {
                getattr(implementation, method).__module__ for method in methods
            } == {module}


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


def test_mcp_mount_root_accepts_post_without_redirect(monkeypatch) -> None:
    async def protocol_view(_request):
        return JSONResponse({"transport": "streamable-http"})

    mounted = Starlette(
        routes=[
            Mount(
                "/mcp",
                app=Starlette(
                    routes=[Route("/", protocol_view, methods=["POST"])]
                ),
            )
        ]
    )
    wrapped = main.MCPAuthMiddleware(mounted)
    monkeypatch.setattr(main.settings, "mcp_api_key", "mcp-test-key")

    with TestClient(wrapped, follow_redirects=False) as client:
        headers = {"Authorization": "Bearer mcp-test-key"}
        compatibility = client.post("/mcp", headers=headers, json={})
        canonical = client.post("/mcp/", headers=headers, json={})

    assert compatibility.status_code == 200
    assert compatibility.headers.get("location") is None
    assert compatibility.history == []
    assert compatibility.json() == {"transport": "streamable-http"}
    assert canonical.status_code == 200
    assert canonical.history == []


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

    inner = Starlette(routes=[Route("/mcp/", context_view)])
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
            json={
                "prompt": "hello",
                "system": "Be concise.",
                "model": "public-model",
                "capability_set_id": "chat.read-only.v1",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "content": "from service",
        "session_id": "session-1",
        "run_id": "run-1",
    }
    assert captured["command"].prompt == "hello"
    assert captured["command"].requested_model == "public-model"
    assert captured["command"].capability_set_id == "chat.read-only.v1"
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
