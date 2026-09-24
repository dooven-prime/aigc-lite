"""Provider boundary for discoverable and callable tools."""

from __future__ import annotations

from typing import Any, Protocol

from ..core.contracts import RequestContext, ToolProviderResult, ToolSpec


class ToolProvider(Protocol):
    provider_id: str

    async def list_tools(self) -> list[ToolSpec]: ...

    async def call_tool(
        self, native_name: str, arguments: dict[str, Any]
    ) -> ToolProviderResult: ...


class ToolProviderSource(Protocol):
    """Load workspace-specific providers for a discovery snapshot."""

    source_id: str

    def list_providers(self, context: RequestContext) -> list[ToolProvider]: ...
