"""Official MCP transport projection for robot capabilities."""

from __future__ import annotations

import json
import secrets
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .config import BridgeSettings
from .service import CAPABILITIES, RobotCapabilityService


class RobotMCPServer(MCPServer):
    def __init__(self, service: RobotCapabilityService, settings: BridgeSettings) -> None:
        self._service = service
        self._bridge_settings = settings
        super().__init__(
            "aigc-lite-ros2",
            version=__version__,
            instructions=(
                "Use only the declared bounded robot capabilities. MCP cancellation is not "
                "an emergency-stop channel."
            ),
        )

        @self.custom_route("/health", ["GET"])
        async def health(request: Request) -> JSONResponse:
            del request
            return JSONResponse(
                {
                    "status": "ok",
                    "service": "aigc-lite-ros2",
                    "version": __version__,
                }
            )

    async def _handle_list_tools(self, ctx, params) -> ListToolsResult:
        del ctx, params
        return ListToolsResult(
            cacheScope="private",
            ttlMs=5_000,
            tools=[
                Tool(
                    name=spec.name,
                    description=spec.description,
                    inputSchema=spec.input_schema,
                    annotations=ToolAnnotations(
                        readOnlyHint=spec.read_only,
                        destructiveHint=spec.destructive,
                        idempotentHint=spec.idempotent,
                        openWorldHint=True,
                    ),
                    _meta=spec.metadata_for(self._bridge_settings.robot_id),
                )
                for spec in CAPABILITIES
            ],
        )

    async def _handle_call_tool(self, ctx, params) -> CallToolResult:
        del ctx
        result = await self._service.invoke(params.name, params.arguments)
        content = json.dumps(result.payload, ensure_ascii=False, default=str)
        return CallToolResult(
            content=[TextContent(type="text", text=content)],
            structuredContent=result.payload,
            isError=result.failed,
            _meta={
                "aigc-lite": {
                    "robot_id": self._bridge_settings.robot_id,
                    "environment": self._bridge_settings.environment.value,
                }
            },
        )


def create_server(
    service: RobotCapabilityService,
    settings: BridgeSettings,
) -> RobotMCPServer:
    return RobotMCPServer(service, settings)


class APIKeyASGI:
    """Minimal transport guard for a separately deployed physical bridge."""

    def __init__(self, app: Any, api_key: str) -> None:
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope, receive, send) -> None:
        if self.api_key and scope["type"] == "http" and scope["path"] != "/health":
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            authorization = headers.get("authorization", "")
            token = (
                authorization[7:].strip()
                if authorization.lower().startswith("bearer ")
                else headers.get("x-api-key", "")
            )
            if not secrets.compare_digest(token, self.api_key):
                response = JSONResponse(
                    {"error": "bridge_authentication_required"}, status_code=401
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
