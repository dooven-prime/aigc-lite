"""Chat files are bounded, run-linked inputs rather than knowledge admission."""

import json
import sqlite3

from fastapi.testclient import TestClient

from app import database, main
from app.core.errors import UpstreamRequestError
from app.services.gateway import GatewayService


async def echo_attached_source(messages, _model=None, _provider=None, _usage=None):
    assert "candidate_only" not in messages[-1]["content"]
    assert "lemma one" in messages[-1]["content"]
    yield "received"


def _client(tmp_path, monkeypatch):
    def connect():
        connection = sqlite3.connect(tmp_path / "chat-files.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect)
    monkeypatch.setattr(main, "gateway_service", GatewayService(stream_runner=echo_attached_source))
    return TestClient(main.app)


def test_streamed_attachment_is_run_artifact_not_knowledge(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/api/sessions").json() == []
        response = client.post(
            "/api/chat/stream",
            json={"prompt": "Check this", "attachments": [
                {"name": "lemma.md", "content": "lemma one\n"}
            ]},
        )
        assert response.status_code == 200
        assert "data: [DONE]" in response.text
        session_id = response.headers["x-session-id"]
        run_id = response.headers["x-run-id"]
        session = client.get(f"/api/sessions/{session_id}").json()
        assert session["title"] == "Check this"
        assert "[Attached files: lemma.md]" in session["messages"][0]["content"]
        artifacts = client.get("/api/artifacts", params={"run_id": run_id}).json()
        assert len(artifacts) == 1
        assert artifacts[0]["name"] == "lemma.md"
        assert artifacts[0]["metadata"]["session_id"] == session_id
        assert artifacts[0]["metadata"]["authority"] == "candidate_only"
        assert artifacts[0]["content_hash"]
        assert client.get("/api/knowledge/search", params={"q": "lemma one"}).json() == []


def test_invalid_attachment_fails_before_creating_session(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        for name, content in [
            ("../secret.md", "hello"),
            ("report.pdf", "hello"),
            ("notes.txt", "x" * 16_385),
        ]:
            response = client.post(
                "/api/chat/stream",
                json={"prompt": "Read", "attachments": [{"name": name, "content": content}]},
            )
            assert response.status_code == 422
        assert client.get("/api/sessions").json() == []


def test_attachment_stream_failure_keeps_artifact_and_failed_run(tmp_path, monkeypatch):
    async def fail(_messages, _model=None, _provider=None, _usage=None):
        if False:
            yield ""
        raise UpstreamRequestError("upstream failed")

    with _client(tmp_path, monkeypatch) as client:
        monkeypatch.setattr(main, "gateway_service", GatewayService(stream_runner=fail))
        response = client.post(
            "/api/chat/stream",
            json={"prompt": "Check", "attachments": [
                {"name": "proof.txt", "content": "lemma one"}
            ]},
        )
        assert response.status_code == 200
        assert "data: [DONE]" not in response.text
        assert json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: {")))["code"] == "upstream_request_failed"
        run_id = response.headers["x-run-id"]
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "failed"
        assert len(client.get("/api/artifacts", params={"run_id": run_id}).json()) == 1
