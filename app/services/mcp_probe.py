"""Workspace-scoped MCP health probes with stable, safe failure projection."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from time import monotonic

from ..adapters.tools.mcp import MCPToolProvider
from ..adapters.tools.repository import provider_from_record
from ..core.contracts import RequestContext
from ..core.errors import (
    CredentialNotConfiguredError,
    MCPProbeErrorCode,
    ResourceNotFoundError,
)
from ..database import get_repository
from ..repository import Repository, utc_now

RepositoryProvider = Callable[[], Repository]
ProviderFactory = Callable[[dict, str], MCPToolProvider]


def _causes(exc: BaseException):
    yield exc
    for nested in getattr(exc, "exceptions", ()):
        yield from _causes(nested)
    if exc.__cause__ is not None:
        yield from _causes(exc.__cause__)
    elif exc.__context__ is not None:
        yield from _causes(exc.__context__)


def classify_probe_error(exc: BaseException) -> MCPProbeErrorCode:
    causes = list(_causes(exc))
    if any(isinstance(item, CredentialNotConfiguredError) for item in causes):
        return MCPProbeErrorCode.AUTH_FAILED

    names = " ".join(type(item).__name__.casefold() for item in causes)
    detail = " ".join(str(item).casefold() for item in causes)
    if "timeout" in names or "timed out" in detail:
        return MCPProbeErrorCode.TIMEOUT
    if re.search(r"\b(?:401|403)\b", detail) or any(
        marker in detail
        for marker in ("unauthorized", "forbidden", "authentication", "invalid api key")
    ):
        return MCPProbeErrorCode.AUTH_FAILED
    if any(
        marker in detail
        for marker in (
            "protocol version",
            "protocol mismatch",
            "invalid json-rpc",
            "initialize response",
            "unsupported media type",
        )
    ):
        return MCPProbeErrorCode.PROTOCOL_MISMATCH
    if any(
        marker in names
        for marker in ("connecterror", "connectionerror", "networkerror", "dns")
    ) or any(
        marker in detail
        for marker in (
            "connection refused",
            "connection reset",
            "name or service not known",
            "nodename nor servname",
            "no route to host",
        )
    ):
        return MCPProbeErrorCode.UNREACHABLE
    return MCPProbeErrorCode.DISCOVERY_FAILED


class MCPProbeService:
    """Probe one stored server and retain only its latest safe health projection."""

    def __init__(
        self,
        repository_provider: RepositoryProvider = get_repository,
        provider_factory: ProviderFactory = provider_from_record,
    ) -> None:
        self._repository_provider = repository_provider
        self._provider_factory = provider_factory

    async def probe(self, context: RequestContext, server_id: str) -> dict:
        repository = self._repository_provider()
        server = repository.get_mcp_server(context.workspace_id, server_id)
        if server is None:
            raise ResourceNotFoundError("mcp_server", server_id)

        started = monotonic()
        try:
            provider = self._provider_factory(server, context.workspace_id)
            async with asyncio.timeout(float(server["timeout_seconds"])):
                tools = await provider.list_tools()
            projection = {
                "health_status": "healthy",
                "last_tested_at": utc_now(),
                "last_error_code": None,
                "last_latency_ms": round((monotonic() - started) * 1000),
                "last_tool_count": len(tools),
            }
        except Exception as exc:  # noqa: BLE001 - public result is a stable category only
            projection = {
                "health_status": "unhealthy",
                "last_tested_at": utc_now(),
                "last_error_code": classify_probe_error(exc).value,
                "last_latency_ms": round((monotonic() - started) * 1000),
                "last_tool_count": None,
            }
        updated = repository.record_mcp_probe(
            context.workspace_id, server_id, projection
        )
        return {
            "server_id": updated["id"],
            "provider_id": updated["provider_id"],
            "status": updated["health_status"],
            "tested_at": updated["last_tested_at"],
            "error_code": updated.get("last_error_code"),
            "latency_ms": updated.get("last_latency_ms"),
            "tool_count": updated.get("last_tool_count"),
        }
