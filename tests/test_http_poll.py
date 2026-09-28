import asyncio
import json

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from app.config import settings
from app.core.contracts import RequestContext
from app.core.errors import InvalidScheduleError
from app.repository import SQLiteRepository
from app.services.http_poll import HTTPPollService


def _context() -> RequestContext:
    return RequestContext(
        request_id="schedule:http-poll:1",
        workspace_id="workspace-a",
        principal_id="system:scheduler",
    )


def test_http_poll_is_allowlisted_and_records_only_safe_projection(
    tmp_path, monkeypatch
) -> None:
    captured: dict = {}

    async def handler(request: Request) -> Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        return Response(204, content=b"private response body")

    transport = MockTransport(handler)

    def client_factory(**kwargs):
        captured["client"] = kwargs
        return AsyncClient(transport=transport, **kwargs)

    monkeypatch.setattr(
        settings, "http_poll_allowed_hosts", "status.example.test"
    )
    repository = SQLiteRepository(tmp_path / "http-poll.db")
    repository.init()
    service = HTTPPollService(
        repository_provider=lambda: repository,
        client_factory=client_factory,
    )
    result = asyncio.run(
        service.poll(
            _context(),
            {
                "url": "https://status.example.test/health?token=secret-value",
                "expected_status": 204,
                "timeout_seconds": 2,
            },
        )
    )

    assert result["matched"]
    assert result["status_code"] == 204
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/health?token=secret-value")
    assert captured["client"]["follow_redirects"] is False
    assert captured["client"]["trust_env"] is False
    detail = repository.get_run("workspace-a", result["run_id"])
    assert detail is not None
    assert detail["status"] == "succeeded"
    step = detail["steps"][0]
    assert step["name"] == "http.poll"
    assert "secret-value" not in step["input_content"]
    assert json.loads(step["input_content"])["url"].endswith("?<redacted>")
    assert "private response body" not in step["output_content"]
    assert step["metadata"]["transport"] == "scheduler"


def test_http_poll_rejects_unlisted_hosts_and_marks_status_mismatch(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        settings, "http_poll_allowed_hosts", "status.example.test:8443"
    )
    repository = SQLiteRepository(tmp_path / "http-poll-failure.db")
    repository.init()

    with pytest.raises(InvalidScheduleError) as raised:
        HTTPPollService(
            repository_provider=lambda: repository
        ).validate_payload({"url": "http://127.0.0.1/internal"})
    assert raised.value.metadata == {"field": "payload.url"}

    transport = MockTransport(lambda _request: Response(503))
    service = HTTPPollService(
        repository_provider=lambda: repository,
        client_factory=lambda **kwargs: AsyncClient(
            transport=transport, **kwargs
        ),
    )
    result = asyncio.run(
        service.poll(
            _context(),
            {
                "url": "https://status.example.test:8443/health",
                "expected_status": 200,
            },
        )
    )

    assert not result["matched"]
    assert result["status_code"] == 503
    detail = repository.get_run("workspace-a", result["run_id"])
    assert detail is not None
    assert detail["status"] == "failed"
    assert detail["error_code"] == "upstream_invalid_response"


def test_http_poll_cancellation_is_propagated_and_recorded(
    tmp_path, monkeypatch
) -> None:
    started = asyncio.Event()

    async def handler(_request: Request) -> Response:
        started.set()
        await asyncio.Future()
        raise AssertionError  # pragma: no cover

    monkeypatch.setattr(
        settings, "http_poll_allowed_hosts", "status.example.test"
    )
    repository = SQLiteRepository(tmp_path / "http-poll-cancel.db")
    repository.init()
    transport = MockTransport(handler)
    service = HTTPPollService(
        repository_provider=lambda: repository,
        client_factory=lambda **kwargs: AsyncClient(
            transport=transport, **kwargs
        ),
    )

    async def scenario() -> None:
        task = asyncio.create_task(
            service.poll(
                _context(), {"url": "https://status.example.test/health"}
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    runs = repository.list_runs("workspace-a")
    assert len(runs) == 1
    detail = repository.get_run("workspace-a", runs[0]["id"])
    assert detail is not None
    assert detail["status"] == "cancelled"
    assert detail["error_code"] == "agent_cancelled"
    assert detail["steps"][0]["status"] == "cancelled"
