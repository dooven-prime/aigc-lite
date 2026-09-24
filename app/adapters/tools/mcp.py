"""Official MCP SDK adapter for a remote Streamable HTTP tool server."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from ...adapters.credentials.env import EnvCredentialProvider
from ...core.contracts import (
    ToolProviderResult,
    ToolRisk,
    ToolSource,
    ToolSpec,
)
from ...ports.credentials import CredentialProvider

TransportFactory = Callable[[], AbstractAsyncContextManager]


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", value)


class MCPToolProvider:
    """Discover and call one remote MCP server under a workspace namespace."""

    is_remote = True

    def __init__(
        self,
        provider_id: str,
        url: str,
        *,
        workspace_id: str | None = None,
        headers: dict[str, str] | None = None,
        header_env: dict[str, str] | None = None,
        header_credentials: dict[str, str] | None = None,
        credential_provider: CredentialProvider | None = None,
        risk: ToolRisk = ToolRisk.LOW,
        required_scopes: frozenset[str] = frozenset(),
        timeout_seconds: float = 30.0,
        transport_factory: TransportFactory | None = None,
    ) -> None:
        parsed_url = urlsplit(url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise ValueError("MCP URL must be HTTP(S) without embedded credentials")
        if timeout_seconds <= 0:
            raise ValueError("MCP timeout must be positive")
        self.provider_id = _safe_name(provider_id)
        self.url = url
        self.workspace_id = workspace_id
        self.headers = headers or {}
        self.header_credentials = {
            **{header: f"env://{name}" for header, name in (header_env or {}).items()},
            **(header_credentials or {}),
        }
        self.credential_provider = credential_provider or EnvCredentialProvider()
        self.risk = risk
        self.required_scopes = required_scopes
        self.timeout_seconds = timeout_seconds
        self._transport_factory = transport_factory or self._transport

    @asynccontextmanager
    async def _transport(self):
        headers = {
            **self.headers,
            **{
                header: self.credential_provider.resolve(reference)
                for header, reference in self.header_credentials.items()
            },
            "X-AIGC-Lite-MCP-Hop": "1",
        }
        async with httpx2.AsyncClient(
            headers=headers, timeout=self.timeout_seconds
        ) as http_client:
            async with streamable_http_client(
                self.url, http_client=http_client
            ) as streams:
                yield streams

    async def list_tools(self) -> list[ToolSpec]:
        async with Client(self._transport_factory()) as client:
            result = await client.list_tools()
        return [
            ToolSpec(
                name=f"{self.provider_id}__{_safe_name(tool.name)}",
                native_name=tool.name,
                description=tool.description or tool.name,
                input_schema=tool.input_schema,
                source=ToolSource.MCP,
                provider_id=self.provider_id,
                workspace_id=self.workspace_id,
                risk=self.risk,
                required_scopes=self.required_scopes,
                timeout_seconds=self.timeout_seconds,
            )
            for tool in result.tools
        ]

    async def call_tool(
        self, native_name: str, arguments: dict[str, Any]
    ) -> ToolProviderResult:
        async with Client(self._transport_factory()) as client:
            result = await client.call_tool(native_name, arguments)
        if result.structured_content is not None:
            content = json.dumps(result.structured_content, ensure_ascii=False, default=str)
        else:
            parts = []
            for item in result.content:
                if getattr(item, "type", None) == "text":
                    parts.append(item.text)
                else:
                    parts.append(
                        json.dumps(item.model_dump(by_alias=True, mode="json"), ensure_ascii=False)
                    )
            content = "\n".join(parts)
        return ToolProviderResult(
            content=content,
            failed=bool(result.is_error),
            metadata={"mcp_result_type": result.result_type},
        )
