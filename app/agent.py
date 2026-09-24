"""Bounded agent runtime with a predictable tool execution loop."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from .config import settings
from .core.contracts import AgentStepRecord, StepKind, StepStatus, ToolInvocationResult
from .core.errors import AgentModelTurnLimitError, AgentToolCallLimitError
from .database import search_documents
from .providers import completion
from .redaction import safe_json_text
from .tools import invoke, schemas

StepObserver = Callable[[AgentStepRecord], None]
ToolInvoker = Callable[[str, str], Awaitable[ToolInvocationResult]]
def _safe_json_text(value: str, *, hide_errors: bool = False) -> str:
    return safe_json_text(value, hide_errors=hide_errors)


def _message_input(messages: list[dict]) -> str:
    if not messages:
        return ""
    message = messages[-1]
    if "_ledger_content" in message:
        return message["_ledger_content"]
    content = message.get("content", "")
    if message.get("role") == "tool" and isinstance(content, str):
        return _safe_json_text(content, hide_errors=True)
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def build_messages(
    prompt: str,
    system: str,
    history: list[dict] | None = None,
    tenant_id: str = "default",
) -> list[dict]:
    """Build model-visible messages, including relevant tenant documents."""
    snippets = search_documents(tenant_id, prompt, limit=3)
    if snippets:
        context = "\n\n".join(f"[{item['name']}]\n{item['content']}" for item in snippets)
        system = f"{system}\n\nRelevant workspace documents:\n{context}"
    messages: list[dict] = [{"role": "system", "content": system}]
    messages.extend({"role": item["role"], "content": item["content"]} for item in (history or []))
    messages.append({"role": "user", "content": prompt})
    return messages


async def run_agent(
    prompt: str,
    system: str = "You are a helpful assistant.",
    model: str | None = None,
    max_steps: int | None = None,
    history: list[dict] | None = None,
    tenant_id: str = "default",
    provider: dict | None = None,
    usage_callback=None,
    step_callback: StepObserver | None = None,
    available_tools: list[dict] | None = None,
    tool_invoker: ToolInvoker | None = None,
    max_tool_calls: int | None = None,
) -> str:
    """Run a bounded OpenAI-compatible tool loop."""
    max_steps = settings.max_agent_steps if max_steps is None else max_steps
    max_tool_calls = (
        settings.max_agent_tool_calls if max_tool_calls is None else max_tool_calls
    )
    if max_steps < 1 or max_steps > 32:
        raise ValueError("max_steps must be between 1 and 32")
    if max_tool_calls < 0 or max_tool_calls > 256:
        raise ValueError("max_tool_calls must be between 0 and 256")
    messages = build_messages(prompt, system, history, tenant_id)
    tool_schemas = schemas() if available_tools is None else available_tools
    if max_tool_calls == 0:
        tool_schemas = []
    tool_call_count = 0
    for step in range(max_steps):
        model_input = _message_input(messages)
        provider_messages = [
            {key: value for key, value in message.items() if not key.startswith("_")}
            for message in messages
        ]
        if provider is None:
            message = await completion(
                provider_messages, model, tool_schemas if step < max_steps - 1 else None
            )
        else:
            message = await completion(
                provider_messages,
                model,
                tool_schemas if step < max_steps - 1 else None,
                provider=provider,
            )
        usage = message.pop("_usage", {})
        if usage and usage_callback:
            usage_callback(usage)
        messages.append(message)
        tool_calls = message.get("tool_calls") or []
        if step_callback:
            output = message.get("content") or ""
            selected_model = model or (provider or {}).get("model") or settings.llm_model
            step_callback(
                AgentStepRecord(
                    kind=StepKind.MODEL,
                    name=selected_model,
                    status=StepStatus.SUCCEEDED,
                    input_content=model_input,
                    output_content=output,
                    metadata={
                        "iteration": step + 1,
                        "usage": usage,
                        "tool_calls": [
                            call.get("function", {}).get("name", "") for call in tool_calls
                        ],
                    },
                )
            )
        if not tool_calls:
            return message.get("content") or ""
        if step == max_steps - 1:
            # Tools were intentionally not offered on the final model turn. Do
            # not execute an unsolicited call after the model budget is spent.
            raise AgentModelTurnLimitError(max_steps)
        for call in tool_calls:
            if tool_call_count >= max_tool_calls:
                raise AgentToolCallLimitError(max_tool_calls)
            tool_call_count += 1
            function = call.get("function", {})
            name = function.get("name", "")
            arguments = function.get("arguments", "{}")
            if tool_invoker:
                invocation = await tool_invoker(name, arguments)
            else:
                result = await invoke(name, arguments)
                try:
                    failed = isinstance(json.loads(result), dict) and "error" in json.loads(
                        result
                    )
                except json.JSONDecodeError:
                    failed = True
                invocation = ToolInvocationResult(
                    content=result,
                    failed=failed,
                    ledger_input=_safe_json_text(arguments),
                    ledger_output=_safe_json_text(result, hide_errors=failed),
                )
            if step_callback:
                step_callback(
                    AgentStepRecord(
                        kind=StepKind.TOOL,
                        name=name,
                        status=(
                            StepStatus.FAILED
                            if invocation.failed
                            else StepStatus.SUCCEEDED
                        ),
                        input_content=invocation.ledger_input,
                        output_content=invocation.ledger_output,
                        metadata={
                            "tool_call_id": call.get("id", ""),
                            "tool_call_index": tool_call_count,
                            **invocation.metadata,
                        },
                    )
                )
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": invocation.content,
                "_ledger_content": invocation.ledger_output,
            })
    raise AgentModelTurnLimitError(max_steps)
