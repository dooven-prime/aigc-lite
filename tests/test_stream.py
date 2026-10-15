import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import database, main
from app.core.contracts import (
    ChatCommand,
    RequestContext,
    ToolHints,
    ToolProviderResult,
    ToolSource,
    ToolSpec,
)
from app.core.errors import AgentCancelledError, AgentToolCallLimitError, UpstreamRequestError
from app.repository import SQLiteRepository
from app.services.gateway import GatewayService
from app.services.tool_catalog import ToolCatalog


async def fake_stream(_messages, _model=None, _provider=None, _usage_callback=None):
    yield "hello"
    yield " world"


def test_stream_response_is_persisted(tmp_path, monkeypatch) -> None:
    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "stream.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    monkeypatch.setattr(main, "gateway_service", GatewayService(stream_runner=fake_stream))
    with TestClient(main.app) as client:
        response = client.post("/api/chat/stream", json={"prompt": "hello"})
        assert response.status_code == 200
        assert "data: [DONE]" in response.text
        assert response.headers["x-run-id"]
        events = [line for line in response.text.splitlines() if line.startswith("data: {")]
        assert json.loads(events[0][6:])["content"] == "hello"
        session_id = response.headers["x-session-id"]
        messages = client.get(f"/api/sessions/{session_id}").json()["messages"]
        assert messages[-1]["content"] == "hello world"
        run = client.get(f"/api/runs/{response.headers['x-run-id']}").json()
        completed_cancel = client.post(
            f"/api/runs/{response.headers['x-run-id']}/cancel"
        )
        assert run["status"] == "succeeded"
        assert run["steps"][0]["kind"] == "model"
        assert run["steps"][0]["output_content"] == "hello world"
        assert completed_cancel.status_code == 409
        assert completed_cancel.json()["error"]["code"] == "run_not_active"


def test_stream_failure_is_recorded_and_projected_as_sse(tmp_path, monkeypatch) -> None:
    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "failed-stream.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    async def failing_stream(_messages, _model=None, _provider=None, _usage_callback=None):
        if False:
            yield ""
        raise UpstreamRequestError("stream unavailable")

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    monkeypatch.setattr(
        main, "gateway_service", GatewayService(stream_runner=failing_stream)
    )

    with TestClient(main.app) as client:
        response = client.post("/api/chat/stream", json={"prompt": "hello"})
        run = client.get(f"/api/runs/{response.headers['x-run-id']}").json()

    event = next(line for line in response.text.splitlines() if line.startswith("data: {"))
    assert response.status_code == 200
    assert json.loads(event[6:])["code"] == "upstream_request_failed"
    assert run["status"] == "failed"
    assert run["error_code"] == "upstream_request_failed"


def test_stream_wall_time_limit_is_recorded_and_projected_as_sse(
    tmp_path, monkeypatch
) -> None:
    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "limited-stream.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    async def slow_stream(_messages, _model=None, _provider=None, _usage_callback=None):
        await asyncio.sleep(1)
        yield "unreachable"

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    monkeypatch.setattr("app.services.gateway.settings.max_agent_run_seconds", 0.01)
    monkeypatch.setattr(main, "gateway_service", GatewayService(stream_runner=slow_stream))

    with TestClient(main.app) as client:
        response = client.post("/api/chat/stream", json={"prompt": "hello"})
        run = client.get(f"/api/runs/{response.headers['x-run-id']}").json()

    event = next(line for line in response.text.splitlines() if line.startswith("data: {"))
    assert json.loads(event[6:])["code"] == "agent_wall_time_limit_reached"
    assert run["status"] == "limit_reached"
    assert run["error_code"] == "agent_wall_time_limit_reached"


def test_stream_explicit_cancel_reaches_upstream_and_records_run(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "cancel-stream.db")
    repository.init()
    started = asyncio.Event()
    propagated = asyncio.Event()

    async def waiting_stream(
        _messages, _model=None, _provider=None, _usage_callback=None
    ):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            propagated.set()
            raise
        yield "unreachable"

    service = GatewayService(
        repository_provider=lambda: repository,
        stream_runner=waiting_stream,
    )
    context = RequestContext(request_id="request-1", workspace_id="workspace-a")

    async def run() -> dict:
        result = await service.stream_chat(ChatCommand(prompt="wait"), context)
        consumer = asyncio.create_task(anext(result.chunks))
        await started.wait()
        service.cancel_run(context, result.run_id)
        try:
            await consumer
        except AgentCancelledError:
            pass
        else:
            raise AssertionError("stream cancellation was swallowed")
        assert propagated.is_set()
        return repository.get_run("workspace-a", result.run_id)

    detail = asyncio.run(run())
    assert detail["status"] == "cancelled"
    assert detail["error_code"] == "agent_cancelled"
    assert detail["steps"][0]["status"] == "cancelled"


