import asyncio
import json
from typing import Any

import pytest

from app import agent
from app.core.contracts import (
    ChatCommand,
    RequestContext,
    ToolProviderResult,
    ToolSource,
    ToolSpec,
)
from app.core.errors import ErrorCode, ResourceNotFoundError, UpstreamRequestError
from app.repository import SQLiteRepository
from app.services.gateway import GatewayService
from app.services.tool_catalog import ToolCatalog
from app.tools import tool


@tool("ledger_secret_echo")
def ledger_secret_echo(api_key: str) -> dict:
    """Echo a credential-shaped value for ledger redaction tests."""
    return {"token": api_key, "ok": True}


def _context(workspace_id: str = "workspace-a") -> RequestContext:
    return RequestContext(request_id="request-1", workspace_id=workspace_id)


def test_gateway_chat_coordinates_agent_usage_and_persistence(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "gateway.db")
    repository.init()
    calls: list[dict] = []
    usage_records: list[dict] = []

    async def fake_agent(
        prompt,
        system,
        model,
        max_steps,
        history,
        tenant_id,
        *,
        provider,
        usage_callback,
        step_callback,
        available_tools,
        tool_invoker,
    ) -> str:
        calls.append(
            {
                "prompt": prompt,
                "system": system,
                "model": model,
                "max_steps": max_steps,
                "history": history,
                "tenant_id": tenant_id,
                "provider": provider,
                "step_callback": step_callback,
                "available_tools": available_tools,
                "tool_invoker": tool_invoker,
            }
        )
        usage_callback({"prompt_tokens": 4, "completion_tokens": 2})
        return "gateway answer"

    def fake_usage(usage, model, workspace_id, pricing) -> None:
        usage_records.append(
            {
                "usage": usage,
                "model": model,
                "workspace_id": workspace_id,
                "pricing": pricing,
            }
        )

    service = GatewayService(
        repository_provider=lambda: repository,
        agent_runner=fake_agent,
        usage_recorder=fake_usage,
    )
    result = asyncio.run(
        service.chat(
            ChatCommand(prompt="hello", system="Be concise."),
            _context(),
        )
    )

    assert result.content == "gateway answer"
    assert result.run_id
    assert calls[0]["tenant_id"] == "workspace-a"
    assert calls[0]["history"] == []
    assert usage_records[0]["workspace_id"] == "workspace-a"
    messages = repository.get_session("workspace-a", result.session_id)["messages"]
    assert [(item["role"], item["content"]) for item in messages] == [
        ("user", "hello"),
        ("assistant", "gateway answer"),
    ]
    run = repository.get_run("workspace-a", result.run_id)
    assert run["status"] == "succeeded"
    assert run["request_id"] == "request-1"
    assert run["steps"][0]["kind"] == "agent"
    assert run["steps"][0]["output_content"] == "gateway answer"


def test_gateway_rejects_session_owned_by_another_workspace(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "ownership.db")
    repository.init()
    session = repository.create_session("workspace-a", "Private")

    async def unused_agent(*_args, **_kwargs) -> str:
        raise AssertionError("agent must not run for an inaccessible session")

    service = GatewayService(
        repository_provider=lambda: repository,
        agent_runner=unused_agent,
    )

    with pytest.raises(ResourceNotFoundError) as captured:
        asyncio.run(
            service.chat(
                ChatCommand(prompt="hello", session_id=session["id"]),
                _context("workspace-b"),
            )
        )

    assert captured.value.code == ErrorCode.RESOURCE_NOT_FOUND
    assert repository.get_session("workspace-a", session["id"])["messages"] == []


