"""Application service for discoverable and observable tool execution."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ..core.contracts import (
    RequestContext,
    RunStatus,
    StepKind,
    StepStatus,
    ToolInvocationResult,
)
from ..core.errors import ErrorCode
from ..database import get_repository
from ..repository import Repository
from .tool_catalog import ToolCatalog, ToolSession, create_default_tool_catalog

RepositoryProvider = Callable[[], Repository]


class ToolService:
    """Project Tool Catalog discovery and calls into transport-neutral use cases."""

    def __init__(
        self,
        *,
        repository_provider: RepositoryProvider = get_repository,
        tool_catalog: ToolCatalog | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._tool_catalog = tool_catalog or create_default_tool_catalog(
            repository_provider
        )

    async def discover(self, context: RequestContext) -> ToolSession:
        return await self._tool_catalog.open(context)

    async def invoke(
        self,
        context: RequestContext,
        name: str,
        arguments: dict[str, Any],
        *,
        transport: str,
    ) -> tuple[ToolInvocationResult, str]:
        """Invoke a tool and persist one standalone Run/Step execution fact."""
        repository = self._repository_provider()
        run = repository.create_run(
            context.workspace_id,
            "",
            context.request_id,
            None,
            "tool-catalog",
        )
        try:
            session = await self._tool_catalog.open(context)
            result = await session.invoke(
                name, json.dumps(arguments, ensure_ascii=False, default=str)
            )
        except Exception:
            repository.append_run_step(
                context.workspace_id,
                run["id"],
                1,
                StepKind.TOOL.value,
                name,
                StepStatus.FAILED.value,
                "{}",
                "",
                {
                    "transport": transport,
                    "error_code": ErrorCode.INTERNAL_ERROR.value,
                },
            )
            repository.finish_run(
                context.workspace_id,
                run["id"],
                RunStatus.FAILED.value,
                ErrorCode.INTERNAL_ERROR.value,
            )
            raise

        metadata = {"transport": transport, **result.metadata}
        repository.append_run_step(
            context.workspace_id,
            run["id"],
            1,
            StepKind.TOOL.value,
            name,
            StepStatus.FAILED.value if result.failed else StepStatus.SUCCEEDED.value,
            result.ledger_input,
            result.ledger_output,
            metadata,
        )
        error_code = self._error_code(result) if result.failed else None
        repository.finish_run(
            context.workspace_id,
            run["id"],
            RunStatus.FAILED.value if result.failed else RunStatus.SUCCEEDED.value,
            error_code,
        )
        return result, run["id"]

    @staticmethod
    def _error_code(result: ToolInvocationResult) -> str:
        value = result.metadata.get("error_code")
        if isinstance(value, str) and value:
            return value
        try:
            decoded = json.loads(result.content)
            if isinstance(decoded, dict) and isinstance(decoded.get("error"), str):
                return decoded["error"]
        except json.JSONDecodeError:
            pass
        return ErrorCode.TOOL_EXECUTION_FAILED.value
