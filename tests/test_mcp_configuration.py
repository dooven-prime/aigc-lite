import os

import pytest
from cryptography.fernet import Fernet

from app.adapters.credentials.env import EnvCredentialProvider
from app.adapters.tools.repository import RepositoryMCPProviderSource
from app.config import settings
from app.core.contracts import RequestContext
from app.core.errors import CredentialNotConfiguredError
from app.repository import SQLiteRepository
from app.services.credentials import CredentialService


def _server(provider_id: str = "research") -> dict:
    return {
        "provider_id": provider_id,
        "url": "https://mcp.example.test/mcp",
        "header_credentials": {"Authorization": "env://RESEARCH_MCP_AUTH"},
        "risk": "medium",
        "required_scopes": ["research:read"],
        "timeout_seconds": 12.5,
        "enabled": True,
    }


def test_mcp_server_repository_is_workspace_isolated(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "mcp-config.db")
    repository.init()
    saved = repository.save_mcp_server("workspace-a", _server())

    assert saved["header_credentials"] == {
        "Authorization": "env://RESEARCH_MCP_AUTH"
    }
    assert repository.list_mcp_servers("workspace-b") == []
    assert repository.list_mcp_servers("workspace-a")[0]["provider_id"] == "research"
    assert not repository.delete_mcp_server("workspace-b", saved["id"])
    assert repository.delete_mcp_server("workspace-a", saved["id"])


def test_repository_source_loads_latest_workspace_configuration(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "mcp-source.db")
    repository.init()
    repository.save_mcp_server("workspace-a", _server())
    source = RepositoryMCPProviderSource(lambda: repository)
    context = RequestContext(request_id="r1", workspace_id="workspace-a")

    provider = source.list_providers(context)[0]
    assert provider.provider_id == "research"
    assert provider.workspace_id == "workspace-a"
    assert provider.header_credentials == {
        "Authorization": "env://RESEARCH_MCP_AUTH"
    }
    assert provider.timeout_seconds == 12.5

    changed = _server()
    changed["enabled"] = False
    repository.save_mcp_server("workspace-a", changed)
    assert source.list_providers(context) == []


def test_environment_credential_reference_is_resolved_per_operation(monkeypatch) -> None:
    provider = EnvCredentialProvider()
    monkeypatch.setenv("RESEARCH_MCP_AUTH", "Bearer first")
    assert provider.resolve("env://RESEARCH_MCP_AUTH") == "Bearer first"
    monkeypatch.setenv("RESEARCH_MCP_AUTH", "Bearer rotated")
    assert provider.resolve("env://RESEARCH_MCP_AUTH") == "Bearer rotated"

    monkeypatch.delenv("RESEARCH_MCP_AUTH")
    with pytest.raises(CredentialNotConfiguredError):
        provider.resolve("env://RESEARCH_MCP_AUTH")
    with pytest.raises(CredentialNotConfiguredError):
        provider.resolve(os.devnull)


def test_repository_mcp_source_resolves_encrypted_reference_in_workspace(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "master_key", Fernet.generate_key().decode())
    repository = SQLiteRepository(tmp_path / "encrypted-mcp-source.db")
    repository.init()
    context = RequestContext(request_id="r1", workspace_id="workspace-a")
    credential = CredentialService(lambda: repository).create(
        context, "research-token", "Bearer stored-value"
    )
    server = _server()
    server["header_credentials"] = {
        "Authorization": credential["reference"]
    }
    repository.save_mcp_server("workspace-a", server)

    provider = RepositoryMCPProviderSource(lambda: repository).list_providers(
        context
    )[0]

    assert provider.credential_provider.resolve(credential["reference"]) == (
        "Bearer stored-value"
    )
