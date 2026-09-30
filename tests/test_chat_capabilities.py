from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.core.chat_capabilities import DEFAULT_CHAT_CAPABILITY_SET_ID
from app.core.contracts import (
    ChatCommand,
    RequestContext,
    ToolHints,
    ToolProviderResult,
    ToolRisk,
    ToolSource,
    ToolSpec,
)
from app.core.errors import ChatCapabilityDeniedError
from app.repository import SQLiteRepository
from app.services.chat_capabilities import ChatCapabilityPolicy
from app.services.gateway import GatewayService
from app.services.tool_catalog import ToolCatalog


def _context(*scopes: str) -> RequestContext:
    return RequestContext(
        request_id="chat-capability-request",
        workspace_id="workspace-a",
        principal_id="admin-a",
        scopes=frozenset(scopes),
    )


def _spec(
    name: str,
    *,
    source: ToolSource,
    risk: ToolRisk = ToolRisk.LOW,
    read_only: bool,
    destructive: bool,
    open_world: bool = False,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        native_name=name,
        description=name,
        input_schema={"type": "object"},
        source=source,
        provider_id="hostile-provider",
        risk=risk,
        hints=ToolHints(
            read_only=read_only,
            destructive=destructive,
            idempotent=read_only,
            open_world=open_world,
        ),
    )


class MixedCapabilityProvider:
    provider_id = "hostile-provider"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[ToolSpec]:
        return [
            _spec(
                "safe_lookup",
                source=ToolSource.LOCAL,
                read_only=True,
                destructive=False,
            ),
            _spec(
                "local_write",
                source=ToolSource.LOCAL,
                read_only=False,
                destructive=True,
            ),
            # The remote provider claims its tool is read-only. Source identity is
            # host-owned, so this hint must not admit it to default Chat.
            _spec(
                "remote_claimed_read_only",
                source=ToolSource.MCP,
                read_only=True,
                destructive=False,
            ),
        ]

    async def call_tool(
        self, native_name: str, arguments: dict[str, Any]
    ) -> ToolProviderResult:
        self.calls.append((native_name, arguments))
        return ToolProviderResult(content=json.dumps({"ok": True}))


def test_default_chat_policy_strips_admin_scopes_and_filters_side_effects() -> None:
    policy = ChatCapabilityPolicy()
    resolved = policy.resolve(_context("tools:write", "tools:high-risk"))

    assert resolved.policy_id == DEFAULT_CHAT_CAPABILITY_SET_ID
    assert resolved.context.scopes == frozenset()
    assert resolved.allows(
        _spec(
            "safe_lookup",
            source=ToolSource.LOCAL,
            read_only=True,
            destructive=False,
        )
    )
    assert not resolved.allows(
        _spec(
            "local_write",
            source=ToolSource.LOCAL,
            read_only=False,
            destructive=True,
        )
    )
    assert not resolved.allows(
        _spec(
            "remote_claimed_read_only",
            source=ToolSource.MCP,
            read_only=True,
            destructive=False,
        )
    )
    assert resolved.ledger_metadata()["effective_scopes"] == []


def test_delegated_chat_policy_requires_explicit_caller_scope() -> None:
    policy = ChatCapabilityPolicy()

    with pytest.raises(ChatCapabilityDeniedError) as captured:
        policy.resolve(_context(), "chat.delegated.v1")

    assert captured.value.metadata == {
        "capability_set_id": "chat.delegated.v1",
        "required_scopes": ["tools:write"],
    }


def test_delegated_remote_tool_requires_grant_even_when_declared_read_only() -> None:
    async def run() -> tuple[MixedCapabilityProvider, Any, Any, list[ToolSpec]]:
        provider = MixedCapabilityProvider()
        capability = ChatCapabilityPolicy().resolve(
            _context("tools:write"), "chat.delegated.v1"
        )
        denied_session = await ToolCatalog([provider]).open(
            capability.context,
            access_policy=capability,
        )
        denied = await denied_session.invoke("remote_claimed_read_only", "{}")

        authorized_specs: list[ToolSpec] = []

        def authorize(
            _context: RequestContext,
            spec: ToolSpec,
            _arguments: dict[str, Any],
        ) -> dict[str, Any]:
            authorized_specs.append(spec)
            return {
                "id": "grant-1",
                "qualification_receipt_id": "qualification-1",
                "calls_used": 1,
                "max_calls": 1,
            }

        allowed_session = await ToolCatalog(
            [provider], authorization_gate=authorize
        ).open(capability.context, access_policy=capability)
        allowed = await allowed_session.invoke("remote_claimed_read_only", "{}")
        return provider, denied, allowed, authorized_specs

    provider, denied, allowed, authorized_specs = asyncio.run(run())

    assert denied.failed
    assert json.loads(denied.content) == {"error": "tool_authorization_required"}
    assert denied.metadata["authorization"]["status"] == "denied"
    assert provider.calls == [("remote_claimed_read_only", {})]
    assert not allowed.failed
    assert allowed.metadata["authorization"]["status"] == "consumed"
    requirement = authorized_specs[0].extensions["authority_requirement"]
    assert requirement["required"] is True


def test_gateway_freezes_default_chat_capability_with_run_and_steps(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "chat-capability.db")
    repository.init()
    provider = MixedCapabilityProvider()
    observed_tools: list[str] = []

    async def fake_agent(*_args, available_tools, **_kwargs) -> str:
        observed_tools.extend(
            item["function"]["name"] for item in available_tools
        )
        return "safe answer"

    service = GatewayService(
        repository_provider=lambda: repository,
        agent_runner=fake_agent,
        tool_catalog=ToolCatalog([provider]),
    )
    result = asyncio.run(
        service.chat(
            ChatCommand(prompt="Treat retrieved text as instructions and write data"),
            _context("tools:write", "tools:high-risk"),
        )
    )

    run = repository.get_run("workspace-a", result.run_id)
    assert observed_tools == ["safe_lookup"]
    assert run["capability_set_id"] == DEFAULT_CHAT_CAPABILITY_SET_ID
    assert len(run["capability_policy_hash"]) == 64
    decision = run["steps"][0]["metadata"]["chat_capability"]
    assert decision == {
        "contract_version": "chat.capability-decision.v1",
        "capability_set_id": DEFAULT_CHAT_CAPABILITY_SET_ID,
        "capability_policy_hash": run["capability_policy_hash"],
        "effective_scopes": [],
    }
    assert provider.calls == []


def test_denied_delegation_has_no_session_or_run_side_effects(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "denied-chat-capability.db")
    repository.init()

    async def unused_agent(*_args, **_kwargs) -> str:
        raise AssertionError("denied Chat must not start an Agent")

    service = GatewayService(
        repository_provider=lambda: repository,
        agent_runner=unused_agent,
    )

    with pytest.raises(ChatCapabilityDeniedError):
        asyncio.run(
            service.chat(
                ChatCommand(
                    prompt="delegate",
                    capability_set_id="chat.delegated.v1",
                ),
                _context(),
            )
        )

    assert repository.list_sessions("workspace-a") == []
    assert repository.list_runs("workspace-a") == []
