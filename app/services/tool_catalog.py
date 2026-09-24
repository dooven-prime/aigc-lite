"""Workspace-aware discovery, policy, invocation, and ledger projections for tools."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from ..adapters.tools.local import LocalToolProvider
from ..adapters.tools.mcp import MCPToolProvider
from ..adapters.tools.repository import RepositoryMCPProviderSource
from ..config import settings
from ..core.contracts import (
    RequestContext,
    ToolInvocationResult,
    ToolRisk,
    ToolSpec,
)
from ..core.errors import ErrorCode
from ..database import get_repository
from ..ports.tools import ToolProvider, ToolProviderSource
from ..redaction import redact, redact_record_text
from ..repository import Repository

_RISK_SCOPES = {
    ToolRisk.LOW: frozenset(),
    ToolRisk.MEDIUM: frozenset({"tools:write"}),
    ToolRisk.HIGH: frozenset({"tools:high-risk"}),
}


def _ledger_text(content: str, *, failed: bool) -> str:
    if failed:
        return json.dumps({"error": ErrorCode.TOOL_EXECUTION_FAILED.value})
    return redact_record_text(content, max_chars=settings.max_tool_record_chars)


class ToolSession:
    """Immutable discovery snapshot used for one Agent run."""

    def __init__(
        self,
        entries: dict[str, tuple[ToolSpec, ToolProvider]],
        discovery_errors: dict[str, str] | None = None,
    ) -> None:
        self._entries = entries
        self._discovery_errors = discovery_errors or {}

    @property
    def specs(self) -> list[ToolSpec]:
        return [entry[0] for entry in self._entries.values()]

    @property
    def discovery_errors(self) -> dict[str, str]:
        """Return safe provider-level discovery failures for diagnostics."""
        return dict(self._discovery_errors)

    def model_schemas(self) -> list[dict]:
        return [spec.model_schema() for spec in self.specs]

    async def invoke(self, name: str, arguments: str) -> ToolInvocationResult:
        entry = self._entries.get(name)
        if entry is None:
            error = json.dumps({"error": ErrorCode.TOOL_NOT_AVAILABLE.value})
            return ToolInvocationResult(
                content=error,
                failed=True,
                ledger_input="{}",
                ledger_output=error,
                metadata={"tool": name},
            )
        spec, provider = entry
        try:
            decoded = json.loads(arguments or "{}")
            if not isinstance(decoded, dict):
                raise TypeError
        except (json.JSONDecodeError, TypeError):
            error = json.dumps({"error": ErrorCode.INVALID_TOOL_ARGUMENTS.value})
            return ToolInvocationResult(
                content=error,
                failed=True,
                ledger_input="[invalid JSON omitted]",
                ledger_output=error,
                metadata=self._metadata(spec),
            )

        try:
            async with asyncio.timeout(spec.timeout_seconds):
                result = await provider.call_tool(spec.native_name, decoded)
        except TimeoutError:
            error_code = ErrorCode.TOOL_TIMEOUT.value
            error = json.dumps({"error": error_code})
            return ToolInvocationResult(
                content=error,
                failed=True,
                ledger_input=json.dumps(
                    redact(decoded), ensure_ascii=False, default=str
                )[: settings.max_tool_record_chars],
                ledger_output=error,
                metadata={**self._metadata(spec), "error_code": error_code},
            )
        except Exception:  # noqa: BLE001 - keep transport details out of model and ledger
            error_code = ErrorCode.TOOL_PROVIDER_UNAVAILABLE.value
            error = json.dumps({"error": error_code})
            return ToolInvocationResult(
                content=error,
                failed=True,
                ledger_input=json.dumps(
                    redact(decoded), ensure_ascii=False, default=str
                )[: settings.max_tool_record_chars],
                ledger_output=error,
                metadata={**self._metadata(spec), "error_code": error_code},
            )
        model_content = result.content[: settings.max_tool_result_chars]
        return ToolInvocationResult(
            content=model_content,
            failed=result.failed,
            ledger_input=json.dumps(redact(decoded), ensure_ascii=False, default=str)[
                : settings.max_tool_record_chars
            ],
            ledger_output=_ledger_text(result.content, failed=result.failed),
            metadata={**self._metadata(spec), **result.metadata},
        )

    @staticmethod
    def _metadata(spec: ToolSpec) -> dict:
        metadata = {
            "source": spec.source.value,
            "provider_id": spec.provider_id,
            "native_name": spec.native_name,
            "risk": spec.risk.value,
        }
        if spec.execution_mode is not None:
            metadata.update(
                {
                    "execution_mode": spec.execution_mode.value,
                    "cancellation_mode": spec.execution_mode.cancellation_mode,
                }
            )
        return metadata


class ToolCatalog:
    """Combine providers while enforcing workspace and scope visibility."""

    def __init__(
        self,
        providers: list[ToolProvider] | None = None,
        provider_sources: list[ToolProviderSource] | None = None,
    ) -> None:
        self._providers: dict[str, ToolProvider] = {}
        self._provider_sources = provider_sources or []
        for provider in providers or []:
            self.register(provider)

    def register(self, provider: ToolProvider) -> None:
        if provider.provider_id in self._providers:
            raise ValueError(f"Tool provider already registered: {provider.provider_id}")
        self._providers[provider.provider_id] = provider

    def unregister(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    async def open(self, context: RequestContext) -> ToolSession:
        entries: dict[str, tuple[ToolSpec, ToolProvider]] = {}
        discovery_errors: dict[str, str] = {}
        providers = dict(self._providers)
        sources = self._provider_sources if context.tool_hops == 0 else []
        for source in sources:
            try:
                loaded = source.list_providers(context)
            except Exception:  # noqa: BLE001 - isolate dynamic configuration failures
                discovery_errors[source.source_id] = ErrorCode.TOOL_PROVIDER_UNAVAILABLE.value
                continue
            for provider in loaded:
                if provider.provider_id in providers:
                    discovery_errors[provider.provider_id] = (
                        ErrorCode.TOOL_PROVIDER_CONFLICT.value
                    )
                    continue
                providers[provider.provider_id] = provider
        for provider in providers.values():
            if context.tool_hops > 0 and getattr(provider, "is_remote", False):
                continue
            try:
                specs = await provider.list_tools()
            except Exception:  # noqa: BLE001 - discovery must be isolated per provider
                discovery_errors[provider.provider_id] = (
                    ErrorCode.TOOL_PROVIDER_UNAVAILABLE.value
                )
                continue
            for spec in specs:
                if not self._allowed(spec, context):
                    continue
                if spec.name in entries:
                    raise ValueError(f"Duplicate public tool name: {spec.name}")
                entries[spec.name] = (spec, provider)
        return ToolSession(entries, discovery_errors)

    @staticmethod
    def _allowed(spec: ToolSpec, context: RequestContext) -> bool:
        if not spec.enabled:
            return False
        if spec.workspace_id is not None and spec.workspace_id != context.workspace_id:
            return False
        required = spec.required_scopes | _RISK_SCOPES[spec.risk]
        return required.issubset(context.scopes)


def _remote_provider(value: Any) -> MCPToolProvider:
    if not isinstance(value, dict):
        raise TypeError
    provider_id = value["id"]
    url = value["url"]
    workspace_id = value["workspace_id"]
    if not all(isinstance(item, str) and item for item in (provider_id, url, workspace_id)):
        raise TypeError
    if re.fullmatch(r"[a-zA-Z0-9_-]+", provider_id) is None:
        raise ValueError
    parsed_url = urlsplit(url)
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.netloc
        or parsed_url.username is not None
        or parsed_url.password is not None
    ):
        raise ValueError

    header_env = value.get("header_env") or {}
    if not isinstance(header_env, dict) or not all(
        isinstance(header, str)
        and bool(header)
        and isinstance(env_name, str)
        and bool(env_name)
        for header, env_name in header_env.items()
    ):
        raise TypeError
    required_scopes = value.get("required_scopes") or []
    if not isinstance(required_scopes, list) or not all(
        isinstance(scope, str) and bool(scope) for scope in required_scopes
    ):
        raise TypeError

    return MCPToolProvider(
        provider_id,
        url,
        workspace_id=workspace_id,
        header_env=header_env,
        risk=ToolRisk(value.get("risk", ToolRisk.LOW.value)),
        required_scopes=frozenset(required_scopes),
    )


def create_default_tool_catalog(
    repository_provider: Callable[[], Repository] = get_repository,
) -> ToolCatalog:
    catalog = ToolCatalog(
        [LocalToolProvider()],
        [RepositoryMCPProviderSource(repository_provider)],
    )
    if not settings.mcp_servers_json.strip():
        return catalog
    try:
        values = json.loads(settings.mcp_servers_json)
        if not isinstance(values, list):
            raise TypeError
        for value in values:
            catalog.register(_remote_provider(value))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "AIGC_LITE_MCP_SERVERS_JSON contains an invalid remote MCP configuration"
        ) from exc
    return catalog


default_tool_catalog = create_default_tool_catalog()
