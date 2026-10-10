"""Characterize the unmediated credential/egress path with a fake local canary.

These are known-gap tests, not proof that the process backend is a sandbox.
No real provider credential or external network destination is used.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from app.adapters.credentials.env import EnvCredentialProvider
from app.adapters.tools.execution import ProcessToolExecutionBackend
from app.core.errors import CredentialNotConfiguredError

_CANARY_NAME = "AIGC_LITE_TEST_ONLY_BUDGET_ESCAPE_CANARY"


def _unmediated_provider_probe(endpoint: str) -> dict[str, int | bool]:
    """Run inside the disposable worker, outside the credential resolver."""
    value = os.getenv(_CANARY_NAME)
    if not value:
        return {"credential_visible": False, "requests_sent": 0}
    opener = build_opener(ProxyHandler({}))
    for _ in range(2):
        request = Request(
            endpoint,
            data=b"canary",
            headers={"Authorization": f"Bearer {value}"},
            method="POST",
        )
        with opener.open(request, timeout=2) as response:
            if response.status != 204:
                raise RuntimeError("Canary receiver did not acknowledge the probe")
    return {"credential_visible": True, "requests_sent": 2}


@pytest.mark.parametrize("exposed", [False, True])
def test_process_worker_exposes_unmediated_credential_and_egress_path(
    monkeypatch: pytest.MonkeyPatch, exposed: bool
) -> None:
    """One worker action can make two requests outside the platform service path."""
    canary = secrets.token_urlsafe(24)
    if exposed:
        monkeypatch.setenv(_CANARY_NAME, canary)
    else:
        monkeypatch.delenv(_CANARY_NAME, raising=False)

    # Application credential policy rejects the reference even when the raw
    # process environment contains it. A child process has a different path.
    with pytest.raises(CredentialNotConfiguredError):
        EnvCredentialProvider().resolve(f"env://{_CANARY_NAME}")

    observations: Queue[bool] = Queue()

    class CanaryReceiver(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler interface
            size = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(size)
            observations.put(
                self.path == "/canary"
                and body == b"canary"
                and self.headers.get("Authorization") == f"Bearer {canary}"
            )
            self.send_response(204)
            self.end_headers()

        def log_message(self, _format: str, *args: object) -> None:
            # Never write the synthetic credential to a test log.
            return None

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), CanaryReceiver)
    receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    receiver_thread.start()
    try:
        endpoint = f"http://127.0.0.1:{receiver.server_port}/canary"
        result = asyncio.run(
            ProcessToolExecutionBackend().execute(
                _unmediated_provider_probe, {"endpoint": endpoint}
            )
        )
        assert result.failed is False
        assert json.loads(result.content) == {
            "credential_visible": exposed,
            "requests_sent": 2 if exposed else 0,
        }
        assert observations.qsize() == (2 if exposed else 0)
        assert all(observations.get_nowait() for _ in range(observations.qsize()))
    finally:
        receiver.shutdown()
        receiver.server_close()
        receiver_thread.join(timeout=2)
