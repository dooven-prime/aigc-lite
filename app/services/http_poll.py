"""Allowlisted HTTP health polling with safe execution-ledger projection."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from math import isfinite
from time import monotonic
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..config import settings
from ..core.contracts import RequestContext, RunStatus, StepKind, StepStatus
from ..core.errors import ErrorCode, InvalidScheduleError
from ..database import get_repository
from ..repository import Repository

RepositoryProvider = Callable[[], Repository]
ClientFactory = Callable[..., httpx.AsyncClient]


def _normalized_host(value: str) -> str:
    return value.strip().casefold().encode("idna").decode("ascii")


class HTTPPollService:
    """Poll one explicitly allowed endpoint without retaining response content."""

    def __init__(
        self,
        *,
        repository_provider: RepositoryProvider = get_repository,
        client_factory: ClientFactory = httpx.AsyncClient,
    ) -> None:
        self._repository_provider = repository_provider
        self._client_factory = client_factory

    def validate_payload(
        self, payload: dict[str, Any]
    ) -> tuple[str, int, float]:
        unsupported = sorted(
            set(payload) - {"url", "expected_status", "timeout_seconds"}
        )
        if unsupported:
            raise InvalidScheduleError(
                "payload", "http.poll payload contains unsupported fields"
            )
        url = payload.get("url")
        if not isinstance(url, str) or not url.strip() or len(url) > 2000:
            raise InvalidScheduleError(
                "payload.url", "http.poll requires a valid URL"
            )
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.fragment)
        ):
            raise InvalidScheduleError(
                "payload.url",
                "http.poll URL must be HTTP(S) without credentials or fragments",
            )
        try:
            host = _normalized_host(parsed.hostname)
            port = parsed.port
        except (UnicodeError, ValueError) as exc:
            raise InvalidScheduleError(
                "payload.url", "http.poll URL host or port is invalid"
            ) from exc
        authority = f"{host}:{port}" if port is not None else host
        allowed = {
            _normalized_host(item)
            for item in settings.http_poll_allowed_hosts.split(",")
            if item.strip()
        }
        if host not in allowed and authority not in allowed:
            raise InvalidScheduleError(
                "payload.url", "http.poll URL host is not allowlisted"
            )

        expected_status = payload.get("expected_status", 200)
        if (
            not isinstance(expected_status, int)
            or isinstance(expected_status, bool)
            or not 100 <= expected_status <= 599
        ):
            raise InvalidScheduleError(
                "payload.expected_status",
                "http.poll expected_status must be between 100 and 599",
            )
        timeout = payload.get("timeout_seconds", 10.0)
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not isfinite(float(timeout))
            or not 0.1 <= float(timeout) <= 60
        ):
            raise InvalidScheduleError(
                "payload.timeout_seconds",
                "http.poll timeout_seconds must be between 0.1 and 60",
            )
        return url, expected_status, float(timeout)

    async def poll(
        self, context: RequestContext, payload: dict[str, Any]
    ) -> dict[str, Any]:
        url, expected_status, timeout = self.validate_payload(payload)
        repository = self._repository_provider()
        run = repository.create_run(
            context.workspace_id,
            "",
            context.request_id,
            None,
            "http.poll",
        )
        started = monotonic()
        safe_input = json.dumps(
            {
                "url": self._ledger_url(url),
                "expected_status": expected_status,
                "timeout_seconds": timeout,
            },
            ensure_ascii=False,
        )
        try:
            async with self._client_factory(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream("GET", url) as response:
                    status_code = response.status_code
            latency_ms = round((monotonic() - started) * 1000)
            matched = status_code == expected_status
            output = {
                "status_code": status_code,
                "expected_status": expected_status,
                "matched": matched,
                "latency_ms": latency_ms,
            }
            error_code = (
                None if matched else ErrorCode.UPSTREAM_INVALID_RESPONSE.value
            )
        except asyncio.CancelledError:
            latency_ms = round((monotonic() - started) * 1000)
            repository.append_run_step(
                context.workspace_id,
                run["id"],
                1,
                StepKind.TOOL.value,
                "http.poll",
                StepStatus.CANCELLED.value,
                safe_input,
                "",
                {
                    "transport": "scheduler",
                    "cancelled": True,
                    "latency_ms": latency_ms,
                    "error_code": ErrorCode.AGENT_CANCELLED.value,
                },
            )
            repository.finish_run(
                context.workspace_id,
                run["id"],
                RunStatus.CANCELLED.value,
                ErrorCode.AGENT_CANCELLED.value,
            )
            raise
        except httpx.HTTPError:
            latency_ms = round((monotonic() - started) * 1000)
            matched = False
            output = {
                "error": ErrorCode.UPSTREAM_REQUEST_FAILED.value,
                "matched": False,
                "latency_ms": latency_ms,
            }
            error_code = ErrorCode.UPSTREAM_REQUEST_FAILED.value

        repository.append_run_step(
            context.workspace_id,
            run["id"],
            1,
            StepKind.TOOL.value,
            "http.poll",
            StepStatus.SUCCEEDED.value if matched else StepStatus.FAILED.value,
            safe_input,
            json.dumps(output, ensure_ascii=False),
            {
                "transport": "scheduler",
                "expected_status": expected_status,
                "latency_ms": latency_ms,
                **({"error_code": error_code} if error_code else {}),
            },
        )
        repository.finish_run(
            context.workspace_id,
            run["id"],
            RunStatus.SUCCEEDED.value if matched else RunStatus.FAILED.value,
            error_code,
        )
        return {"run_id": run["id"], **output}

    @staticmethod
    def _ledger_url(url: str) -> str:
        parsed = urlsplit(url)
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                "<redacted>" if parsed.query else "",
                "",
            )
        )
