"""Run the standalone bridge over MCP Streamable HTTP."""

from __future__ import annotations

import asyncio

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

from .config import BridgeSettings
from .nav2 import Nav2Backend
from .server import APIKeyASGI, create_server
from .service import RobotCapabilityService
from .simulator import SimulatorBackend


def _backend(settings: BridgeSettings):
    if settings.backend == "simulator":
        return SimulatorBackend(
            robot_id=settings.robot_id,
            map_id=settings.map_id,
            travel_seconds=settings.sim_travel_seconds,
            outcome=settings.sim_outcome,
        )
    return Nav2Backend(
        robot_id=settings.robot_id,
        environment=settings.environment,
        map_id=settings.map_id,
        base_frame=settings.base_frame,
        global_frame=settings.global_frame,
        action_name=settings.nav2_action_name,
    )


async def _run(settings: BridgeSettings) -> None:
    backend = _backend(settings)
    server = create_server(RobotCapabilityService(backend), settings)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.allowed_hosts),
        allowed_origins=list(settings.allowed_origins),
    )
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=security,
        host=settings.host,
    )
    guarded_app = APIKeyASGI(app, settings.api_key)
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(
            guarded_app,
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
    )
    try:
        await uvicorn_server.serve()
    finally:
        await backend.close()


def main() -> None:
    asyncio.run(_run(BridgeSettings.from_env()))


if __name__ == "__main__":
    main()
