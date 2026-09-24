"""MCP SDK runtime plus a small outbound Streamable HTTP client."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from .config import settings
from .core.contracts import RequestContext
from .services.tools import ToolService
from .tools import invoke, schemas

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings
except ImportError:  # pragma: no cover - package metadata provides the dependency
    MCPServer = None
    TransportSecuritySettings = None


def handle_rpc(request: dict[str, Any]) -> dict[str, Any]:
    """Handle the legacy JSON-RPC shape retained for simple integrations."""
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    if request.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "Invalid Request"}}
    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "params must be an object"}}
    if method == "initialize":
        result = {
            "protocolVersion": settings.legacy_mcp_protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": settings.app_name, "version": "0.2.0"},
        }
    elif method == "notifications/initialized":
        return {}
    elif method == "tools/list":
        result = {
            "tools": [
                {"name": item["function"]["name"], "description": item["function"]["description"],
                 "inputSchema": item["function"]["parameters"]}
                for item in schemas()
            ]
        }
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class CatalogMCPServer(MCPServer if MCPServer is not None else object):
    """Official MCP server whose tool surface is projected from Tool Catalog."""

    def __init__(self, tool_service: ToolService):
        if MCPServer is None:  # pragma: no cover
            raise RuntimeError("Install the 'mcp' dependency to enable the MCP transport")
        self._tool_service = tool_service
        super().__init__(
            settings.app_name,
            version="0.3.0",
            instructions="Use the workspace-authorized tools for workspace tasks.",
        )

    async def _handle_list_tools(self, ctx, params) -> ListToolsResult:
        del params
        context = _request_context(ctx)
        session = await self._tool_service.discover(context)
        return ListToolsResult(
            cacheScope="private",
            ttlMs=0,
            tools=[
                Tool(
                    name=spec.name,
                    description=spec.description,
                    inputSchema=spec.input_schema,
                    _meta={
                        "aigc-lite": {
                            "source": spec.source.value,
                            "provider_id": spec.provider_id,
                            "risk": spec.risk.value,
                            **(
                                {
                                    "execution_mode": spec.execution_mode.value,
                                    "cancellation_mode": (
                                        spec.execution_mode.cancellation_mode
                                    ),
                                }
                                if spec.execution_mode is not None
                                else {}
                            ),
                        }
                    },
                )
                for spec in session.specs
            ],
        )

    async def _handle_call_tool(self, ctx, params) -> CallToolResult:
        context = _request_context(ctx)
        result, run_id = await self._tool_service.invoke(
            context,
            params.name,
            params.arguments or {},
            transport="mcp",
        )
        structured = None
        try:
            decoded = json.loads(result.content)
            if isinstance(decoded, (dict, list)):
                structured = decoded
        except json.JSONDecodeError:
            pass
        return CallToolResult(
            content=[TextContent(type="text", text=result.content)],
            structuredContent=structured,
            isError=result.failed,
            _meta={"aigc-lite": {"run_id": run_id}},
        )


def _request_context(ctx) -> RequestContext:
    request = getattr(ctx, "request", None)
    if request is not None:
        context = getattr(request.state, "mcp_context", None)
        if isinstance(context, RequestContext):
            return context
    return RequestContext(
        request_id=str(getattr(ctx, "request_id", None) or uuid4()),
        workspace_id="default",
    )


def create_mcp_server(tool_service: ToolService | None = None):
    """Build the official SDK server as a Tool Catalog transport projection."""
    if MCPServer is None:
        raise RuntimeError("Install the optional 'mcp' dependency to enable the MCP SDK transport")
    return CatalogMCPServer(tool_service or ToolService())


@asynccontextmanager
async def mcp_lifespan(server) -> AsyncIterator[None]:
    """Run one SDK session manager for the mounted app lifetime."""
    async with server.session_manager.run():
        yield


def build_transport_apps(server):
    """Return Streamable HTTP and legacy SSE ASGI applications."""
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[item.strip() for item in settings.mcp_allowed_hosts.split(",") if item.strip()],
        allowed_origins=[item.strip() for item in settings.mcp_allowed_origins.split(",") if item.strip()],
    )
    return (
        server.streamable_http_app(
            streamable_http_path="/",
            host=settings.host,
            max_request_body_size=settings.max_body_bytes,
            transport_security=security,
        ),
        server.sse_app(
            sse_path="/sse",
            message_path="/messages/",
            host=settings.host,
            transport_security=security,
        ),
    )


async def call_rpc(request: dict[str, Any], url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, json=request, headers=headers or {})
        response.raise_for_status()
        return response.json()


class MCPClient:
    """Outbound client for a remote Streamable HTTP MCP endpoint."""

    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self.url = url
        self.headers = headers or {}

    async def call(self, request: dict[str, Any]) -> dict[str, Any]:
        return await call_rpc(request, self.url, self.headers)


async def call_local_tool(request: dict[str, Any]) -> dict[str, Any]:
    params = request.get("params") or {}
    result = await invoke(params.get("name", ""), json.dumps(params.get("arguments") or {}))
    return {"jsonrpc": "2.0", "id": request.get("id"), "result": {"content": [{"type": "text", "text": result}]}}
