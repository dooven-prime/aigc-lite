import asyncio

from app.core.contracts import RequestContext, ToolSource, ToolSpec
from app.core.errors import MCPProbeErrorCode
from app.repository import SQLiteRepository
from app.services.mcp_probe import MCPProbeService, classify_probe_error


def _server(timeout_seconds: float = 1) -> dict:
    return {
        "provider_id": "research",
        "url": "https://mcp.example.test/mcp",
        "header_credentials": {},
        "risk": "low",
        "required_scopes": [],
        "timeout_seconds": timeout_seconds,
        "enabled": True,
    }


class HealthyProvider:
    async def list_tools(self):
        return [
            ToolSpec(
                name="research__lookup",
                native_name="lookup",
                description="Lookup",
                input_schema={"type": "object"},
                source=ToolSource.MCP,
                provider_id="research",
            )
        ]


class SlowProvider:
    async def list_tools(self):
        await asyncio.sleep(1)
        return []


def test_probe_persists_latest_safe_health_projection(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "probe.db")
    repository.init()
    saved = repository.save_mcp_server("workspace-a", _server())
    context = RequestContext(request_id="request-1", workspace_id="workspace-a")
    service = MCPProbeService(
        repository_provider=lambda: repository,
        provider_factory=lambda _record, _workspace: HealthyProvider(),
    )

    result = asyncio.run(service.probe(context, saved["id"]))

    assert result["status"] == "healthy"
    assert result["tool_count"] == 1
    assert result["error_code"] is None
    stored = repository.get_mcp_server("workspace-a", saved["id"])
    assert stored["health_status"] == "healthy"
    assert stored["last_tool_count"] == 1
    assert stored["last_tested_at"]


def test_probe_timeout_is_stable_and_workspace_scoped(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "probe-timeout.db")
    repository.init()
    saved = repository.save_mcp_server("workspace-a", _server(0.01))
    service = MCPProbeService(
        repository_provider=lambda: repository,
        provider_factory=lambda _record, _workspace: SlowProvider(),
    )

    result = asyncio.run(
        service.probe(
            RequestContext(request_id="request-1", workspace_id="workspace-a"),
            saved["id"],
        )
    )

    assert result["status"] == "unhealthy"
    assert result["error_code"] == "timeout"
    assert result["tool_count"] is None


def test_probe_error_classification_does_not_expose_exception_text() -> None:
    assert classify_probe_error(ConnectionError("token=private")) is MCPProbeErrorCode.UNREACHABLE
    assert classify_probe_error(RuntimeError("401 Authorization private")) is MCPProbeErrorCode.AUTH_FAILED
    assert classify_probe_error(RuntimeError("protocol version mismatch")) is MCPProbeErrorCode.PROTOCOL_MISMATCH
