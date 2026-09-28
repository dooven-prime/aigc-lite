import asyncio
import json
from datetime import UTC, datetime

import pytest

from app.adapters.tools.local import LocalToolProvider
from app.core.contracts import ToolInvocationResult
from app.core.errors import InvalidScheduleError, ResourceNotFoundError
from app.core.scheduling import ScheduledTask, ScheduleKind, ScheduleStatus
from app.repository import SQLiteRepository
from app.services.task_runner import TaskRunner
from app.services.tool_catalog import ToolCatalog
from app.services.tools import ToolService


def _task(target: str = "agent.chat", payload: dict | None = None) -> ScheduledTask:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return ScheduledTask(
        id="task-1",
        workspace_id="workspace-a",
        name="Scheduled chat",
        target=target,
        payload=payload or {"prompt": "Summarize the workspace"},
        kind=ScheduleKind.ONCE,
        status=ScheduleStatus.SCHEDULED,
        next_run_at=now,
        interval_seconds=None,
        last_run_at=None,
        created_at=now,
        updated_at=now,
    )


def test_agent_target_calls_gateway_with_restricted_scheduler_context() -> None:
    captured: dict = {}

    class FakeGateway:
        async def chat(self, command, context):
            captured["command"] = command
            captured["context"] = context
            return "ok"

    runner = TaskRunner(gateway_service=FakeGateway())
    result = asyncio.run(
        runner.run(
            _task(
                payload={
                    "prompt": "Summarize the workspace",
                    "system": "Be concise.",
                    "model": "public-model",
                    "session_id": "session-1",
                }
            )
        )
    )

    assert result == "ok"
    assert captured["command"].prompt == "Summarize the workspace"
    assert captured["command"].requested_model == "public-model"
    assert captured["context"].workspace_id == "workspace-a"
    assert captured["context"].principal_id == "system:scheduler"
    assert captured["context"].scopes == frozenset()
    assert captured["context"].request_id.startswith("schedule:task-1:")


def test_agent_target_rejects_injected_scopes() -> None:
    class UnusedGateway:
        async def chat(self, command, context):  # pragma: no cover
            raise AssertionError("invalid payload must not reach the gateway")

    runner = TaskRunner(gateway_service=UnusedGateway())

    with pytest.raises(InvalidScheduleError) as raised:
        asyncio.run(
            runner.run(
                _task(payload={"prompt": "hello", "scopes": ["tools:high-risk"]})
            )
        )

    assert raised.value.metadata == {"field": "payload"}


def test_unknown_target_is_not_dynamically_imported() -> None:
    class UnusedGateway:
        async def chat(self, command, context):  # pragma: no cover
            raise AssertionError

    runner = TaskRunner(gateway_service=UnusedGateway())

    with pytest.raises(ResourceNotFoundError):
        asyncio.run(runner.run(_task(target="python.import")))


