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
from app.services.tool_catalog import _remote_provider


def _server(
    credential_reference: str,
    provider_id: str = "research",
) -> dict:
    return {
        "provider_id": provider_id,
        "url": "https://mcp.example.test/mcp",
        "header_credentials": {"Authorization": credential_reference},
        "risk": "medium",
        "required_scopes": ["research:read"],
        "timeout_seconds": 12.5,
        "enabled": True,
    }


def test_mcp_server_repository_is_workspace_isolated(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "master_key", Fernet.generate_key().decode())
    repository = SQLiteRepository(tmp_path / "mcp-config.db")
    repository.init()
    context = RequestContext(request_id="r1", workspace_id="workspace-a")
    credential = CredentialService(lambda: repository).create(
        context, "research-token", "Bearer stored-value"
    )
    with pytest.raises(ValueError, match="credential_reference_not_active"):
        repository.save_mcp_server(
            "workspace-b", _server(credential["reference"])
        )
    with pytest.raises(ValueError, match="credential_reference_not_active"):
        repository.save_mcp_server(
            "workspace-a",
            _server(
                "encrypted-db://credential/11111111-1111-4111-8111-111111111111"
            ),
        )
    saved = repository.save_mcp_server(
        "workspace-a", _server(credential["reference"])
    )

    assert saved["header_credentials"] == {
        "Authorization": credential["reference"]
    }
    assert repository.list_mcp_servers("workspace-b") == []
    assert repository.list_mcp_servers("workspace-a")[0]["provider_id"] == "research"
    assert not repository.delete_mcp_server("workspace-b", saved["id"])
    assert repository.delete_mcp_server("workspace-a", saved["id"])


def test_repository_source_loads_latest_workspace_configuration(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "master_key", Fernet.generate_key().decode())
    repository = SQLiteRepository(tmp_path / "mcp-source.db")
    repository.init()
    context = RequestContext(request_id="r1", workspace_id="workspace-a")
    credential = CredentialService(lambda: repository).create(
        context, "research-token", "Bearer stored-value"
    )
    repository.save_mcp_server(
        "workspace-a", _server(credential["reference"])
    )
    source = RepositoryMCPProviderSource(lambda: repository)

    provider = source.list_providers(context)[0]
    assert provider.provider_id == "research"
    assert provider.workspace_id == "workspace-a"
    assert provider.header_credentials == {
        "Authorization": credential["reference"]
    }
    assert provider.timeout_seconds == 12.5

    changed = _server(credential["reference"])
    changed["enabled"] = False
    repository.save_mcp_server("workspace-a", changed)
    assert source.list_providers(context) == []


def test_environment_credential_reference_is_resolved_per_operation(monkeypatch) -> None:
    provider = EnvCredentialProvider(frozenset({"RESEARCH_MCP_AUTH"}))
    monkeypatch.setenv("RESEARCH_MCP_AUTH", "Bearer first")
    assert provider.resolve("env://RESEARCH_MCP_AUTH") == "Bearer first"
    monkeypatch.setenv("RESEARCH_MCP_AUTH", "Bearer rotated")
    assert provider.resolve("env://RESEARCH_MCP_AUTH") == "Bearer rotated"

    monkeypatch.delenv("RESEARCH_MCP_AUTH")
    with pytest.raises(CredentialNotConfiguredError):
        provider.resolve("env://RESEARCH_MCP_AUTH")
    with pytest.raises(CredentialNotConfiguredError):
        provider.resolve(os.devnull)


def test_environment_credential_provider_fails_closed_without_allowlist(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AIGC_LITE_MASTER_KEY", "master-secret")
    monkeypatch.setenv("AIGC_LITE_LLM_API_KEY", "platform-llm-secret")
    provider = EnvCredentialProvider()

    with pytest.raises(CredentialNotConfiguredError):
        provider.resolve("env://AIGC_LITE_MASTER_KEY")
    with pytest.raises(CredentialNotConfiguredError):
        provider.resolve("env://AIGC_LITE_LLM_API_KEY")


def test_static_mcp_env_reference_requires_deployer_allowlist(
    monkeypatch,
) -> None:
    definition = {
        "id": "static-research",
        "url": "https://mcp.example.test/mcp",
        "workspace_id": "workspace-a",
        "header_env": {"Authorization": "RESEARCH_MCP_AUTH"},
    }
    monkeypatch.setattr(settings, "mcp_env_credential_allowlist", "")
    with pytest.raises(ValueError, match="not allowlisted"):
        _remote_provider(definition)

    monkeypatch.setattr(
        settings, "mcp_env_credential_allowlist", "RESEARCH_MCP_AUTH"
    )
    monkeypatch.setenv("RESEARCH_MCP_AUTH", "Bearer deployment-owned")
    provider = _remote_provider(definition)
    assert provider.credential_provider.resolve(
        "env://RESEARCH_MCP_AUTH"
    ) == "Bearer deployment-owned"


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
    server = _server(credential["reference"])
    repository.save_mcp_server("workspace-a", server)

    provider = RepositoryMCPProviderSource(lambda: repository).list_providers(
        context
    )[0]

    assert provider.credential_provider.resolve(credential["reference"]) == (
        "Bearer stored-value"
    )
