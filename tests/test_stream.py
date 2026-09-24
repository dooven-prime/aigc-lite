import json
import sqlite3

from fastapi.testclient import TestClient

from app import database, main
from app.core.errors import UpstreamRequestError
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
        assert run["status"] == "succeeded"
        assert run["steps"][0]["kind"] == "model"
        assert run["steps"][0]["output_content"] == "hello world"


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
