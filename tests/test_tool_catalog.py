import asyncio
import json
from typing import Any

from app.adapters.tools.local import LocalToolProvider
from app.core.contracts import (
    RequestContext,
    ToolProviderResult,
    ToolRisk,
    ToolSource,
    ToolSpec,
)
from app.services.tool_catalog import ToolCatalog
from app.tools import tool


@tool("catalog_status")
def catalog_status(verbose: bool = False) -> dict:
    """Return catalog status."""
    return {"status": "ok", "verbose": verbose}


@tool("catalog_danger", risk=ToolRisk.HIGH)
def catalog_danger() -> dict:
    """Represent a high-risk operation for policy tests."""
    return {"ran": True}


def test_catalog_filters_risk_and_returns_ledger_projection() -> None:
    async def run():
        catalog = ToolCatalog([LocalToolProvider()])
        ordinary = await catalog.open(
            RequestContext(request_id="r1", workspace_id="workspace-a")
        )
        elevated = await catalog.open(
            RequestContext(
                request_id="r2",
                workspace_id="workspace-a",
                scopes=frozenset({"tools:high-risk"}),
            )
        )
        result = await ordinary.invoke("catalog_status", '{"verbose":true}')
        denied = await ordinary.invoke("catalog_danger", "{}")
        return ordinary, elevated, result, denied

    ordinary, elevated, result, denied = asyncio.run(run())
    assert "catalog_status" in {spec.name for spec in ordinary.specs}
    assert "catalog_danger" not in {spec.name for spec in ordinary.specs}
    assert "catalog_danger" in {spec.name for spec in elevated.specs}
    assert json.loads(result.content) == {"status": "ok", "verbose": True}
    assert result.metadata["source"] == "local"
    assert result.ledger_input == '{"verbose": true}'
    assert denied.failed
    assert json.loads(denied.content) == {"error": "tool_not_available"}


class BrokenDiscoveryProvider:
    provider_id = "broken-discovery"

    async def list_tools(self) -> list[ToolSpec]:
        raise ConnectionError("secret transport detail")

    async def call_tool(
        self, native_name: str, arguments: dict[str, Any]
    ) -> ToolProviderResult:
        raise AssertionError("unreachable")


class BrokenCallProvider:
    provider_id = "broken-call"

    async def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="broken-call__lookup",
                native_name="lookup",
                description="Lookup a record.",
                input_schema={"type": "object"},
                source=ToolSource.MCP,
                provider_id=self.provider_id,
            )
        ]

    async def call_tool(
        self, native_name: str, arguments: dict[str, Any]
    ) -> ToolProviderResult:
        raise ConnectionError("authorization=secret")


def test_catalog_isolates_provider_discovery_failure() -> None:
    async def run():
        catalog = ToolCatalog([BrokenDiscoveryProvider(), LocalToolProvider()])
        return await catalog.open(
            RequestContext(request_id="r1", workspace_id="workspace-a")
        )

    session = asyncio.run(run())
    assert "catalog_status" in {spec.name for spec in session.specs}
    assert session.discovery_errors == {
        "broken-discovery": "tool_provider_unavailable"
    }


def test_catalog_turns_remote_call_failure_into_safe_step_projection() -> None:
    async def run():
        catalog = ToolCatalog([BrokenCallProvider()])
        session = await catalog.open(
            RequestContext(request_id="r1", workspace_id="workspace-a")
        )
        return await session.invoke(
            "broken-call__lookup", '{"token":"secret", "query":"hello"}'
        )

    result = asyncio.run(run())
    assert result.failed
    assert json.loads(result.content) == {"error": "tool_provider_unavailable"}
    assert json.loads(result.ledger_input) == {"token": "***", "query": "hello"}
    assert "secret" not in result.ledger_output
    assert result.metadata["source"] == "mcp"
    assert result.metadata["error_code"] == "tool_provider_unavailable"


class SlowProvider:
    provider_id = "slow"

    async def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="slow__wait",
                native_name="wait",
                description="Wait too long.",
                input_schema={"type": "object"},
                source=ToolSource.MCP,
                provider_id=self.provider_id,
                timeout_seconds=0.01,
            )
        ]

    async def call_tool(
        self, native_name: str, arguments: dict[str, Any]
    ) -> ToolProviderResult:
        await asyncio.sleep(1)
        return ToolProviderResult(content="unreachable")


def test_catalog_projects_tool_timeout_as_stable_failure() -> None:
    async def run():
        session = await ToolCatalog([SlowProvider()]).open(
            RequestContext(request_id="r1", workspace_id="workspace-a")
        )
        return await session.invoke("slow__wait", "{}")

    result = asyncio.run(run())
    assert result.failed
    assert json.loads(result.content) == {"error": "tool_timeout"}
    assert result.metadata["error_code"] == "tool_timeout"