def test_gateway_records_failed_run_without_persisting_secret_error_text(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "failed-run.db")
    repository.init()

    async def failing_agent(*_args, **_kwargs) -> str:
        raise UpstreamRequestError("upstream failed with private diagnostic")

    service = GatewayService(
        repository_provider=lambda: repository,
        agent_runner=failing_agent,
    )

    with pytest.raises(UpstreamRequestError):
        asyncio.run(service.chat(ChatCommand(prompt="remember this"), _context()))

    run = repository.list_runs("workspace-a")[0]
    assert run["status"] == "failed"
    assert run["error_code"] == "upstream_request_failed"
    detail = repository.get_run("workspace-a", run["id"])
    assert detail["steps"][0]["metadata"]["error_code"] == "upstream_request_failed"
    assert "private diagnostic" not in str(detail)


def test_gateway_persists_each_model_and_tool_step_with_redaction(
    tmp_path, monkeypatch
) -> None:
    repository = SQLiteRepository(tmp_path / "tool-ledger.db")
    repository.init()
    calls = 0

    async def fake_completion(messages, model=None, tools=None, provider=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-secret",
                        "function": {
                            "name": "ledger_secret_echo",
                            "arguments": '{"api_key":"private-value"}',
                        },
                    }
                ],
            }
        assert "private-value" in messages[-1]["content"]
        return {"role": "assistant", "content": "completed", "tool_calls": []}

    monkeypatch.setattr(agent, "completion", fake_completion)
    service = GatewayService(
        repository_provider=lambda: repository,
        agent_runner=agent.run_agent,
    )
    result = asyncio.run(service.chat(ChatCommand(prompt="use the tool"), _context()))
    steps = repository.get_run("workspace-a", result.run_id)["steps"]

    assert [step["kind"] for step in steps] == ["model", "tool", "model"]
    assert [step["sequence"] for step in steps] == [1, 2, 3]
    assert steps[1]["input_content"] == '{"api_key": "***"}'
    assert steps[1]["output_content"] == '{"token": "***", "ok": true}'
    assert steps[1]["metadata"]["source"] == "local"
    assert steps[1]["metadata"]["provider_id"] == "local"
    assert "private-value" not in str(steps)


class DisconnectedMCPProvider:
    provider_id = "remote"

    async def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="remote__lookup",
                native_name="lookup",
                description="Lookup a remote record.",
                input_schema={"type": "object"},
                source=ToolSource.MCP,
                provider_id=self.provider_id,
            )
        ]

    async def call_tool(
        self, native_name: str, arguments: dict[str, Any]
    ) -> ToolProviderResult:
        raise ConnectionError("token=private-transport-detail")


def test_gateway_persists_safe_failed_step_for_disconnected_mcp_tool(
    tmp_path, monkeypatch
) -> None:
    repository = SQLiteRepository(tmp_path / "remote-tool-ledger.db")
    repository.init()
    calls = 0

    async def fake_completion(messages, model=None, tools=None, provider=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert any(
                item["function"]["name"] == "remote__lookup" for item in tools
            )
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-remote",
                        "function": {
                            "name": "remote__lookup",
                            "arguments": '{"token":"private-value"}',
                        },
                    }
                ],
            }
        assert json.loads(messages[-1]["content"]) == {
            "error": "tool_provider_unavailable"
        }
        return {"role": "assistant", "content": "recovered", "tool_calls": []}

    monkeypatch.setattr(agent, "completion", fake_completion)
    service = GatewayService(
        repository_provider=lambda: repository,
        agent_runner=agent.run_agent,
        tool_catalog=ToolCatalog([DisconnectedMCPProvider()]),
    )
    result = asyncio.run(service.chat(ChatCommand(prompt="use remote"), _context()))
    steps = repository.get_run("workspace-a", result.run_id)["steps"]

    assert [step["status"] for step in steps] == ["succeeded", "failed", "succeeded"]
    assert steps[1]["input_content"] == '{"token": "***"}'
    assert json.loads(steps[1]["output_content"]) == {
        "error": "tool_provider_unavailable"
    }
    assert steps[1]["metadata"]["source"] == "mcp"
    assert steps[1]["metadata"]["provider_id"] == "remote"
    assert "private" not in str(steps)
