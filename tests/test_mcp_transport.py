import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app import mcp as mcp_runtime
from app.adapters.tools.local import LocalToolProvider
from app.adapters.tools.mcp import MCPToolProvider
from app.core.contracts import (
    RequestContext,
    ToolProviderResult,
    ToolSource,
    ToolSpec,
)
from app.mcp import build_transport_apps, create_mcp_server
from app.repository import SQLiteRepository
from app.services.tool_catalog import ToolCatalog
from app.services.tools import ToolService
from app.tools import tool


@tool("transport_echo")
async def transport_echo(value: str) -> dict[str, str]:
    """Echo a value through the MCP transport."""
    return {"value": value}


def test_official_streamable_http_transport(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "official-mcp.db")
    repository.init()
    catalog = ToolCatalog([LocalToolProvider()])
    server = create_mcp_server(
        ToolService(repository_provider=lambda: repository, tool_catalog=catalog)
    )
    streamable_app, _ = build_transport_apps(server)

    async def run() -> tuple[str, dict[str, str]]:
        url = "http://127.0.0.1/"

        @asynccontextmanager
        async def transport():
            asgi_transport = httpx2.ASGITransport(app=streamable_app)
            async with httpx2.AsyncClient(
                transport=asgi_transport, base_url=url, headers={"host": "127.0.0.1:80"}
            ) as http_client:
                async with streamable_http_client(url, http_client=http_client) as streams:
                    yield streams

        async with server.session_manager.run():
            async with Client(transport()) as client:
                assert client.protocol_version == "2026-07-28"
                tools = await client.list_tools()
                result = await client.call_tool("transport_echo", {"value": "ok"})
                assert not result.is_error
                return tools.tools[0].name, json.loads(result.content[0].text)

    name, result = asyncio.run(run())
    assert name == "workspace_status"
    assert result == {"value": "ok"}


def test_legacy_rpc_protocol_version_is_explicitly_separate(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_runtime.settings,
        "legacy_mcp_protocol_version",
        "2025-06-18",
    )
    response = mcp_runtime.handle_rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert response["result"]["protocolVersion"] == "2025-06-18"


def test_remote_mcp_provider_uses_catalog_namespace_and_workspace_policy(
    tmp_path,
) -> None:
    repository = SQLiteRepository(tmp_path / "remote-provider.db")
    repository.init()
    server = create_mcp_server(
        ToolService(
            repository_provider=lambda: repository,
            tool_catalog=ToolCatalog([LocalToolProvider()]),
        )
    )
    streamable_app, _ = build_transport_apps(server)

    @asynccontextmanager
    async def transport():
        url = "http://127.0.0.1/"
        asgi_transport = httpx2.ASGITransport(app=streamable_app)
        async with httpx2.AsyncClient(
            transport=asgi_transport, base_url=url, headers={"host": "127.0.0.1:80"}
        ) as http_client:
            async with streamable_http_client(url, http_client=http_client) as streams:
                yield streams

    async def run():
        provider = MCPToolProvider(
            "research",
            "http://127.0.0.1/",
            workspace_id="workspace-a",
            transport_factory=transport,
        )
        catalog = ToolCatalog([provider])
        async with server.session_manager.run():
            allowed = await catalog.open(
                RequestContext(request_id="r1", workspace_id="workspace-a")
            )
            denied = await catalog.open(
                RequestContext(request_id="r2", workspace_id="workspace-b")
            )
            result = await allowed.invoke(
                "research__transport_echo", '{"value":"remote ok"}'
            )
        return allowed, denied, result

    allowed, denied, result = asyncio.run(run())
    assert "research__transport_echo" in {spec.name for spec in allowed.specs}
    assert denied.specs == []
    assert json.loads(result.content) == {"value": "remote ok"}
    assert result.metadata["source"] == "mcp"
    assert result.metadata["provider_id"] == "research"


class ProjectedRemoteProvider:
    provider_id = "research"
    is_remote = True

    async def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="research__lookup",
                native_name="lookup",
                description="Lookup a remote record.",
                input_schema={
                    "type": "object",
                    "properties": {"token": {"type": "string"}},
                },
                source=ToolSource.MCP,
                provider_id=self.provider_id,
                workspace_id="workspace-a",
            )
        ]

    async def call_tool(
        self, native_name: str, arguments: dict[str, Any]
    ) -> ToolProviderResult:
        return ToolProviderResult(
            content=json.dumps(
                {"token": arguments["token"], "source": "remote"}
            )
        )


class MCPContextApp:
    def __init__(self, app, context: RequestContext):
        self.app = app
        self.context = context

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope.setdefault("state", {})["mcp_context"] = self.context
        await self.app(scope, receive, send)


def test_inbound_mcp_projects_remote_catalog_and_records_safe_step(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "inbound-projection.db")
    repository.init()
    catalog = ToolCatalog([ProjectedRemoteProvider()])
    server = create_mcp_server(
        ToolService(repository_provider=lambda: repository, tool_catalog=catalog)
    )
    streamable_app, _ = build_transport_apps(server)

    def transport(context: RequestContext):
        @asynccontextmanager
        async def connect():
            url = "http://127.0.0.1/"
            app = MCPContextApp(streamable_app, context)
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url=url,
                headers={"host": "127.0.0.1:80"},
            ) as http_client:
                async with streamable_http_client(
                    url, http_client=http_client
                ) as streams:
                    yield streams

        return connect()

    async def run():
        direct_context = RequestContext(
            request_id="request-direct", workspace_id="workspace-a"
        )
        forwarded_context = RequestContext(
            request_id="request-forwarded",
            workspace_id="workspace-a",
            tool_hops=1,
        )
        async with server.session_manager.run():
            async with Client(transport(direct_context)) as client:
                discovered = await client.list_tools()
                result = await client.call_tool(
                    "research__lookup", {"token": "private-value"}
                )
            async with Client(transport(forwarded_context)) as client:
                forwarded = await client.list_tools()
        return discovered, result, forwarded

    discovered, result, forwarded = asyncio.run(run())
    assert [item.name for item in discovered.tools] == ["research__lookup"]
    assert not result.is_error
    assert result.structured_content == {
        "token": "private-value",
        "source": "remote",
    }
    assert forwarded.tools == []

    run = repository.list_runs("workspace-a")[0]
    assert result.meta["aigc-lite"]["run_id"] == run["id"]
    detail = repository.get_run("workspace-a", run["id"])
    assert detail["status"] == "succeeded"
    assert detail["session_id"] == ""
    assert detail["steps"][0]["kind"] == "tool"
    assert detail["steps"][0]["input_content"] == '{"token": "***"}'
    assert detail["steps"][0]["output_content"] == (
        '{"token": "***", "source": "remote"}'
    )
    assert detail["steps"][0]["metadata"]["transport"] == "mcp"
    assert detail["steps"][0]["metadata"]["source"] == "mcp"
    assert "private-value" not in str(detail)
