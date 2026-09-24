"""Explicit tool registry for local extensions and MCP adapters."""

from __future__ import annotations

import inspect
import json
import types
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from .core.contracts import ToolExecutionMode, ToolRisk
from .core.errors import ErrorCode

Tool = Callable[..., Any] | Callable[..., Awaitable[Any]]
TOOLS: dict[str, Tool] = {}
TOOL_POLICIES: dict[str, dict[str, Any]] = {}


def process_callable_reference(function: Tool) -> tuple[str, str]:
    """Return a spawn-safe import reference or reject an unsafe callable."""
    module_name = getattr(function, "__module__", "")
    qualname = getattr(function, "__qualname__", "")
    if (
        not module_name
        or module_name == "__main__"
        or not qualname
        or "<locals>" in qualname
        or inspect.ismethod(function)
        or inspect.iscoroutinefunction(function)
    ):
        raise ValueError(
            "Process tools must be synchronous top-level functions in importable modules"
        )
    return module_name, qualname


def tool(
    name: str | None = None,
    *,
    risk: ToolRisk | str = ToolRisk.LOW,
    required_scopes: tuple[str, ...] = (),
    execution: ToolExecutionMode | str | None = None,
    timeout_seconds: float | None = None,
):
    def register(function: Tool) -> Tool:
        execution_mode = (
            ToolExecutionMode.ASYNC
            if execution is None and inspect.iscoroutinefunction(function)
            else ToolExecutionMode.THREAD
            if execution is None
            else ToolExecutionMode(execution)
        )
        is_async = inspect.iscoroutinefunction(function)
        if (execution_mode is ToolExecutionMode.ASYNC) != is_async:
            raise ValueError(
                "Async functions and async execution mode must be used together"
            )
        if execution_mode is ToolExecutionMode.PROCESS:
            process_callable_reference(function)
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("Tool timeout must be positive")
        tool_name = name or function.__name__
        TOOLS[tool_name] = function
        TOOL_POLICIES[tool_name] = {
            "risk": ToolRisk(risk),
            "required_scopes": frozenset(required_scopes),
            "execution_mode": execution_mode,
            "timeout_seconds": timeout_seconds,
        }
        return function

    return register


@tool("workspace_status")
def workspace_status() -> dict[str, str]:
    """Return the current aigc-lite service status."""
    return {"status": "ok", "service": "aigc-lite"}


def _json_schema(annotation: Any) -> dict[str, Any]:
    if annotation in {inspect.Signature.empty, Any}:
        return {}
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin in {Union, types.UnionType}:
        return {"anyOf": [_json_schema(item) for item in arguments]}
    if origin is Literal:
        values = list(arguments)
        schema = _json_schema(type(values[0])) if values else {}
        return {**schema, "enum": values}
    if origin in {list, tuple, set}:
        return {
            "type": "array",
            "items": _json_schema(arguments[0]) if arguments else {},
        }
    if origin is dict:
        return {
            "type": "object",
            "additionalProperties": _json_schema(arguments[1]) if len(arguments) > 1 else True,
        }
    mapping = {str: "string", int: "integer", float: "number", bool: "boolean"}
    return {"type": mapping[annotation]} if annotation in mapping else {}


def _function_schema(name: str, function: Tool) -> dict[str, Any]:
    signature = inspect.signature(function)
    try:
        hints = get_type_hints(function)
    except (NameError, TypeError):
        hints = {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    additional_properties = False
    for parameter_name, parameter in signature.parameters.items():
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            additional_properties = True
            continue
        schema = _json_schema(hints.get(parameter_name, parameter.annotation))
        if parameter.default is inspect.Parameter.empty:
            required.append(parameter_name)
        else:
            try:
                json.dumps(parameter.default)
                schema["default"] = parameter.default
            except (TypeError, ValueError):
                pass
        properties[parameter_name] = schema
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": additional_properties,
    }
    if required:
        parameters["required"] = required
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": inspect.getdoc(function) or name,
            "parameters": parameters,
        },
    }


def schemas() -> list[dict[str, Any]]:
    """Derive fresh model-facing metadata from each function signature and docstring."""
    return [_function_schema(name, function) for name, function in TOOLS.items()]


def registration(name: str) -> tuple[Tool, dict[str, Any]] | None:
    function = TOOLS.get(name)
    if function is None:
        return None
    return function, TOOL_POLICIES.get(
        name,
        {
            "risk": ToolRisk.LOW,
            "required_scopes": frozenset(),
            "execution_mode": (
                ToolExecutionMode.ASYNC
                if inspect.iscoroutinefunction(function)
                else ToolExecutionMode.THREAD
            ),
            "timeout_seconds": None,
        },
    )


async def invoke(name: str, arguments: str) -> str:
    registered = registration(name)
    if registered is None:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        kwargs = json.loads(arguments or "{}")
        if not isinstance(kwargs, dict):
            raise TypeError
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"error": ErrorCode.INVALID_TOOL_ARGUMENTS.value})
    try:
        function, policy = registered
        from .adapters.tools.execution import default_execution_backends

        result = await default_execution_backends.execute(
            policy["execution_mode"], function, kwargs
        )
        return result.content
    except Exception:  # noqa: BLE001 - tool details must not cross the boundary
        return json.dumps({"error": ErrorCode.TOOL_EXECUTION_FAILED.value})
