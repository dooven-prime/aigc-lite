"""Build remote MCP providers from workspace-owned repository records."""

from __future__ import annotations

from collections.abc import Callable

from ...adapters.credentials import (
    CompositeCredentialProvider,
    EncryptedCredentialProvider,
    EnvCredentialProvider,
)
from ...core.contracts import RequestContext, ToolRisk
from ...database import get_repository
from ...ports.credentials import CredentialProvider
from ...ports.tools import ToolProvider
from ...repository import Repository
from .mcp import MCPToolProvider

RepositoryProvider = Callable[[], Repository]


def provider_from_record(
    record: dict,
    workspace_id: str,
    credential_provider: CredentialProvider | None = None,
) -> MCPToolProvider:
    """Build the runtime provider used by discovery, calls, and admin probes."""

    resolved_provider = credential_provider or CompositeCredentialProvider(
        environment=EnvCredentialProvider(),
        encrypted_database=EncryptedCredentialProvider(workspace_id),
    )
    return MCPToolProvider(
        record["provider_id"],
        record["url"],
        workspace_id=workspace_id,
        header_credentials=record["header_credentials"],
        credential_provider=resolved_provider,
        risk=ToolRisk(record["risk"]),
        required_scopes=frozenset(record["required_scopes"]),
        timeout_seconds=float(record["timeout_seconds"]),
    )


class RepositoryMCPProviderSource:
    """Load enabled MCP server records for the current workspace."""

    source_id = "repository-mcp"

    def __init__(
        self,
        repository_provider: RepositoryProvider = get_repository,
        credential_provider: CredentialProvider | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._credential_provider = credential_provider

    def list_providers(self, context: RequestContext) -> list[ToolProvider]:
        records = self._repository_provider().list_mcp_servers(context.workspace_id)
        credential_provider = self._credential_provider or CompositeCredentialProvider(
            environment=EnvCredentialProvider(),
            encrypted_database=EncryptedCredentialProvider(
                context.workspace_id, self._repository_provider
            ),
        )
        return [
            provider_from_record(
                record,
                context.workspace_id,
                credential_provider,
            )
            for record in records
            if record["enabled"]
        ]