def test_builtin_targets_dispatch_with_restricted_scheduler_context() -> None:
    captured: dict = {}

    class UnusedGateway:
        async def chat(self, command, context):  # pragma: no cover
            raise AssertionError

    class FakeProbeService:
        async def probe(self, context, server_id):
            captured["probe"] = (context, server_id)
            return {"status": "healthy"}

    class FakeHTTPPollService:
        def validate_payload(self, payload):
            captured["http_validation"] = payload

        async def poll(self, context, payload):
            captured["http"] = (context, payload)
            return {"matched": True, "status_code": 200}

    class FakeToolService:
        async def invoke(self, context, name, arguments, *, transport):
            captured["tool"] = (context, name, arguments, transport)
            return (
                ToolInvocationResult(
                    content='{"status":"ok"}',
                    failed=False,
                    ledger_input="{}",
                    ledger_output='{"status":"ok"}',
                ),
                "run-1",
            )

    runner = TaskRunner(
        gateway_service=UnusedGateway(),
        http_poll_service=FakeHTTPPollService(),
        mcp_probe_service=FakeProbeService(),
        tool_service=FakeToolService(),
    )

    assert runner.target_names == (
        "agent.chat",
        "http.poll",
        "mcp.probe",
        "tool.call",
    )
    runner.validate("http.poll", {"url": "https://status.example.test"})
    runner.validate("mcp.probe", {"server_id": "server-1"})
    runner.validate(
        "tool.call", {"name": "workspace_status", "arguments": {}}
    )
    probe = asyncio.run(
        runner.run(
            _task(target="mcp.probe", payload={"server_id": "server-1"})
        )
    )
    http_poll = asyncio.run(
        runner.run(
            _task(
                target="http.poll",
                payload={"url": "https://status.example.test"},
            )
        )
    )
    tool_result = asyncio.run(
        runner.run(
            _task(
                target="tool.call",
                payload={"name": "workspace_status", "arguments": {}},
            )
        )
    )

    assert probe == {"status": "healthy"}
    assert http_poll == {"matched": True, "status_code": 200}
    http_context, http_payload = captured["http"]
    assert http_payload == {"url": "https://status.example.test"}
    assert http_context.principal_id == "system:scheduler"
    assert http_context.scopes == frozenset()
    assert tool_result == {
        "run_id": "run-1",
        "failed": False,
        "content": '{"status":"ok"}',
    }
    probe_context, server_id = captured["probe"]
    assert server_id == "server-1"
    assert probe_context.workspace_id == "workspace-a"
    assert probe_context.principal_id == "system:scheduler"
    assert probe_context.scopes == frozenset()
    tool_context, name, arguments, transport = captured["tool"]
    assert name == "workspace_status"
    assert arguments == {}
    assert transport == "scheduler"
    assert tool_context.principal_id == "system:scheduler"
    assert tool_context.scopes == frozenset()


@pytest.mark.parametrize(
    ("target", "payload", "field"),
    [
        ("mcp.probe", {}, "payload.server_id"),
        (
            "mcp.probe",
            {"server_id": "server-1", "scopes": ["tools:high-risk"]},
            "payload",
        ),
        ("tool.call", {"name": "workspace_status", "arguments": []}, "payload.arguments"),
        (
            "tool.call",
            {"name": "workspace_status", "arguments": {}, "scopes": []},
            "payload",
        ),
    ],
)
def test_builtin_target_payload_validation(target, payload, field) -> None:
    class UnusedGateway:
        async def chat(self, command, context):  # pragma: no cover
            raise AssertionError

    runner = TaskRunner(gateway_service=UnusedGateway())

    with pytest.raises(InvalidScheduleError) as raised:
        runner.validate(target, payload)

    assert raised.value.metadata == {"field": field}


def test_scheduled_tool_call_uses_catalog_policy_and_records_step(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "scheduled-tool.db")
    repository.init()
    tool_service = ToolService(
        repository_provider=lambda: repository,
        tool_catalog=ToolCatalog([LocalToolProvider()]),
    )

    class UnusedGateway:
        async def chat(self, command, context):  # pragma: no cover
            raise AssertionError

    runner = TaskRunner(
        gateway_service=UnusedGateway(), tool_service=tool_service
    )
    result = asyncio.run(
        runner.run(
            _task(
                target="tool.call",
                payload={"name": "workspace_status", "arguments": {}},
            )
        )
    )

    assert not result["failed"]
    assert json.loads(result["content"]) == {
        "status": "ok",
        "service": "aigc-lite",
    }
    detail = repository.get_run("workspace-a", result["run_id"])
    assert detail is not None
    assert detail["status"] == "succeeded"
    assert detail["steps"][0]["name"] == "workspace_status"
    assert detail["steps"][0]["metadata"]["transport"] == "scheduler"


def test_custom_target_registration_is_explicit_and_unique() -> None:
    class UnusedGateway:
        async def chat(self, command, context):  # pragma: no cover
            raise AssertionError

    async def cleanup(task):
        return task.id

    runner = TaskRunner(gateway_service=UnusedGateway())
    runner.register("system.cleanup", cleanup)
    assert "system.cleanup" in runner.target_names

    with pytest.raises(ValueError, match="already registered"):
        runner.register("system.cleanup", cleanup)
