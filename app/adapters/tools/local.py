"""Adapter from the existing explicit Python registry to ToolProvider."""

from __future__ import annotations

import json
from typing import Any

from ...adapters.tools.execution import (
    ToolExecutionBackends,
    default_execution_backends,
)
from ...config import settings
from ...core.contracts import ToolProviderResult, ToolSource, ToolSpec
from ...core.errors import ErrorCode
from ...tools import TOOLS, _function_schema, registration


class LocalToolProvider:
    provider_id = "local"
    is_remote = False

    def __init__(
        self, execution_backends: ToolExecutionBackends | None = None
    ) -> None:
        self._execution_backends = execution_backends or default_execution_backends

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
                    timeout_seconds=(
                        policy["timeout_seconds"]
                        or settings.default_tool_timeout_seconds
                    ),
                    execution_mode=policy["execution_mode"],
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
        function, policy = registered
        try:
            return await self._execution_backends.execute(
                policy["execution_mode"], function, arguments
            )
        except Exception:  # noqa: BLE001 - the model receives a stable failure only
            return ToolProviderResult(
                content=json.dumps({"error": ErrorCode.TOOL_EXECUTION_FAILED.value}),
                failed=True,
                metadata={
                    "execution_mode": policy["execution_mode"].value,
                    "cancellation_mode": policy[
                        "execution_mode"
                    ].cancellation_mode,
                    "error_code": ErrorCode.TOOL_EXECUTION_FAILED.value,
                },
            )
