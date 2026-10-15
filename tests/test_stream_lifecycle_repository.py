"""Durable stream admission and recovery contracts for both SQL adapters."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest

from app.repository import PostgresRepository, SQLiteRepository


def _iso(value: datetime) -> str:
    return value.isoformat()


def _stream_run(repository, workspace_id: str, deadline: datetime) -> tuple[dict, dict]:
    session = repository.create_session(workspace_id, "stream")
    run = repository.create_run(
        workspace_id,
        session["id"],
        str(uuid4()),
        None,
        "test-model",
        stream_start_deadline_at=_iso(deadline),
    )
    return session, run


def _attempt(run: dict) -> dict:
    return {
        "attempt_id": str(uuid4()),
        "model_call_id": str(uuid4()),
        "step_id": str(uuid4()),
        "tenant_id": run["tenant_id"],
        "principal_id": None,
        "api_key_id": None,
        "request_id": run["request_id"],
        "run_id": run["id"],
        "sequence": 1,
        "iteration": 1,
        "model": "test-model",
        "provider_config_id": None,
        "pricing_snapshot": {"version": 1},
        "input_content": "hello",
        "step_metadata": {"stream": True},
    }


def _exercise_stream_lifecycle(repository, workspace_id: str) -> None:
    now = datetime.now(UTC)
    session, pending = _stream_run(repository, workspace_id, now + timedelta(minutes=5))
    assert pending["stream_state"] == "pending"
    assert repository.get_stream_run_state(workspace_id, pending["id"])["status"] == "running"
    assert (
        repository.request_stream_cancel(
            workspace_id,
            pending["id"],
            requested_at=_iso(now),
        )
        == "closed_pending"
    )
    assert not repository.claim_stream_run(
        workspace_id,
        pending["id"],
        claimed_at=_iso(now),
        lease_until=_iso(now + timedelta(minutes=1)),
    )
    stopped = repository.get_run(workspace_id, pending["id"])
    assert stopped["status"] == "failed"
    assert stopped["error_code"] == "stream_not_started"
    assert stopped["steps"][0]["kind"] == "agent"
    assert stopped["steps"][0]["metadata"]["error_code"] == "stream_not_started"
    assert repository.list_model_usage(workspace_id, pending["id"]) == []
    with pytest.raises(ValueError, match="not active"):
        repository.append_run_step(
            workspace_id,
            pending["id"],
            2,
            "tool",
            "late",
            "succeeded",
            "",
            "",
            {},
        )
    assert (
        repository.request_stream_cancel(
            workspace_id,
            pending["id"],
            requested_at=_iso(now),
        )
        is None
    )

    _, successful = _stream_run(repository, workspace_id, now + timedelta(minutes=5))
    second = (
        type(repository)(repository.database_path)
        if isinstance(repository, SQLiteRepository)
        else type(repository)(repository.database_url)
    )
    assert repository.claim_stream_run(
        workspace_id,
        successful["id"],
        claimed_at=_iso(now),
        lease_until=_iso(now + timedelta(minutes=1)),
    )
    assert not second.claim_stream_run(
        workspace_id,
        successful["id"],
        claimed_at=_iso(now),
        lease_until=_iso(now + timedelta(minutes=1)),
    )
    with pytest.raises(KeyError):
        repository.begin_model_usage({**_attempt(successful), "request_id": "wrong"})
    repository.append_run_step(
        workspace_id,
        successful["id"],
        1,
        "agent",
        "accepted",
        "succeeded",
        "",
        "",
        {},
    )
    assert repository.close_stream_run(
        workspace_id,
        successful["id"],
        expected_state="claimed",
        status="succeeded",
        assistant_message=(successful["session_id"], "answer"),
        closed_at=_iso(now + timedelta(seconds=1)),
    )
    assert not repository.close_stream_run(
        workspace_id,
        successful["id"],
        expected_state="claimed",
        status="succeeded",
        closed_at=_iso(now + timedelta(seconds=2)),
    )
    assert repository.get_run(workspace_id, successful["id"])["status"] == "succeeded"
    assert (
        repository.get_session(workspace_id, successful["session_id"])["messages"][-1]["content"]
        == "answer"
    )
    with pytest.raises(ValueError, match="not active"):
        repository.append_run_step(
            workspace_id,
            successful["id"],
            2,
            "tool",
            "late",
            "succeeded",
            "",
            "",
            {},
        )
    with pytest.raises(ValueError):
        repository.finish_run(workspace_id, successful["id"], "failed")

    _, active = _stream_run(repository, workspace_id, now + timedelta(minutes=10))
    assert repository.claim_stream_run(
        workspace_id,
        active["id"],
        claimed_at=_iso(now),
        lease_until=_iso(now + timedelta(minutes=1)),
    )
    receipt = repository.begin_model_usage(_attempt(active))
    assert receipt["usage_status"] == "IN_FLIGHT"
    assert not repository.close_stream_run(
        workspace_id,
        active["id"],
        expected_state="claimed",
        status="succeeded",
        closed_at=_iso(now + timedelta(minutes=2)),
    )
    assert repository.reconcile_stale_stream_runs(_iso(now + timedelta(minutes=2)))["claimed"] >= 1
    recovered = repository.get_run(workspace_id, active["id"])
    assert recovered["status"] == "failed"
    assert recovered["error_code"] == "stream_execution_indeterminate"
    assert [step["status"] for step in recovered["steps"]] == ["failed", "failed"]
    assert recovered["steps"][0]["metadata"]["usage_status"] == "UNKNOWN"
    assert recovered["steps"][1]["metadata"]["error_code"] == "stream_execution_indeterminate"
    settled = repository.list_model_usage(workspace_id, active["id"])[0]
    assert settled["usage_status"] == "UNKNOWN"
    assert settled["termination"] == "FAILED_OR_INDETERMINATE"
    assert settled["prompt_tokens"] is None
    with pytest.raises(ValueError, match="not active"):
        repository.append_run_step(
            workspace_id,
            active["id"],
            3,
            "tool",
            "late",
            "succeeded",
            "",
            "",
            {},
        )
    with pytest.raises(KeyError):
        repository.begin_model_usage(_attempt(active))

    _, stale = _stream_run(repository, workspace_id, now - timedelta(seconds=1))
    assert repository.reconcile_stale_stream_runs(_iso(now))["pending"] >= 1
    assert repository.get_run(workspace_id, stale["id"])["error_code"] == "stream_not_started"
    assert repository.list_model_usage(workspace_id, stale["id"]) == []
    assert session["id"] != successful["session_id"]

    _, broken = _stream_run(repository, workspace_id, now + timedelta(minutes=5))
    assert repository.claim_stream_run(
        workspace_id,
        broken["id"],
        claimed_at=_iso(now),
        lease_until=_iso(now + timedelta(minutes=1)),
    )
    repository.begin_model_usage(_attempt(broken))
    with pytest.raises(RuntimeError, match="IN_FLIGHT"):
        repository.close_stream_run(
            workspace_id,
            broken["id"],
            expected_state="claimed",
            status="succeeded",
            closed_at=_iso(now + timedelta(seconds=1)),
        )
    assert repository.get_run(workspace_id, broken["id"])["status"] == "running"
    assert repository.close_stream_run(
        workspace_id,
        broken["id"],
        expected_state="claimed",
        status="failed",
        error_code="internal_error",
        closed_at=_iso(now + timedelta(seconds=1)),
    )
    assert repository.list_model_usage(workspace_id, broken["id"])[0]["usage_status"] == "UNKNOWN"
    assert [step["status"] for step in repository.get_run(workspace_id, broken["id"])["steps"]] == [
        "failed",
        "failed",
    ]

    _, expired = _stream_run(repository, workspace_id, now + timedelta(minutes=5))
    assert repository.claim_stream_run(
        workspace_id,
        expired["id"],
        claimed_at=_iso(now - timedelta(minutes=2)),
        lease_until=_iso(now - timedelta(minutes=1)),
    )
    with pytest.raises(ValueError, match="not active"):
        repository.append_run_step(
            workspace_id,
            expired["id"],
            1,
            "tool",
            "late",
            "succeeded",
            "",
            "",
            {},
        )
    with pytest.raises(KeyError):
        repository.begin_model_usage(_attempt(expired))
    assert repository.reconcile_expired_stream_run(workspace_id, expired["id"], now=_iso(now))
    assert not repository.reconcile_expired_stream_run(workspace_id, expired["id"], now=_iso(now))
    assert repository.get_run(workspace_id, expired["id"])["error_code"] == (
        "stream_execution_indeterminate"
    )

    _, cancellation = _stream_run(repository, workspace_id, now + timedelta(minutes=5))
    assert repository.claim_stream_run(
        workspace_id,
        cancellation["id"],
        claimed_at=_iso(now),
        lease_until=_iso(now + timedelta(minutes=1)),
    )
    assert (
        second.request_stream_cancel(
            workspace_id,
            cancellation["id"],
            requested_at=_iso(now),
        )
        == "requested_claimed"
    )
    with pytest.raises(ValueError, match="not active"):
        repository.append_run_step(
            workspace_id,
            cancellation["id"],
            1,
            "tool",
            "late",
            "succeeded",
            "",
            "",
            {},
        )
    assert not repository.close_stream_run(
        workspace_id,
        cancellation["id"],
        expected_state="claimed",
        status="failed",
        error_code="internal_error",
        closed_at=_iso(now + timedelta(seconds=1)),
    )
    assert not repository.close_stream_run(
        workspace_id,
        cancellation["id"],
        expected_state="claimed",
        status="succeeded",
        closed_at=_iso(now + timedelta(seconds=1)),
    )
    assert repository.close_stream_run(
        workspace_id,
        cancellation["id"],
        expected_state="claimed",
        status="cancelled",
        error_code="agent_cancelled",
        closed_at=_iso(now + timedelta(seconds=1)),
    )
    assert repository.get_run(workspace_id, cancellation["id"])["error_code"] == "agent_cancelled"


def _exercise_competing_claims(repository, workspace_id: str) -> None:
    now = datetime.now(UTC)
    _, run = _stream_run(repository, workspace_id, now + timedelta(minutes=5))
    barrier = Barrier(3)

    def claim() -> bool:
        second = (
            SQLiteRepository(repository.database_path)
            if isinstance(repository, SQLiteRepository)
            else PostgresRepository(repository.database_url)
        )
        barrier.wait()
        return second.claim_stream_run(
            workspace_id,
            run["id"],
            claimed_at=_iso(now),
            lease_until=_iso(now + timedelta(minutes=1)),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(claim)
        second = pool.submit(claim)
        barrier.wait()
        assert [first.result(), second.result()].count(True) == 1
    assert repository.get_stream_run_state(workspace_id, run["id"])["stream_state"] == "claimed"


def test_sqlite_stream_lifecycle_repository(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "stream-lifecycle.db")
    repository.init()
    _exercise_stream_lifecycle(repository, f"stream-{uuid4()}")
    _exercise_competing_claims(repository, f"stream-{uuid4()}")


@pytest.mark.skipif(
    not os.getenv("AIGC_LITE_TEST_POSTGRES_URL"),
    reason="AIGC_LITE_TEST_POSTGRES_URL is not configured",
)
def test_postgres_stream_lifecycle_repository() -> None:
    repository = PostgresRepository(os.environ["AIGC_LITE_TEST_POSTGRES_URL"])
    repository.init()
    _exercise_stream_lifecycle(repository, f"stream-{uuid4()}")
    _exercise_competing_claims(repository, f"stream-{uuid4()}")
