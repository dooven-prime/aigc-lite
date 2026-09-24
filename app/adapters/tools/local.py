"""Adapter from the existing explicit Python registry to ToolProvider."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

from ...config import settings
from ...core.contracts import ToolProviderResult, ToolSource, ToolSpec
from ...core.errors import ErrorCode
from ...tools import TOOLS, _function_schema, registration


class LocalToolProvider:
    provider_id = "local"
    is_remote = False

    async def list_tools(self) -> list[ToolSpec]:
        values = []
        for name, function in TOOLS.items():
            policy = registration(name)[1]
            schema = _function_schema(name, function)["function"]
            values.append(
                ToolSpec(
                    name=name,
                    native_name=name,
                    description=schema["description"],
                    input_schema=schema["parameters"],
                    source=ToolSource.LOCAL,
                    provider_id=self.provider_id,
                    risk=policy["risk"],
                    required_scopes=policy["required_scopes"],
                    timeout_seconds=settings.default_tool_timeout_seconds,
                )
            )
        return values

    async def call_tool(
        self, native_name: str, arguments: dict[str, Any]
    ) -> ToolProviderResult:
        registered = registration(native_name)
        if registered is None:
            return ToolProviderResult(
                content=json.dumps({"error": ErrorCode.TOOL_NOT_AVAILABLE.value}),
                failed=True,
            )
        function, _policy = registered
        try:
            if inspect.iscoroutinefunction(function):
                result = await function(**arguments)
            else:
                result = await asyncio.to_thread(function, **arguments)
            return ToolProviderResult(
                content=json.dumps(result, ensure_ascii=False, default=str)
            )
        except Exception:  # noqa: BLE001 - the model receives a stable failure only
            return ToolProviderResult(
                content=json.dumps({"error": ErrorCode.TOOL_EXECUTION_FAILED.value}),
                failed=True,
            )
