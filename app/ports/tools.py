"""Provider boundary for discoverable and callable tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from ..core.contracts import (
    RequestContext,
    ToolExecutionMode,
    ToolProviderResult,
    ToolSpec,
)


class ToolExecutionBackend(Protocol):
    """Execute one local Python callable under a declared isolation mode."""

    mode: ToolExecutionMode

    async def execute(
        self, function: Callable[..., Any], arguments: dict[str, Any]
    ) -> ToolProviderResult: ...


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
