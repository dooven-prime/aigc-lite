"""ModelUsageReceipt.v1 is an accounting observation, not spending authority."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

import app.repositories.ledger_snapshot as ledger_snapshot_module
from app import main, providers
from app.core.contracts import ChatCommand, RequestContext
from app.core.errors import AgentCancelledError, UpstreamResponseError
from app.core.model_usage import estimated_cost, normalize_usage, pricing_snapshot
from app.repository import SQLiteRepository
from app.services.gateway import GatewayService
from app.services.model_usage import ModelUsageObserver


def _repository(tmp_path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "model-usage.db")
    repository.init()
    return repository


def _run(repository: SQLiteRepository) -> dict:
    session = repository.create_session("workspace-a", "usage")
    return repository.create_run("workspace-a", session["id"], "request-a", None, "test-model")


def _begin_values(run: dict, *, sequence: int = 1) -> dict:
    return {
        "attempt_id": str(uuid4()),
        "model_call_id": str(uuid4()),
        "step_id": str(uuid4()),
        "tenant_id": "workspace-a",
        "principal_id": "user-a",
        "api_key_id": None,
        "request_id": "request-a",
        "run_id": run["id"],
        "sequence": sequence,
        "iteration": 1,
        "model": "test-model",
        "provider_config_id": "config-a",
        "pricing_snapshot": {
            "version": 1, "unit": "per_1000_tokens", "source": "workspace_model_config",
            "provider_config_id": "config-a", "price_known": True,
            "input_price": "0.5", "output_price": "1",
        },
        "input_content": "hello",
        "step_metadata": {"iteration": 1},
    }


def _finish_values(*, usage_status: str = "REPORTED", final_hash: str = "hash-a") -> dict:
    return {
        "usage_status": usage_status,
        "prompt_tokens": 4 if usage_status == "REPORTED" else None,
        "completion_tokens": 2 if usage_status == "REPORTED" else None,
        "estimated_cost": "0.004" if usage_status == "REPORTED" else None,
        "termination": "COMPLETED",
        "finish_reason": "stop",
        "provider_request_id": "vendor-a",
        "output_bytes": len("answer"),
        "finalization_hash": final_hash,
        "step_status": "succeeded",
        "output_content": "answer",
        "step_metadata": {"usage_status": usage_status},
    }


def test_unknown_zero_invalid_and_unconfirmed_pricing_are_distinct() -> None:
    assert normalize_usage(None).status == "UNKNOWN"
    assert normalize_usage({"prompt_tokens": 0, "completion_tokens": 0}).status == "REPORTED"
    assert normalize_usage({"prompt_tokens": 4}).completion_tokens is None
    assert normalize_usage({"prompt_tokens": 4, "completion_tokens": -1}).prompt_tokens is None
    assert normalize_usage({"prompt_tokens": True, "completion_tokens": 1}).status == "INVALID"
    assert normalize_usage({"prompt_tokens": "4", "completion_tokens": 1}).status == "INVALID"
    unconfirmed = pricing_snapshot(
        {"input_price": 0, "output_price": 0, "pricing_confirmed": 0},
        provider_config_id="config-a",
    )
    assert unconfirmed["price_known"] is False
    assert estimated_cost(normalize_usage({"prompt_tokens": 0, "completion_tokens": 0}), unconfirmed) is None
    confirmed = pricing_snapshot(
        {"input_price": 0, "output_price": 0, "pricing_confirmed": 1},
        provider_config_id="config-a",
    )
    assert estimated_cost(normalize_usage({"prompt_tokens": 0, "completion_tokens": 0}), confirmed) == "0"


def test_begin_and_finalize_are_atomic_idempotent_and_workspace_bound(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = _run(repository)
    values = _begin_values(run)
    first = repository.begin_model_usage(values)
    assert first["usage_status"] == "IN_FLIGHT"
    assert first["step_id"] == values["step_id"]
    assert repository.begin_model_usage(values)["attempt_id"] == values["attempt_id"]
    with pytest.raises(ValueError):
        repository.begin_model_usage({**values, "model": "different"})
    assert repository.get_model_usage("wrong-workspace", values["attempt_id"]) is None
    assert repository.get_run("workspace-a", run["id"])["steps"][0]["status"] == "running"

    second = _begin_values(run, sequence=1)
    with pytest.raises(sqlite3.IntegrityError):
        repository.begin_model_usage(second)
    assert repository.get_model_usage("workspace-a", second["attempt_id"]) is None

    finished = _finish_values()
    result = repository.finish_model_usage("workspace-a", values["attempt_id"], finished)
    assert result["usage_status"] == "REPORTED"
    assert repository.finish_model_usage("workspace-a", values["attempt_id"], finished)["attempt_id"] == values["attempt_id"]
    with pytest.raises(ValueError):
        repository.finish_model_usage(
            "workspace-a", values["attempt_id"], {**finished, "finalization_hash": "different"}
        )
    with pytest.raises(KeyError):
        repository.finish_model_usage("wrong-workspace", values["attempt_id"], finished)
    step = repository.get_run("workspace-a", run["id"])["steps"][0]
    assert step["status"] == "succeeded"
    assert step["output_content"] == "answer"

    repository.usage("workspace-a", "historical", 10, 5, 0.1)
    summary = repository.usage_summary("workspace-a")
    assert summary["requests"] == 2
    assert summary["legacy_requests"] == 1
    assert summary["receipt_attempts"] == 1
    assert summary["prompt_tokens"] == 14
    assert summary["cost"] == pytest.approx(0.104)


def test_observer_keeps_reported_usage_on_failed_model_turn(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = _run(repository)
    observer = ModelUsageObserver(
        repository,
        RequestContext(request_id="request-a", workspace_id="workspace-a", principal_id="user-a"),
        run["id"], "test-model",
        {"id": "config-a", "input_price": 0.5, "output_price": 1, "pricing_confirmed": 1},
        default_input_price=0,
        default_output_price=0,
        reserve_sequence=lambda: 1,
        step_metadata={},
    )
    observer.begin(1, "hello", "test-model")
    usage = {"prompt_tokens": 4, "completion_tokens": 2}
    observer.observe_usage(usage)
    observer.observe_usage(usage)
    result = observer.finish(error=UpstreamResponseError("LLM stream ended before completion"))
    assert result["usage_status"] == "REPORTED"
    assert result["termination"] == "STREAM_INTERRUPTED"
    assert result["estimated_cost"] == "0.004"
    assert repository.get_run("workspace-a", run["id"])["steps"][0]["status"] == "failed"


def test_real_agent_run_links_one_receipt_to_model_step_without_legacy_write(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path)

    async def fake_completion(_messages, _model=None, _tools=None, _provider=None):
        return {
            "role": "assistant", "content": "done",
            "_usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "_provider_request_id": "vendor-a", "_finish_reason": "stop",
        }

    monkeypatch.setattr("app.agent.completion", fake_completion)
    service = GatewayService(repository_provider=lambda: repository)
    context = RequestContext(request_id="http-request", workspace_id="workspace-a", principal_id="user-a")
    result = asyncio.run(service.chat(ChatCommand(prompt="hello"), context))
    receipts = repository.list_model_usage("workspace-a", result.run_id)
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["usage_status"] == "REPORTED"
    assert receipt["prompt_tokens"] == receipt["completion_tokens"] == 0
    assert receipt["estimated_cost"] is None  # default zero rates were not configured
    assert receipt["principal_id"] == "user-a"
    assert receipt["request_id"] == "http-request"
    assert receipt["provider_request_id"] == "vendor-a"
    step = repository.get_run("workspace-a", result.run_id)["steps"][0]
    assert step["id"] == receipt["step_id"]
    assert step["metadata"]["model_call_id"] == receipt["model_call_id"]
    assert repository.usage_summary("workspace-a")["legacy_requests"] == 0
    assert repository.usage_summary("workspace-a")["receipt_attempts"] == 1
    assert repository.usage_summary("workspace-a")["unpriced_reported_requests"] == 1


@pytest.mark.parametrize(
    ("usage_frame", "expected_status"),
    [
        (None, "UNKNOWN"),
        ({"prompt_tokens": 4, "completion_tokens": 2}, "REPORTED"),
    ],
)
def test_real_stream_preserves_usage_state_when_done_is_missing(
    tmp_path, monkeypatch, usage_frame, expected_status
) -> None:
    repository = _repository(tmp_path)
    event = {"id": "vendor-stream-a", "choices": [{"delta": {"content": "partial"}}], "usage": None}
    frames = [event]
    if usage_frame is not None:
        frames.append({"choices": [], "usage": usage_frame})
    body = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        providers.httpx, "AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(providers.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(providers.settings, "llm_base_url", "https://provider.test/v1")
    service = GatewayService(repository_provider=lambda: repository)
    context = RequestContext(request_id="stream-request", workspace_id="workspace-a")

    async def run():
        result = await service.stream_chat(ChatCommand(prompt="hello"), context)
        with pytest.raises(UpstreamResponseError):
            _ = [item async for item in result.chunks]
        return result

    result = asyncio.run(run())
    receipt = repository.list_model_usage("workspace-a", result.run_id)[0]
    assert receipt["usage_status"] == expected_status
    assert receipt["termination"] == "STREAM_INTERRUPTED"
    assert receipt["completion_tokens"] == (2 if usage_frame else None)
    assert receipt["provider_request_id"] == "vendor-stream-a"
    assert receipt["output_bytes"] == len("partial")
    assert repository.get_run("workspace-a", result.run_id)["status"] == "failed"
    assert repository.get_run("workspace-a", result.run_id)["steps"][0]["status"] == "failed"


def test_cancelled_stream_with_partial_output_keeps_unknown_usage(tmp_path) -> None:
    repository = _repository(tmp_path)

    async def controlled_stream(_messages, _model, _provider, _usage_callback):
        yield "partial"
        await asyncio.Event().wait()

    service = GatewayService(
        repository_provider=lambda: repository,
        stream_runner=controlled_stream,
    )
    context = RequestContext(request_id="cancel-stream", workspace_id="workspace-a")

    async def run():
        result = await service.stream_chat(ChatCommand(prompt="hello"), context)
        assert (await anext(result.chunks))["content"] == "partial"
        service.cancel_run(context, result.run_id)
        with pytest.raises(AgentCancelledError):
            await anext(result.chunks)
        return result

    result = asyncio.run(run())
    receipt = repository.list_model_usage("workspace-a", result.run_id)[0]
    assert receipt["usage_status"] == "UNKNOWN"
    assert receipt["prompt_tokens"] is None
    assert receipt["output_bytes"] == len("partial")
    assert receipt["termination"] == "CANCELLED_OR_TIMEOUT"
    assert repository.get_run("workspace-a", result.run_id)["status"] == "cancelled"


def test_malformed_provider_message_finalizes_unknown_instead_of_leaving_running(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path)

    async def fake_completion(_messages, _model=None, _tools=None, _provider=None):
        return {"role": "assistant", "content": 42}

    monkeypatch.setattr("app.agent.completion", fake_completion)
    service = GatewayService(repository_provider=lambda: repository)
    with pytest.raises(UpstreamResponseError):
        asyncio.run(service.chat(
            ChatCommand(prompt="hello"),
            RequestContext(request_id="bad-output", workspace_id="workspace-a"),
        ))
    runs = repository.list_runs("workspace-a")
    receipts = repository.list_model_usage("workspace-a", runs[0]["id"])
    assert receipts[0]["usage_status"] == "UNKNOWN"
    assert repository.get_run("workspace-a", runs[0]["id"])["steps"][0]["status"] == "failed"


@pytest.mark.parametrize("invalid", [
    {"role": "assistant", "content": "\ud800"},
    {"role": "assistant", "content": "ok", "tool_calls": [{"function": 42}]},
])
def test_malformed_nested_or_unicode_response_closes_receipt(tmp_path, monkeypatch, invalid) -> None:
    repository = _repository(tmp_path)

    async def fake_completion(_messages, _model=None, _tools=None, _provider=None):
        return invalid

    monkeypatch.setattr("app.agent.completion", fake_completion)
    service = GatewayService(repository_provider=lambda: repository)
    with pytest.raises(UpstreamResponseError):
        asyncio.run(service.chat(
            ChatCommand(prompt="hello"),
            RequestContext(request_id="invalid-output", workspace_id="workspace-a"),
        ))
    run = repository.list_runs("workspace-a")[0]
    receipt = repository.list_model_usage("workspace-a", run["id"])[0]
    assert receipt["usage_status"] == "UNKNOWN"
    assert receipt["termination"] == "FAILED_OR_INDETERMINATE"
    assert repository.get_run("workspace-a", run["id"])["steps"][0]["status"] == "failed"


def test_usage_summary_handles_large_integer_totals_and_invalid_partial_counts(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = _run(repository)
    for index in range(2):
        begin = _begin_values(run, sequence=index + 1)
        repository.begin_model_usage(begin)
        finalized = _finish_values(final_hash=f"hash-{index}")
        finalized["prompt_tokens"] = 2**63 - 1
        repository.finish_model_usage("workspace-a", begin["attempt_id"], finalized)
    invalid = _begin_values(run, sequence=3)
    repository.begin_model_usage(invalid)
    repository.finish_model_usage(
        "workspace-a", invalid["attempt_id"], _finish_values(usage_status="INVALID", final_hash="invalid")
    )
    summary = repository.usage_summary("workspace-a")
    assert summary["prompt_tokens"] == 2 * (2**63 - 1)
    assert summary["invalid_requests"] == 1
    assert summary["receipt_attempts"] == 3


def test_http_api_key_identity_and_run_scoped_read_projection(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(main.settings, "database_url", "")
    monkeypatch.setattr(main.settings, "host", "127.0.0.1")
    monkeypatch.setattr(main.settings, "scheduler_enabled", False)
    monkeypatch.setattr(main.settings, "tenants_json", json.dumps([
        {"id": "workspace-a", "name": "Workspace A", "api_key": "test-api-key-which-is-not-a-user-session"}
    ]))
    monkeypatch.setattr(main.settings, "api_key", "")
    monkeypatch.setattr(main.settings, "auth_secret", "test-auth-secret-with-more-than-32-characters")
    repository = SQLiteRepository(tmp_path / "aigc-lite.db")
    repository.init()
    own_run = _run(repository)
    other_session = repository.create_session("workspace-b", "other")
    other_run = repository.create_run("workspace-b", other_session["id"], "other-request", None, "model")
    observed = []

    def capture(context, _limit):
        observed.append(context)
        return []

    monkeypatch.setattr(main.memory_service, "list_runs", capture)
    headers = {"Authorization": "Bearer test-api-key-which-is-not-a-user-session"}
    with TestClient(main.app) as client:
        assert client.get("/api/runs", headers=headers).status_code == 200
        assert client.get(f"/api/runs/{other_run['id']}/model-usage", headers=headers).status_code == 404
        assert client.get(f"/api/runs/{own_run['id']}/model-usage", headers=headers).json() == []
        assert client.get(
            f"/api/runs/{other_run['id']}/ledger-projection", headers=headers
        ).status_code == 404
        projected = client.get(
            f"/api/runs/{own_run['id']}/ledger-projection", headers=headers
        )
        assert projected.status_code == 200
        assert projected.json()["run_id"] == own_run["id"]
        assert projected.json()["signals"]["verified_evidence"] == []
        monkeypatch.setattr(ledger_snapshot_module, "MAX_LEDGER_SNAPSHOT_BYTES", 32)
        too_large = client.get(
            f"/api/runs/{own_run['id']}/ledger-projection", headers=headers
        )
        assert too_large.status_code == 413
        assert too_large.json()["error"]["code"] == "ledger_projection_too_large"
    assert observed[0].principal_id is None
    assert observed[0].api_key_id.startswith("api-key:v1:")
    assert "test-api-key" not in observed[0].api_key_id
