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
from ...core.artifacts import (
    ArtifactDraft,
    ArtifactKind,
    CitationDraft,
    CitationSourceKind,
)
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


def _resource_name(item: Any, uri: str, fallback: str) -> str:
    for attribute in ("title", "name"):
        value = getattr(item, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    tail = uri.rstrip("/").rsplit("/", 1)[-1]
    return tail or fallback


def _resource_artifact(item: Any, native_name: str) -> ArtifactDraft | None:
    item_type = getattr(item, "type", None)
    if item_type == "resource_link":
        uri = str(getattr(item, "uri", "") or "")
        if not uri:
            return None
        media_type = str(getattr(item, "mime_type", None) or "text/uri-list")
        description = str(getattr(item, "description", None) or "")
        return ArtifactDraft(
            name=_resource_name(item, uri, f"{native_name} resource"),
            kind=ArtifactKind.LINK,
            media_type=media_type,
            content_text=description,
            uri=uri,
            metadata={
                "mcp_content_type": item_type,
                "size": getattr(item, "size", None),
            },
        )
    if item_type != "resource":
        return None
    resource = getattr(item, "resource", None)
    if resource is None:
        return None
    uri = str(getattr(resource, "uri", "") or "")
    text = str(getattr(resource, "text", None) or "")
    media_type = str(getattr(resource, "mime_type", None) or "text/plain")
    if not uri and not text:
        return None
    if media_type == "application/json":
        kind = ArtifactKind.JSON
    elif media_type in {"text/markdown", "text/x-markdown"}:
        kind = ArtifactKind.MARKDOWN
    elif text:
        kind = ArtifactKind.TEXT
    else:
        kind = ArtifactKind.FILE
    return ArtifactDraft(
        name=_resource_name(resource, uri, f"{native_name} resource"),
        kind=kind,
        media_type=media_type,
        content_text=text,
        uri=uri or None,
        metadata={"mcp_content_type": item_type},
    )


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
        artifacts: list[ArtifactDraft] = []
        citations: list[CitationDraft] = []
        if result.structured_content is not None:
            content = json.dumps(result.structured_content, ensure_ascii=False, default=str)
            artifacts.append(
                ArtifactDraft(
                    name=f"{native_name} result",
                    kind=ArtifactKind.JSON,
                    media_type="application/json",
                    content_text=content,
                    metadata={"mcp_content_type": "structured_content"},
                )
            )
            citations.append(
                CitationDraft(
                    source_kind=CitationSourceKind.TOOL,
                    title=f"{self.provider_id}:{native_name}",
                    source_id=f"{self.provider_id}:{native_name}",
                    artifact_index=0,
                )
            )
        else:
            parts = []
            for item in result.content:
                if getattr(item, "type", None) == "text":
                    parts.append(item.text)
                else:
                    parts.append(
                        json.dumps(item.model_dump(by_alias=True, mode="json"), ensure_ascii=False)
                    )
                artifact = _resource_artifact(item, native_name)
                if artifact is not None:
                    artifact_index = len(artifacts)
                    artifacts.append(artifact)
                    citations.append(
                        CitationDraft(
                            source_kind=CitationSourceKind.TOOL,
                            title=f"{self.provider_id}:{native_name}",
                            source_id=f"{self.provider_id}:{native_name}",
                            source_uri=artifact.uri,
                            excerpt=artifact.content_text[:1000],
                            artifact_index=artifact_index,
                        )
                    )
            content = "\n".join(parts)
        return ToolProviderResult(
            content=content,
            failed=bool(result.is_error),
            metadata={"mcp_result_type": result.result_type},
            artifacts=tuple(artifacts),
            citations=tuple(citations),
        )
