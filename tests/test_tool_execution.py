import asyncio
import json
import multiprocessing
import os
import threading
import time

import pytest

from app.adapters.tools.execution import ToolExecutionBackends
from app.adapters.tools.local import LocalToolProvider
from app.core.contracts import (
    RequestContext,
    ToolExecutionMode,
    ToolProviderResult,
)
from app.services.tool_catalog import ToolCatalog
from app.tools import tool

_ASYNC_CANCELLED = threading.Event()
_THREAD_COMPLETED = threading.Event()


@tool("execution_async_wait", timeout_seconds=0.02)
async def execution_async_wait() -> str:
    """Wait long enough for cooperative cancellation."""
    try:
        await asyncio.sleep(30)
    except asyncio.CancelledError:
        _ASYNC_CANCELLED.set()
        raise
    return "unreachable"


@tool("execution_thread_wait", timeout_seconds=0.02)
def execution_thread_wait() -> str:
    """Finish after the Agent has stopped waiting for this thread."""
    time.sleep(0.15)
    _THREAD_COMPLETED.set()
    return "finished"


@tool("execution_process_identity", execution="process")
def execution_process_identity() -> dict[str, int]:
    """Return the disposable worker process identity."""
    return {"pid": os.getpid()}


@tool("execution_process_wait", execution="process", timeout_seconds=0.5)
def execution_process_wait() -> str:
    """Wait until the process backend terminates this worker."""
    time.sleep(30)
    return "unreachable"


async def _open_session():
    return await ToolCatalog([LocalToolProvider()]).open(
        RequestContext(request_id="execution-test", workspace_id="workspace-a")
    )


def _session():
    return asyncio.run(_open_session())


def test_local_tool_specs_expose_execution_and_cancellation_modes() -> None:
    specs = {spec.name: spec for spec in _session().specs}
    assert specs["execution_async_wait"].execution_mode is ToolExecutionMode.ASYNC
    assert specs["execution_thread_wait"].execution_mode is ToolExecutionMode.THREAD
    assert specs["execution_process_wait"].execution_mode is ToolExecutionMode.PROCESS
    assert ToolExecutionMode.ASYNC.cancellation_mode == "cooperative"
    assert ToolExecutionMode.THREAD.cancellation_mode == "soft"
    assert ToolExecutionMode.PROCESS.cancellation_mode == "hard"


def test_process_backend_rejects_non_importable_nested_tools() -> None:
    def nested_tool() -> str:
        return "unsafe"

    with pytest.raises(ValueError, match="top-level"):
        tool("nested_process_tool", execution="process")(nested_tool)


def test_local_provider_uses_injected_execution_backend() -> None:
    class StubThreadBackend:
        mode = ToolExecutionMode.THREAD

        async def execute(self, function, arguments):
            return ToolProviderResult(
                content=json.dumps(
                    {"function": function.__name__, "arguments": arguments}
                ),
                metadata={"execution_mode": "stub"},
            )

    provider = LocalToolProvider(ToolExecutionBackends([StubThreadBackend()]))
    result = asyncio.run(provider.call_tool("execution_thread_wait", {}))
    assert json.loads(result.content) == {
        "function": "execution_thread_wait",
        "arguments": {},
    }
    assert result.metadata["execution_mode"] == "stub"


def test_async_backend_propagates_timeout_cancellation() -> None:
    _ASYNC_CANCELLED.clear()
    result = asyncio.run(_session().invoke("execution_async_wait", "{}"))
    assert result.failed
    assert json.loads(result.content) == {"error": "tool_timeout"}
    assert result.metadata["execution_mode"] == "async"
    assert result.metadata["cancellation_mode"] == "cooperative"
    assert _ASYNC_CANCELLED.is_set()


def test_thread_backend_timeout_is_soft_and_thread_finishes() -> None:
    _THREAD_COMPLETED.clear()

    async def invoke_and_observe():
        session = await _open_session()
        result = await session.invoke("execution_thread_wait", "{}")
        completed_at_timeout = _THREAD_COMPLETED.is_set()
        return result, completed_at_timeout

    result, completed_at_timeout = asyncio.run(invoke_and_observe())
    assert result.failed
    assert json.loads(result.content) == {"error": "tool_timeout"}
    assert result.metadata["execution_mode"] == "thread"
    assert result.metadata["cancellation_mode"] == "soft"
    assert not completed_at_timeout
    assert _THREAD_COMPLETED.wait(1)


def test_process_backend_runs_out_of_process_and_hard_stops_on_timeout() -> None:
    session = _session()
    identity = asyncio.run(session.invoke("execution_process_identity", "{}"))
    assert not identity.failed
    assert json.loads(identity.content)["pid"] != os.getpid()
    assert identity.metadata["execution_mode"] == "process"

    started = time.monotonic()
    limited = asyncio.run(session.invoke("execution_process_wait", "{}"))
    elapsed = time.monotonic() - started
    assert limited.failed
    assert json.loads(limited.content) == {"error": "tool_timeout"}
    assert limited.metadata["cancellation_mode"] == "hard"
    assert elapsed < 5
    assert not any(
        child.name.startswith("aigc-lite-tool-")
        for child in multiprocessing.active_children()
    )
