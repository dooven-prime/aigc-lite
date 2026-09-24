import asyncio
import json
import sqlite3

from fastapi.testclient import TestClient

from app import database, main
from app.core.contracts import ChatCommand, RequestContext
from app.core.errors import UpstreamRequestError
from app.repository import SQLiteRepository
from app.services.gateway import GatewayService


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
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("stream cancellation was swallowed")
        assert propagated.is_set()
        return repository.get_run("workspace-a", result.run_id)

    detail = asyncio.run(run())
    assert detail["status"] == "cancelled"
    assert detail["error_code"] == "agent_cancelled"
    assert detail["steps"][0]["status"] == "cancelled"