def test_stream_uses_bounded_agent_tool_loop_and_records_steps(tmp_path, monkeypatch) -> None:
    repository = SQLiteRepository(tmp_path / "tool-stream.db")
    repository.init()
    calls: list[dict] = []

    class LookupProvider:
        provider_id = "test-lookup"

        async def list_tools(self):
            return [ToolSpec(
                name="safe_lookup", native_name="safe_lookup", description="Read only",
                input_schema={"type": "object"}, source=ToolSource.LOCAL,
                provider_id=self.provider_id,
                hints=ToolHints(read_only=True, destructive=False, idempotent=True, open_world=False),
            )]

        async def call_tool(self, name, arguments):
            calls.append({"name": name, "arguments": arguments})
            return ToolProviderResult(content='{"found":true}')

    async def model_stream(messages, _model, tools, _provider, *, on_delta):
        if len([item for item in messages if item["role"] == "tool"]) == 0:
            assert any(item["function"]["name"] == "safe_lookup" for item in tools)
            await on_delta("Checking")
            return {
                "role": "assistant", "content": "Checking",
                "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "safe_lookup", "arguments": "{}"}}],
                "_usage": {"prompt_tokens": 4, "completion_tokens": 2},
            }
        assert messages[-1]["role"] == "tool"
        await on_delta("Found it")
        return {"role": "assistant", "content": "Found it", "_usage": {"prompt_tokens": 6, "completion_tokens": 3}}

    monkeypatch.setattr("app.services.gateway.stream_completion", model_stream)
    service = GatewayService(
        repository_provider=lambda: repository,
        tool_catalog=ToolCatalog([LookupProvider()]),
    )

    async def run():
        result = await service.stream_chat(ChatCommand(prompt="look up"), RequestContext(
            request_id="stream-tool", workspace_id="workspace-a", principal_id="user-a",
        ))
        return result, [event async for event in result.chunks]

    result, events = asyncio.run(run())
    assert [event["content"] for event in events if "content" in event] == ["Checking", "Found it"]
    assert [event["kind"] for event in events if event.get("event") == "step"] == ["model", "tool", "model"]
    assert calls == [{"name": "safe_lookup", "arguments": {}}]
    detail = repository.get_run("workspace-a", result.run_id)
    assert detail["status"] == "succeeded"
    assert [step["kind"] for step in detail["steps"]] == ["model", "tool", "model"]
    assert detail["steps"][1]["metadata"]["tool_call_id"] == "call-1"
    assert repository.get_session("workspace-a", result.session_id)["messages"][-1]["content"] == "Found it"


def test_stream_rejects_unsolicited_tool_when_tool_budget_is_zero(tmp_path, monkeypatch) -> None:
    repository = SQLiteRepository(tmp_path / "zero-tools.db")
    repository.init()
    monkeypatch.setattr("app.services.gateway.settings.max_agent_tool_calls", 0)

    async def hostile_model(_messages, _model, tools, _provider, *, on_delta):
        assert not tools
        await on_delta("I will invoke a tool")
        return {
            "role": "assistant", "content": "I will invoke a tool",
            "tool_calls": [{"id": "forged", "function": {"name": "workspace_status", "arguments": "{}"}}],
        }

    monkeypatch.setattr("app.services.gateway.stream_completion", hostile_model)
    service = GatewayService(repository_provider=lambda: repository)

    async def run():
        result = await service.stream_chat(
            ChatCommand(prompt="try"),
            RequestContext(request_id="zero", workspace_id="workspace-a"),
        )
        with pytest.raises(AgentToolCallLimitError):
            _ = [item async for item in result.chunks]
        return repository.get_run("workspace-a", result.run_id)

    detail = asyncio.run(run())
    assert detail["status"] == "limit_reached"
    assert [step["kind"] for step in detail["steps"]] == ["model", "agent"]


def test_disconnected_stream_cancels_underlying_agent_run(tmp_path, monkeypatch) -> None:
    repository = SQLiteRepository(tmp_path / "disconnect.db")
    repository.init()

    async def hanging_model(_messages, _model, _tools, _provider, *, on_delta):
        await on_delta("partial")
        await asyncio.Future()

    monkeypatch.setattr("app.services.gateway.stream_completion", hanging_model)
    service = GatewayService(repository_provider=lambda: repository)

    async def run():
        result = await service.stream_chat(
            ChatCommand(prompt="wait"),
            RequestContext(request_id="disconnect", workspace_id="workspace-a"),
        )
        assert (await anext(result.chunks))["content"] == "partial"
        await result.chunks.aclose()
        return repository.get_run("workspace-a", result.run_id)

    detail = asyncio.run(run())
    assert detail["status"] == "cancelled"
    assert detail["error_code"] == "agent_cancelled"
