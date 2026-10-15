"""Regression coverage for the persistent lifecycle of streaming chat runs."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import main
from app.core.contracts import ChatCommand, RequestContext
from app.core.errors import (
    AgentCancelledError,
    RunNotActiveError,
    StreamNotStartedError,
    UpstreamRequestError,
)
from app.repository import SQLiteRepository
from app.services.gateway import GatewayService, _ActiveRunRegistry
from app.services.model_usage import ModelUsageObserver

WORKSPACE = "workspace-a"


def _repository(tmp_path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "stream-lifecycle.db")
    repository.init()
    return repository


def _context(request_id: str) -> RequestContext:
    return RequestContext(request_id=request_id, workspace_id=WORKSPACE)


def _future_time() -> str:
    return (datetime.now(UTC) + timedelta(days=1)).isoformat()


def test_never_consumed_stream_is_reconciled_without_a_model_attempt(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path)
    model_called = False

    async def model(_messages, _model, _tools, _provider, *, on_delta):
        nonlocal model_called
        model_called = True
        await on_delta("unexpected")
        return {"role": "assistant", "content": "unexpected"}

    monkeypatch.setattr("app.services.gateway.stream_completion", model)
    service = GatewayService(repository_provider=lambda: repository)

    async def run() -> tuple[str, dict]:
        result = await service.stream_chat(ChatCommand(prompt="hello"), _context("never-read"))
        assert repository.get_run(WORKSPACE, result.run_id)["status"] == "running"
        assert repository.list_model_usage(WORKSPACE, result.run_id) == []
        reconciled = service.reconcile_stream_runs(now=_future_time())
        await result.chunks.aclose()
        return result.run_id, reconciled

    run_id, reconciled = asyncio.run(run())
    assert reconciled == {"pending": 1, "claimed": 0}
    assert model_called is False
    run = repository.get_run(WORKSPACE, run_id)
    assert run["status"] == "failed"
    assert run["error_code"] == "stream_not_started"
    assert run["stream_state"] == "closed"
    assert len(run["steps"]) == 1
    assert repository.list_model_usage(WORKSPACE, run_id) == []


@pytest.mark.parametrize("action", ["close", "cancel"])
def test_pre_first_read_close_or_cancel_does_not_claim_a_model_attempt(
    tmp_path, monkeypatch, action: str
) -> None:
    repository = _repository(tmp_path)
    model_called = False

    async def model(_messages, _model, _tools, _provider, *, on_delta):
        nonlocal model_called
        model_called = True
        await on_delta("unexpected")
        return {"role": "assistant", "content": "unexpected"}

    monkeypatch.setattr("app.services.gateway.stream_completion", model)
    service = GatewayService(repository_provider=lambda: repository)
    context = _context(f"pre-first-{action}")

    async def run() -> str:
        result = await service.stream_chat(ChatCommand(prompt="hello"), context)
        if action == "close":
            await result.chunks.aclose()
        else:
            service.cancel_run(context, result.run_id)
            await result.chunks.aclose()
        return result.run_id

    run_id = asyncio.run(run())
    assert model_called is False
    detail = repository.get_run(WORKSPACE, run_id)
    assert detail["status"] == "failed"
    assert detail["error_code"] == "stream_not_started"
    assert detail["stream_state"] == "closed"
    assert len(detail["steps"]) == 1
    assert repository.list_model_usage(WORKSPACE, run_id) == []


@pytest.mark.parametrize("cancel_first", [False, True])
def test_first_read_racing_cancel_has_one_terminal_transition(
    tmp_path, monkeypatch, cancel_first: bool
) -> None:
    repository = _repository(tmp_path)
    model_called = False

    async def model(_messages, _model, _tools, _provider, *, on_delta):
        nonlocal model_called
        model_called = True
        await on_delta("unexpected")
        return {"role": "assistant", "content": "unexpected"}

    monkeypatch.setattr("app.services.gateway.stream_completion", model)
    active_runs = _ActiveRunRegistry()
    service = GatewayService(repository_provider=lambda: repository, active_runs=active_runs)
    context = _context(f"first-read-race-{cancel_first}")

    async def run() -> str:
        result = await service.stream_chat(ChatCommand(prompt="hello"), context)
        barrier = asyncio.Event()

        async def read() -> None:
            await barrier.wait()
            try:
                await anext(result.chunks)
            except (
                AgentCancelledError,
                asyncio.CancelledError,
                RunNotActiveError,
                StreamNotStartedError,
                StopAsyncIteration,
            ):
                pass

        async def cancel() -> None:
            await barrier.wait()
            service.cancel_run(context, result.run_id)

        operations = (cancel, read) if cancel_first else (read, cancel)
        tasks = [asyncio.create_task(operation()) for operation in operations]
        barrier.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        await result.chunks.aclose()
        return result.run_id

    run_id = asyncio.run(run())
    run = repository.get_run(WORKSPACE, run_id)
    assert run["status"] in {"failed", "cancelled"}
    assert run["error_code"] in {"stream_not_started", "agent_cancelled"}
    assert run["stream_state"] == "closed"
    assert sum(step["kind"] == "agent" for step in run["steps"]) == 1
    assert repository.list_model_usage(WORKSPACE, run_id) == []
    assert not model_called
    assert not active_runs._tasks


@pytest.mark.parametrize("cancel_yields", [False, True])
def test_completion_and_cancellation_race_has_one_terminal_model_attempt(
    tmp_path, monkeypatch, cancel_yields: bool
) -> None:
    repository = _repository(tmp_path)

    async def run() -> str:
        finish_allowed = asyncio.Event()
        start_race = asyncio.Event()

        async def model(_messages, _model, _tools, _provider, *, on_delta):
            await on_delta("partial")
            await finish_allowed.wait()
            return {
                "role": "assistant",
                "content": "partial",
                "_usage": {"prompt_tokens": 3, "completion_tokens": 1},
            }

        monkeypatch.setattr("app.services.gateway.stream_completion", model)
        service = GatewayService(repository_provider=lambda: repository)
        context = _context(f"race-{cancel_yields}")
        result = await service.stream_chat(ChatCommand(prompt="hello"), context)
        assert (await asyncio.wait_for(anext(result.chunks), 2))["content"] == "partial"

        async def complete() -> None:
            await start_race.wait()
            finish_allowed.set()

        async def cancel() -> None:
            await start_race.wait()
            if cancel_yields:
                await asyncio.sleep(0)
            try:
                service.cancel_run(context, result.run_id)
            except RunNotActiveError:
                pass  # The completion won the race.

        contenders = [asyncio.create_task(complete()), asyncio.create_task(cancel())]
        start_race.set()
        await asyncio.gather(*contenders)
        try:
            _ = [event async for event in result.chunks]
        except AgentCancelledError:
            pass
        return result.run_id

    run_id = asyncio.run(run())
    run = repository.get_run(WORKSPACE, run_id)
    receipts = repository.list_model_usage(WORKSPACE, run_id)
    assert run["status"] in {"succeeded", "cancelled"}
    assert len(receipts) == 1
    assert receipts[0]["usage_status"] != "IN_FLIGHT"
    model_steps = [step for step in run["steps"] if step["kind"] == "model"]
    assert len(model_steps) == 1
    if run["status"] == "succeeded":
        assert receipts[0]["usage_status"] == "REPORTED"
        assert model_steps[0]["status"] == "succeeded"
        assert not any(step["kind"] == "agent" for step in run["steps"])
    else:
        # This barrier cancels while the provider is pending. The next test
        # covers cancellation after observer.finish has recorded REPORTED.
        assert receipts[0]["usage_status"] == "UNKNOWN"
        assert model_steps[0]["status"] == "cancelled"
        assert sum(step["kind"] == "agent" for step in run["steps"]) == 1


def test_durable_cancel_after_reported_usage_prevents_stream_success(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path)
    other_repository = SQLiteRepository(tmp_path / "stream-lifecycle.db")
    original_close = repository.close_stream_run
    injected = False

    def close_with_competing_cancel(*args, **kwargs):
        nonlocal injected
        if kwargs["status"] == "succeeded":
            assert not injected
            run_id = args[1]
            before = repository.get_run(WORKSPACE, run_id)
            receipts = repository.list_model_usage(WORKSPACE, run_id)
            assert before["status"] == "running"
            assert len(receipts) == 1
            assert receipts[0]["usage_status"] == "REPORTED"
            assert before["steps"][0]["status"] == "succeeded"
            assert (
                other_repository.request_stream_cancel(
                    WORKSPACE,
                    run_id,
                    requested_at=datetime.now(UTC).isoformat(),
                )
                == "requested_claimed"
            )
            injected = True
        return original_close(*args, **kwargs)

    monkeypatch.setattr(repository, "close_stream_run", close_with_competing_cancel)

    async def model(_messages, _model, _tools, _provider, *, on_delta):
        await on_delta("answer")
        return {
            "role": "assistant",
            "content": "answer",
            "_usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }

    monkeypatch.setattr("app.services.gateway.stream_completion", model)
    service = GatewayService(repository_provider=lambda: repository)

    async def run() -> tuple[str, str]:
        result = await service.stream_chat(
            ChatCommand(prompt="hello"), _context("cancel-after-receipt")
        )
        try:
            _ = [event async for event in result.chunks]
        except (AgentCancelledError, asyncio.CancelledError, RunNotActiveError):
            pass
        return result.run_id, result.session_id

    run_id, session_id = asyncio.run(run())
    assert injected
    detail = repository.get_run(WORKSPACE, run_id)
    receipts = repository.list_model_usage(WORKSPACE, run_id)
    assert (detail["status"], detail["error_code"]) == ("cancelled", "agent_cancelled")
    assert detail["stream_state"] == "closed"
    assert [(step["kind"], step["status"]) for step in detail["steps"]] == [
        ("model", "succeeded"),
        ("agent", "cancelled"),
    ]
    assert len(receipts) == 1
    assert receipts[0]["usage_status"] == "REPORTED"
    assert receipts[0]["termination"] == "COMPLETED"
    assert receipts[0]["step_id"] == detail["steps"][0]["id"]
    assert repository.get_session(WORKSPACE, session_id)["messages"][-1]["role"] == "user"


def test_http_sse_cross_instance_cancel_emits_structured_error(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "http-stream-lifecycle.db"
    repository_a = SQLiteRepository(database_path)
    repository_a.init()
    repository_b = SQLiteRepository(database_path)
    gateway_a = GatewayService(repository_provider=lambda: repository_a)
    gateway_b = GatewayService(repository_provider=lambda: repository_b)
    monkeypatch.setattr(main, "gateway_service", gateway_a)
    monkeypatch.setitem(
        main.app.dependency_overrides,
        main.current_tenant,
        lambda: main.Tenant(WORKSPACE, "Workspace A"),
    )
    monkeypatch.setattr("app.services.gateway.settings.stream_cancel_poll_seconds", 0.01)

    async def run() -> tuple[httpx.Response, str]:
        partial_emitted = asyncio.Event()
        release = asyncio.Event()

        async def model(_messages, _model, _tools, _provider, *, on_delta):
            await on_delta("partial")
            partial_emitted.set()
            await release.wait()
            return {"role": "assistant", "content": "partial"}

        monkeypatch.setattr("app.services.gateway.stream_completion", model)
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            request = asyncio.create_task(client.post("/api/chat/stream", json={"prompt": "hello"}))
            try:
                await asyncio.wait_for(partial_emitted.wait(), 5)
                runs = repository_a.list_runs(WORKSPACE)
                assert len(runs) == 1
                run_id = runs[0]["id"]
                assert (
                    repository_a.get_stream_run_state(WORKSPACE, run_id)["stream_state"]
                    == "claimed"
                )
                gateway_b.cancel_run(_context("external-cancel"), run_id)
                response = await asyncio.wait_for(request, 5)
                return response, run_id
            finally:
                release.set()
                if not request.done():
                    request.cancel()
                    await asyncio.gather(request, return_exceptions=True)

    response, run_id = asyncio.run(run())
    payloads = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-run-id"] == run_id
    assert json.loads(payloads[0])["content"] == "partial"
    assert json.loads(payloads[-1])["code"] == "agent_cancelled"
    assert "error" in json.loads(payloads[-1])
    assert "[DONE]" not in payloads
    detail = repository_a.get_run(WORKSPACE, run_id)
    receipts = repository_a.list_model_usage(WORKSPACE, run_id)
    assert (detail["status"], detail["error_code"]) == ("cancelled", "agent_cancelled")
    assert len(receipts) == 1
    assert receipts[0]["usage_status"] == "UNKNOWN"
    assert receipts[0]["output_bytes"] == len("partial")


def test_http_sse_reaper_wins_provider_error_race(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "http-reaper-race.db"
    repository_a = SQLiteRepository(database_path)
    repository_a.init()
    repository_b = SQLiteRepository(database_path)
    gateway_a = GatewayService(repository_provider=lambda: repository_a)
    gateway_b = GatewayService(repository_provider=lambda: repository_b)
    monkeypatch.setattr(main, "gateway_service", gateway_a)
    monkeypatch.setitem(
        main.app.dependency_overrides,
        main.current_tenant,
        lambda: main.Tenant(WORKSPACE, "Workspace A"),
    )
    # Keep the local watcher asleep until after the provider raises.
    monkeypatch.setattr("app.services.gateway.settings.stream_cancel_poll_seconds", 60)

    async def run() -> tuple[httpx.Response, str]:
        partial_emitted = asyncio.Event()
        release_error = asyncio.Event()

        async def model(_messages, _model, _tools, _provider, *, on_delta):
            await on_delta("partial")
            partial_emitted.set()
            await release_error.wait()
            raise UpstreamRequestError("provider failed after lease reconciliation")

        monkeypatch.setattr("app.services.gateway.stream_completion", model)
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            request = asyncio.create_task(client.post("/api/chat/stream", json={"prompt": "hello"}))
            try:
                await asyncio.wait_for(partial_emitted.wait(), 5)
                runs = repository_a.list_runs(WORKSPACE)
                assert len(runs) == 1
                run_id = runs[0]["id"]
                assert (
                    repository_a.get_stream_run_state(WORKSPACE, run_id)["stream_state"]
                    == "claimed"
                )
                assert gateway_b.reconcile_stream_runs(now=_future_time()) == {
                    "pending": 0,
                    "claimed": 1,
                }
                assert repository_b.get_run(WORKSPACE, run_id)["error_code"] == (
                    "stream_execution_indeterminate"
                )
                release_error.set()
                response = await asyncio.wait_for(request, 5)
                return response, run_id
            finally:
                release_error.set()
                if not request.done():
                    request.cancel()
                    await asyncio.gather(request, return_exceptions=True)

    response, run_id = asyncio.run(run())
    payloads = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert response.status_code == 200
    assert response.headers["x-run-id"] == run_id
    assert json.loads(payloads[0])["content"] == "partial"
    assert json.loads(payloads[-1])["code"] == "stream_execution_indeterminate"
    assert "[DONE]" not in payloads
    detail = repository_a.get_run(WORKSPACE, run_id)
    receipts = repository_a.list_model_usage(WORKSPACE, run_id)
    assert (detail["status"], detail["error_code"]) == (
        "failed",
        "stream_execution_indeterminate",
    )
    assert len(receipts) == 1
    assert receipts[0]["usage_status"] == "UNKNOWN"
    assert receipts[0]["termination"] == "FAILED_OR_INDETERMINATE"


def test_http_sse_first_pull_after_pending_expiry_without_reaper(tmp_path, monkeypatch) -> None:
    repository = SQLiteRepository(tmp_path / "http-pending-expiry.db")
    repository.init()
    gateway = GatewayService(repository_provider=lambda: repository)
    original_stream_chat = gateway.stream_chat
    model_called = False

    async def expire_before_body_pull(command, context):
        result = await original_stream_chat(command, context)
        past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        with repository._connect() as db:
            updated = db.execute(
                "UPDATE agent_runs SET stream_start_deadline_at = ? "
                "WHERE tenant_id = ? AND id = ? AND stream_state = 'pending'",
                (past, WORKSPACE, result.run_id),
            ).rowcount
        assert updated == 1
        return result

    async def model(_messages, _model, _tools, _provider, *, on_delta):
        nonlocal model_called
        model_called = True
        await on_delta("unexpected")
        return {"role": "assistant", "content": "unexpected"}

    monkeypatch.setattr(gateway, "stream_chat", expire_before_body_pull)
    monkeypatch.setattr("app.services.gateway.stream_completion", model)
    monkeypatch.setattr(main, "gateway_service", gateway)
    monkeypatch.setitem(
        main.app.dependency_overrides,
        main.current_tenant,
        lambda: main.Tenant(WORKSPACE, "Workspace A"),
    )

    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/api/chat/stream", json={"prompt": "hello"})

    response = asyncio.run(run())
    payloads = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert response.status_code == 200
    assert len(payloads) == 1
    assert json.loads(payloads[0])["code"] == StreamNotStartedError.code.value
    assert not model_called
    run_id = response.headers["x-run-id"]
    detail = repository.get_run(WORKSPACE, run_id)
    assert (detail["status"], detail["error_code"]) == (
        "failed",
        StreamNotStartedError.code.value,
    )
    assert detail["stream_state"] == "closed"
    assert repository.list_model_usage(WORKSPACE, run_id) == []


def test_http_sse_claimed_lease_expiry_without_reaper(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "http-claimed-expiry.db"
    repository_a = SQLiteRepository(database_path)
    repository_a.init()
    repository_b = SQLiteRepository(database_path)
    gateway_a = GatewayService(repository_provider=lambda: repository_a)
    monkeypatch.setattr(main, "gateway_service", gateway_a)
    monkeypatch.setitem(
        main.app.dependency_overrides,
        main.current_tenant,
        lambda: main.Tenant(WORKSPACE, "Workspace A"),
    )
    # Provider failure reaches Gateway A before the watcher polls the expired lease.
    monkeypatch.setattr("app.services.gateway.settings.stream_cancel_poll_seconds", 60)

    async def run() -> tuple[httpx.Response, str]:
        partial_emitted = asyncio.Event()
        release_error = asyncio.Event()

        async def model(_messages, _model, _tools, _provider, *, on_delta):
            await on_delta("partial")
            partial_emitted.set()
            await release_error.wait()
            raise UpstreamRequestError("provider failed after lease expiry")

        monkeypatch.setattr("app.services.gateway.stream_completion", model)
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            request = asyncio.create_task(client.post("/api/chat/stream", json={"prompt": "hello"}))
            try:
                await asyncio.wait_for(partial_emitted.wait(), 5)
                runs = repository_a.list_runs(WORKSPACE)
                assert len(runs) == 1
                run_id = runs[0]["id"]
                past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
                with repository_b._connect() as db:
                    updated = db.execute(
                        "UPDATE agent_runs SET stream_lease_until = ? "
                        "WHERE tenant_id = ? AND id = ? AND stream_state = 'claimed'",
                        (past, WORKSPACE, run_id),
                    ).rowcount
                assert updated == 1
                assert repository_b.get_run(WORKSPACE, run_id)["status"] == "running"
                release_error.set()
                response = await asyncio.wait_for(request, 5)
                return response, run_id
            finally:
                release_error.set()
                if not request.done():
                    request.cancel()
                    await asyncio.gather(request, return_exceptions=True)

    response, run_id = asyncio.run(run())
    payloads = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert response.status_code == 200
    assert response.headers["x-run-id"] == run_id
    assert json.loads(payloads[0])["content"] == "partial"
    assert json.loads(payloads[-1])["code"] == "stream_execution_indeterminate"
    assert "[DONE]" not in payloads
    detail = repository_a.get_run(WORKSPACE, run_id)
    receipts = repository_a.list_model_usage(WORKSPACE, run_id)
    assert (detail["status"], detail["error_code"]) == (
        "failed",
        "stream_execution_indeterminate",
    )
    assert len(receipts) == 1
    assert receipts[0]["usage_status"] == "UNKNOWN"


def test_partial_output_closed_stream_preserves_unknown_usage_and_bytes(
    tmp_path, monkeypatch
) -> None:
    repository = _repository(tmp_path)

    async def run() -> str:
        cancelled_upstream = asyncio.Event()

        async def model(_messages, _model, _tools, _provider, *, on_delta):
            await on_delta("partial")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled_upstream.set()
                raise

        monkeypatch.setattr("app.services.gateway.stream_completion", model)
        service = GatewayService(repository_provider=lambda: repository)
        result = await service.stream_chat(ChatCommand(prompt="hello"), _context("partial-close"))
        assert (await asyncio.wait_for(anext(result.chunks), 2))["content"] == "partial"
        await result.chunks.aclose()
        await asyncio.wait_for(cancelled_upstream.wait(), 2)
        return result.run_id

    run_id = asyncio.run(run())
    run = repository.get_run(WORKSPACE, run_id)
    receipts = repository.list_model_usage(WORKSPACE, run_id)
    assert len(receipts) == 1
    receipt = receipts[0]
    assert run["status"] == "cancelled"
    assert run["error_code"] == "agent_cancelled"
    assert receipt["usage_status"] == "UNKNOWN"
    assert receipt["prompt_tokens"] is None
    assert receipt["completion_tokens"] is None
    assert receipt["estimated_cost"] is None
    assert receipt["output_bytes"] == len("partial")
    assert receipt["termination"] == "CANCELLED_OR_TIMEOUT"
    assert run["steps"][0]["status"] == "cancelled"
    assert repository.get_session(WORKSPACE, run["session_id"])["messages"][-1]["role"] == "user"


def test_restart_reconciles_pending_and_claimed_streams_without_inventing_usage(
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    service = GatewayService(repository_provider=lambda: repository)

    async def create_abandoned_runs() -> tuple[str, str, str]:
        pending = await service.stream_chat(
            ChatCommand(prompt="pending"), _context("restart-pending")
        )
        claimed_unsent = await service.stream_chat(
            ChatCommand(prompt="claimed-unsent"), _context("restart-claimed-unsent")
        )
        claimed_inflight = await service.stream_chat(
            ChatCommand(prompt="claimed-inflight"), _context("restart-claimed-inflight")
        )
        claim_time = datetime.now(UTC)
        for result in (claimed_unsent, claimed_inflight):
            assert repository.claim_stream_run(
                WORKSPACE,
                result.run_id,
                claimed_at=claim_time.isoformat(),
                lease_until=(claim_time + timedelta(minutes=1)).isoformat(),
            )
        inflight_run = repository.get_run(WORKSPACE, claimed_inflight.run_id)
        observer = ModelUsageObserver(
            repository,
            _context("restart-claimed-inflight"),
            claimed_inflight.run_id,
            inflight_run["selected_model"],
            None,
            default_input_price=0,
            default_output_price=0,
            reserve_sequence=lambda: 1,
            step_metadata={"stream": True},
        )
        observer.begin(1, "claimed-inflight", inflight_run["selected_model"])
        assert (
            repository.list_model_usage(WORKSPACE, claimed_inflight.run_id)[0]["usage_status"]
            == "IN_FLIGHT"
        )
        return pending.run_id, claimed_unsent.run_id, claimed_inflight.run_id

    pending_id, claimed_unsent_id, claimed_inflight_id = asyncio.run(create_abandoned_runs())
    restarted_repository = SQLiteRepository(tmp_path / "stream-lifecycle.db")
    restarted_repository.init()
    restarted_service = GatewayService(repository_provider=lambda: restarted_repository)
    assert restarted_service.reconcile_stream_runs(now=_future_time()) == {
        "pending": 1,
        "claimed": 2,
    }
    assert restarted_service.reconcile_stream_runs(now=_future_time()) == {
        "pending": 0,
        "claimed": 0,
    }

    pending = restarted_repository.get_run(WORKSPACE, pending_id)
    claimed_unsent = restarted_repository.get_run(WORKSPACE, claimed_unsent_id)
    claimed_inflight = restarted_repository.get_run(WORKSPACE, claimed_inflight_id)
    assert (pending["status"], pending["error_code"]) == ("failed", "stream_not_started")
    assert (claimed_unsent["status"], claimed_unsent["error_code"]) == (
        "failed",
        "stream_execution_indeterminate",
    )
    assert (claimed_inflight["status"], claimed_inflight["error_code"]) == (
        "failed",
        "stream_execution_indeterminate",
    )
    assert all(
        run["stream_state"] == "closed" for run in (pending, claimed_unsent, claimed_inflight)
    )
    assert restarted_repository.list_model_usage(WORKSPACE, pending_id) == []
    assert restarted_repository.list_model_usage(WORKSPACE, claimed_unsent_id) == []
    receipts = restarted_repository.list_model_usage(WORKSPACE, claimed_inflight_id)
    assert len(receipts) == 1
    assert receipts[0]["usage_status"] == "UNKNOWN"
    assert receipts[0]["termination"] == "FAILED_OR_INDETERMINATE"
    assert receipts[0]["prompt_tokens"] is None
    assert receipts[0]["completion_tokens"] is None
    assert [step["status"] for step in claimed_inflight["steps"]] == ["failed", "failed"]
