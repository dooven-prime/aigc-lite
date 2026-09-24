"""Execution backends for local Python tools."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import multiprocessing
from collections.abc import Callable
from typing import Any

from ...core.contracts import ToolExecutionMode, ToolProviderResult
from ...core.errors import ErrorCode
from ...ports.tools import ToolExecutionBackend
from ...tools import process_callable_reference


def _metadata(mode: ToolExecutionMode) -> dict[str, str]:
    return {
        "execution_mode": mode.value,
        "cancellation_mode": mode.cancellation_mode,
    }


def _success(value: Any, mode: ToolExecutionMode) -> ToolProviderResult:
    return ToolProviderResult(
        content=json.dumps(value, ensure_ascii=False, default=str),
        metadata=_metadata(mode),
    )


def _failure(mode: ToolExecutionMode) -> ToolProviderResult:
    return ToolProviderResult(
        content=json.dumps({"error": ErrorCode.TOOL_EXECUTION_FAILED.value}),
        failed=True,
        metadata={
            **_metadata(mode),
            "error_code": ErrorCode.TOOL_EXECUTION_FAILED.value,
        },
    )


class AsyncToolExecutionBackend:
    mode = ToolExecutionMode.ASYNC

    async def execute(
        self, function: Callable[..., Any], arguments: dict[str, Any]
    ) -> ToolProviderResult:
        try:
            result = function(**arguments)
            if not inspect.isawaitable(result):
                return _failure(self.mode)
            return _success(await result, self.mode)
        except Exception:  # noqa: BLE001 - tool details must not cross the boundary
            return _failure(self.mode)


class ThreadToolExecutionBackend:
    mode = ToolExecutionMode.THREAD

    async def execute(
        self, function: Callable[..., Any], arguments: dict[str, Any]
    ) -> ToolProviderResult:
        try:
            result = await asyncio.to_thread(function, **arguments)
            return _success(result, self.mode)
        except Exception:  # noqa: BLE001 - tool details must not cross the boundary
            return _failure(self.mode)


def _resolve_callable(module_name: str, qualname: str) -> Callable[..., Any]:
    value: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        value = getattr(value, part)
    if not callable(value):
        raise TypeError
    return value


def _process_worker(
    sender: Any,
    module_name: str,
    qualname: str,
    arguments: dict[str, Any],
) -> None:
    try:
        function = _resolve_callable(module_name, qualname)
        result = function(**arguments)
        if inspect.isawaitable(result):
            raise TypeError
        payload = {
            "ok": True,
            "content": json.dumps(result, ensure_ascii=False, default=str),
        }
    except BaseException:  # noqa: BLE001 - child diagnostics never cross the boundary
        payload = {
            "ok": False,
            "content": json.dumps(
                {"error": ErrorCode.TOOL_EXECUTION_FAILED.value}
            ),
        }
    try:
        sender.send(payload)
    finally:
        sender.close()


class ProcessToolExecutionBackend:
    """Run an importable function in a disposable spawned worker process."""

    mode = ToolExecutionMode.PROCESS

    def __init__(self) -> None:
        self._context = multiprocessing.get_context("spawn")

    async def execute(
        self, function: Callable[..., Any], arguments: dict[str, Any]
    ) -> ToolProviderResult:
        try:
            module_name, qualname = process_callable_reference(function)
        except ValueError:
            return _failure(self.mode)

        receiver, sender = self._context.Pipe(duplex=False)
        process = self._context.Process(
            target=_process_worker,
            args=(sender, module_name, qualname, arguments),
            name=f"aigc-lite-tool-{getattr(function, '__name__', 'worker')}",
            daemon=True,
        )
        started = False
        completed = False
        try:
            try:
                process.start()
                started = True
            except Exception:  # noqa: BLE001 - expose only a stable tool failure
                return _failure(self.mode)
            finally:
                sender.close()

            while True:
                if receiver.poll():
                    payload = receiver.recv()
                    completed = True
                    if payload.get("ok") is True:
                        return ToolProviderResult(
                            content=payload["content"],
                            metadata=_metadata(self.mode),
                        )
                    return _failure(self.mode)
                if not process.is_alive():
                    if receiver.poll(0.05):
                        continue
                    return _failure(self.mode)
                await asyncio.sleep(0.01)
        finally:
            receiver.close()
            if started:
                if completed:
                    process.join(0.2)
                if process.is_alive():
                    process.terminate()
                    process.join(0.5)
                if process.is_alive():
                    process.kill()
                    process.join(0.5)
                if not process.is_alive():
                    process.close()
            else:
                try:
                    process.close()
                except ValueError:
                    pass


class ToolExecutionBackends:
    """Resolve execution modes without leaking backend choices into the Catalog."""

    def __init__(
        self, backends: list[ToolExecutionBackend] | None = None
    ) -> None:
        values = (
            [
                AsyncToolExecutionBackend(),
                ThreadToolExecutionBackend(),
                ProcessToolExecutionBackend(),
            ]
            if backends is None
            else backends
        )
        self._backends = {backend.mode: backend for backend in values}

    async def execute(
        self,
        mode: ToolExecutionMode,
        function: Callable[..., Any],
        arguments: dict[str, Any],
    ) -> ToolProviderResult:
        backend = self._backends.get(mode)
        if backend is None:
            return _failure(mode)
        return await backend.execute(function, arguments)


default_execution_backends = ToolExecutionBackends()
