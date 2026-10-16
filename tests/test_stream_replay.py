"""Finite, workspace-scoped replay of the durable SSE event ledger."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app import database, main
from app.core.contracts import ChatCommand, RequestContext
from app.core.errors import UpstreamResponseError
from app.repository import PostgresRepository, SQLiteRepository, StreamEventLimitError
from app.services.gateway import GatewayService


def _time(offset: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset)).isoformat()


def _run(repository, workspace_id: str, *, stream: bool = True) -> dict:
    session = repository.create_session(workspace_id, "replay")
    return repository.create_run(
        workspace_id, session["id"], str(uuid4()), None, "test-model",
        stream_start_deadline_at=_time(300) if stream else None,
    )


def _exercise_repository(repository, workspace_id: str) -> None:
    pending = _run(repository, workspace_id)
    assert repository.read_stream_events(workspace_id, pending["id"])["events"] == []
    with pytest.raises(ValueError, match="not active"):
        repository.append_stream_event(workspace_id, pending["id"], {"content": "early"})
    assert repository.claim_stream_run(
        workspace_id, pending["id"], claimed_at=_time(), lease_until=_time(300)
    )
    assert repository.append_stream_event(
        workspace_id, pending["id"], {"content": "first", "turn": 1}
    ) == 1
    assert repository.append_stream_event(
        workspace_id, pending["id"], {"event": "step", "sequence": 1}
    ) == 2
    first_page = repository.read_stream_events(workspace_id, pending["id"], limit=1)
    assert first_page["status"] == "running"
    assert first_page["replay_available"] is True
    assert first_page["replay_boundary"] is None
    assert first_page["next_cursor"] == 1
    assert first_page["has_more"] is True
    assert first_page["events"] == [
        {"cursor": 1, "event_type": "data", "data": {"content": "first", "turn": 1}}
    ]
    assert repository.close_stream_run(
        workspace_id, pending["id"], expected_state="claimed", status="succeeded",
    )
    final_page = repository.read_stream_events(
        workspace_id, pending["id"], after=first_page["next_cursor"], limit=1
    )
    assert final_page["events"] == [
        {"cursor": 2, "event_type": "data", "data": {"event": "step", "sequence": 1}}
    ]
    assert final_page["has_more"] is True
    terminal = repository.read_stream_events(
        workspace_id, pending["id"], after=final_page["next_cursor"]
    )
    assert terminal["status"] == "succeeded"
    assert terminal["events"] == [
        {
            "cursor": 3, "event_type": "terminal",
            "data": {"event": "terminal", "status": "succeeded", "error_code": None},
        }
    ]
    assert repository.read_stream_events(workspace_id, pending["id"], after=3)[
        "events"
    ] == []
    assert repository.read_stream_events(f"other-{workspace_id}", pending["id"]) is None
    assert repository.read_stream_events(workspace_id, _run(repository, workspace_id, stream=False)["id"]) is None
    with pytest.raises(ValueError, match="nonnegative"):
        repository.read_stream_events(workspace_id, pending["id"], after=-1)
    with pytest.raises(ValueError, match="between 1 and 200"):
        repository.read_stream_events(workspace_id, pending["id"], limit=201)
    with pytest.raises(ValueError, match="not active"):
        repository.append_stream_event(workspace_id, pending["id"], {"content": "late"})

    cancelled = _run(repository, workspace_id)
    assert repository.claim_stream_run(
        workspace_id, cancelled["id"], claimed_at=_time(), lease_until=_time(300)
    )
    assert repository.append_stream_event(
        workspace_id, cancelled["id"], {"content": "partial"}
    ) == 1
    assert repository.request_stream_cancel(
        workspace_id, cancelled["id"], requested_at=_time()
    ) == "requested_claimed"
    with pytest.raises(ValueError, match="not active"):
        repository.append_stream_event(workspace_id, cancelled["id"], {"content": "late"})
    assert repository.close_stream_run(
        workspace_id, cancelled["id"], expected_state="claimed", status="cancelled",
        error_code="agent_cancelled",
    )
    cancelled_events = repository.read_stream_events(workspace_id, cancelled["id"])
    assert [item["cursor"] for item in cancelled_events["events"]] == [1, 2]
    assert cancelled_events["events"][-1]["data"]["status"] == "cancelled"

    expired = _run(repository, workspace_id)
    assert repository.claim_stream_run(
        workspace_id, expired["id"], claimed_at=_time(-120), lease_until=_time(-60)
    )
    with pytest.raises(ValueError, match="not active"):
        repository.append_stream_event(workspace_id, expired["id"], {"content": "late"})
    assert repository.reconcile_expired_stream_run(workspace_id, expired["id"], now=_time())
    expired_events = repository.read_stream_events(workspace_id, expired["id"])["events"]
    assert expired_events == [
        {
            "cursor": 1, "event_type": "terminal",
            "data": {
                "event": "terminal", "status": "failed",
                "error_code": "stream_execution_indeterminate",
            },
        }
    ]

    never_started = _run(repository, workspace_id)
    assert repository.request_stream_cancel(
        workspace_id, never_started["id"], requested_at=_time()
    ) == "closed_pending"
    assert repository.read_stream_events(workspace_id, never_started["id"])["events"][0][
        "data"
    ]["error_code"] == "stream_not_started"


def _exercise_cancel_race(repository, workspace_id: str) -> None:
    run = _run(repository, workspace_id)
    assert repository.claim_stream_run(
        workspace_id, run["id"], claimed_at=_time(), lease_until=_time(300)
    )
    barrier = Barrier(3)

    def append() -> bool:
        barrier.wait()
        try:
            repository.append_stream_event(workspace_id, run["id"], {"content": "racing"})
            return True
        except ValueError:
            return False

    def cancel() -> str | None:
        barrier.wait()
        return repository.request_stream_cancel(
            workspace_id, run["id"], requested_at=_time()
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        appended = pool.submit(append)
        cancelled = pool.submit(cancel)
        barrier.wait()
        assert cancelled.result(timeout=10) == "requested_claimed"
        may_have_appended = appended.result(timeout=10)
    assert repository.close_stream_run(
        workspace_id, run["id"], expected_state="claimed", status="cancelled",
        error_code="agent_cancelled",
    )
    replay = repository.read_stream_events(workspace_id, run["id"])
    assert [event["event_type"] for event in replay["events"]] == (
        ["data", "terminal"] if may_have_appended else ["terminal"]
    )
    with pytest.raises(ValueError, match="not active"):
        repository.append_stream_event(workspace_id, run["id"], {"content": "too late"})


def test_sqlite_stream_replay_repository(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "stream-replay.db")
    repository.init()
    workspace = f"stream-replay-{uuid4()}"
    _exercise_repository(repository, workspace)
    _exercise_cancel_race(repository, workspace)


def test_pre_0031_runs_are_explicitly_incomplete_after_upgrade(tmp_path) -> None:
    path = tmp_path / "historical-streams.db"
    app_root = Path(__file__).resolve().parents[1] / "app"
    config = Config(str(app_root / "alembic.ini"))
    config.set_main_option("script_location", str(app_root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    command.upgrade(config, "0029_shared_attempt_invalidation")

    workspace = f"historical-streams-{uuid4()}"
    session_id, closed_id, active_id = (str(uuid4()) for _ in range(3))
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO sessions(id, tenant_id, title, created_at, updated_at) "
            "VALUES (?, ?, 'historical', ?, ?)",
            (session_id, workspace, _time(-120), _time(-120)),
        )
        for run_id, status, stream_state, completed_at, lease_until in (
            (closed_id, "succeeded", "closed", _time(-60), None),
            (active_id, "running", "claimed", None, _time(300)),
        ):
            connection.execute(
                "INSERT INTO agent_runs(id, tenant_id, session_id, request_id, "
                "status, selected_model, created_at, completed_at, stream_state, "
                "stream_start_deadline_at, stream_claimed_at, stream_lease_until) "
                "VALUES (?, ?, ?, ?, ?, 'test-model', ?, ?, ?, ?, ?, ?)",
                (
                    run_id, workspace, session_id, str(uuid4()), status,
                    _time(-120), completed_at, stream_state, _time(-110),
                    _time(-100), lease_until,
                ),
            )
    command.upgrade(config, "head")

    repository = SQLiteRepository(path)
    closed = repository.read_stream_events(workspace, closed_id)
    assert closed["status"] == "succeeded"
    assert closed["events"] == []
    assert closed["replay_available"] is False
    assert closed["replay_boundary"] == "pre_0031"

    assert repository.append_stream_event(
        workspace, active_id, {"content": "post-upgrade only"}
    ) == 1
    assert repository.close_stream_run(
        workspace, active_id, expected_state="claimed", status="succeeded"
    )
    active = repository.read_stream_events(workspace, active_id)
    assert [item["event_type"] for item in active["events"]] == ["data", "terminal"]
    assert active["replay_available"] is False
    assert active["replay_boundary"] == "pre_0031"

    fresh = _run(repository, workspace)
    assert repository.read_stream_events(workspace, fresh["id"])["replay_available"] is True


@pytest.mark.skipif(
    not os.getenv("AIGC_LITE_TEST_POSTGRES_URL"),
    reason="AIGC_LITE_TEST_POSTGRES_URL is not configured",
)
def test_postgres_stream_replay_repository() -> None:
    repository = PostgresRepository(os.environ["AIGC_LITE_TEST_POSTGRES_URL"])
    repository.init()
    workspace = f"stream-replay-{uuid4()}"
    _exercise_repository(repository, workspace)
    _exercise_cancel_race(repository, workspace)


def test_http_replay_is_finite_workspace_scoped_and_does_not_execute(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "http-stream-replay.db"

    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    workspace = f"http-stream-replay-{uuid4()}"
    repository = SQLiteRepository(database_path)
    repository.init()
    calls = 0

    async def model(_messages, _model=None, _provider=None, _usage_callback=None):
        nonlocal calls
        calls += 1
        yield "hello"
        yield " world"

    gateway = GatewayService(repository_provider=lambda: repository, stream_runner=model)
    monkeypatch.setattr(main, "gateway_service", gateway)
    monkeypatch.setitem(
        main.app.dependency_overrides, main.current_tenant,
        lambda: main.Tenant(workspace, "Replay workspace"),
    )

    with TestClient(main.app) as client:
        live = client.post("/api/chat/stream", json={"prompt": "hello"})
        assert live.status_code == 200
        run_id = live.headers["x-run-id"]
        live_cursors = [
            int(line.removeprefix("id: ")) for line in live.text.splitlines()
            if line.startswith("id: ")
        ]
        assert live_cursors == [1, 2, 3]
        first = client.get(f"/api/runs/{run_id}/events", params={"limit": 2})
        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-store"
        assert first.json()["next_cursor"] == 2
        assert first.json()["has_more"] is True
        assert [event["data"]["content"] for event in first.json()["events"]] == [
            "hello", " world",
        ]
        second = client.get(
            f"/api/runs/{run_id}/events", params={"after": 2, "limit": 2}
        )
        assert [event["cursor"] for event in second.json()["events"]] == [3, 4]
        assert second.json()["events"][-1]["data"] == {
            "event": "terminal", "status": "succeeded", "error_code": None,
        }
        assert second.json()["status"] == "succeeded"
        assert client.get(f"/api/runs/{run_id}/events", params={"after": 4}).json()[
            "events"
        ] == []
        assert calls == 1
        assert client.get(f"/api/runs/{run_id}/events", params={"after": -1}).status_code == 422
        assert client.get(f"/api/runs/{run_id}/events", params={"limit": 201}).status_code == 422
        monkeypatch.setitem(
            main.app.dependency_overrides, main.current_tenant,
            lambda: main.Tenant(f"other-{workspace}", "Other workspace"),
        )
        assert client.get(f"/api/runs/{run_id}/events").status_code == 404
        assert calls == 1


def test_active_replay_does_not_claim_or_cancel(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "active-replay.db")
    repository.init()
    called = False

    async def model(_messages, _model=None, _provider=None, _usage_callback=None):
        nonlocal called
        called = True
        yield "unexpected"

    service = GatewayService(repository_provider=lambda: repository, stream_runner=model)
    context = RequestContext(request_id="active-replay", workspace_id="workspace-a")

    async def run() -> tuple[str, dict]:
        result = await service.stream_chat(ChatCommand(prompt="hello"), context)
        snapshot = service.replay_stream_events(context, result.run_id)
        state = repository.get_stream_run_state(context.workspace_id, result.run_id)
        assert state["stream_state"] == "pending"
        await result.chunks.aclose()
        return result.run_id, snapshot

    run_id, snapshot = asyncio.run(run())
    assert snapshot["status"] == "running"
    assert snapshot["events"] == []
    assert called is False
    assert repository.get_run(context.workspace_id, run_id)["error_code"] == "stream_not_started"


def test_http_disconnect_still_cancels_and_leaves_read_only_replay(tmp_path, monkeypatch) -> None:
    repository = SQLiteRepository(tmp_path / "disconnect-replay.db")
    repository.init()
    workspace = f"disconnect-replay-{uuid4()}"
    monkeypatch.setattr(
        main, "gateway_service", GatewayService(repository_provider=lambda: repository)
    )
    monkeypatch.setitem(
        main.app.dependency_overrides, main.current_tenant,
        lambda: main.Tenant(workspace, "Disconnect workspace"),
    )

    async def exercise() -> tuple[str, bool]:
        cancelled = asyncio.Event()
        disconnect = asyncio.Event()

        async def model(_messages, _model, _tools, _provider, *, on_delta):
            await on_delta("partial")
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        monkeypatch.setattr("app.services.gateway.stream_completion", model)
        body = b'{"prompt":"hello"}'
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "POST", "scheme": "http",
            "path": "/api/chat/stream", "raw_path": b"/api/chat/stream",
            "query_string": b"", "root_path": "", "headers": [
                (b"content-type", b"application/json"),
            ],
            "client": ("testclient", 123), "server": ("testserver", 80),
        }
        request_sent = False

        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and b"partial" in message.get("body", b""):
                disconnect.set()

        await asyncio.wait_for(main.app(scope, receive, send), 5)
        assert await asyncio.wait_for(cancelled.wait(), 5) is True
        runs = repository.list_runs(workspace)
        assert len(runs) == 1
        return runs[0]["id"], cancelled.is_set()

    run_id, was_cancelled = asyncio.run(exercise())
    assert was_cancelled
    replay = repository.read_stream_events(workspace, run_id)
    assert [event["data"].get("content") for event in replay["events"]] == [
        "partial", None,
    ]
    assert replay["status"] == "cancelled"
    assert replay["events"][-1]["data"]["error_code"] == "agent_cancelled"


def test_event_count_and_payload_are_bounded(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "bounded-replay.db")
    repository.init()
    workspace = f"bounded-replay-{uuid4()}"
    run = _run(repository, workspace)
    assert repository.claim_stream_run(
        workspace, run["id"], claimed_at=_time(), lease_until=_time(300)
    )
    with pytest.raises(StreamEventLimitError, match="size limit"):
        repository.append_stream_event(workspace, run["id"], {"content": "x" * (2 * 1024 * 1024 + 4096)})
    # A low bound exercises capacity without thousands of database round trips.
    from app import repository as repository_module

    original_limit = repository_module._MAX_STREAM_EVENTS_PER_RUN
    repository_module._MAX_STREAM_EVENTS_PER_RUN = 2
    try:
        assert repository.append_stream_event(workspace, run["id"], {"content": "a"}) == 1
        assert repository.append_stream_event(workspace, run["id"], {"content": "b"}) == 2
        with pytest.raises(StreamEventLimitError, match="count exceeds"):
            repository.append_stream_event(workspace, run["id"], {"content": "c"})
    finally:
        repository_module._MAX_STREAM_EVENTS_PER_RUN = original_limit
    assert repository.close_stream_run(
        workspace, run["id"], expected_state="claimed", status="failed",
        error_code="upstream_invalid_response",
    )
    assert [item["cursor"] for item in repository.read_stream_events(workspace, run["id"])[
        "events"
    ]] == [1, 2, 3]


def test_event_count_limit_ends_stream_with_replayable_terminal(tmp_path, monkeypatch) -> None:
    from app import repository as repository_module

    repository = SQLiteRepository(tmp_path / "event-limit-stream.db")
    repository.init()
    monkeypatch.setattr(repository_module, "_MAX_STREAM_EVENTS_PER_RUN", 2)

    async def model(_messages, _model=None, _provider=None, _usage_callback=None):
        for content in ("a", "b", "c"):
            yield content

    service = GatewayService(repository_provider=lambda: repository, stream_runner=model)
    context = RequestContext(request_id="event-limit", workspace_id="workspace-a")

    async def run() -> str:
        result = await service.stream_chat(ChatCommand(prompt="hello"), context)
        with pytest.raises(UpstreamResponseError, match="event count"):
            _ = [event async for event in result.chunks]
        return result.run_id

    run_id = asyncio.run(run())
    replay = repository.read_stream_events(context.workspace_id, run_id)
    assert [item["cursor"] for item in replay["events"]] == [1, 2, 3]
    assert replay["status"] == "failed"
    assert replay["error_code"] == "upstream_invalid_response"
    assert replay["events"][-1]["data"]["error_code"] == "upstream_invalid_response"
