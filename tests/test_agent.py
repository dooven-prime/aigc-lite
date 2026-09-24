import asyncio

import pytest

from app import agent
from app.core.contracts import ToolInvocationResult
from app.core.errors import AgentModelTurnLimitError, AgentToolCallLimitError
from app.tools import tool


@tool("agent_add")
def agent_add(left: int, right: int) -> int:
    """Add two numbers for agent tests."""
    return left + right


def test_agent_executes_tool_and_returns_follow_up(monkeypatch) -> None:
    async def run() -> tuple[str, list, list]:
        calls = []
        steps = []

        async def fake_completion(messages, model=None, tools=None, provider=None):
            calls.append(list(messages))
            if len(calls) == 1:
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call-1",
                        "function": {"name": "agent_add", "arguments": '{"left": 2, "right": 3}'},
                    }],
                }
            return {"role": "assistant", "content": "5", "tool_calls": []}

        monkeypatch.setattr(agent, "completion", fake_completion)
        result = await agent.run_agent("calculate", max_steps=2, step_callback=steps.append)
        return result, calls, steps

    result, calls, steps = asyncio.run(run())
    assert result == "5"
    assert calls[1][-1]["role"] == "tool"
    assert calls[1][-1]["content"] == "5"
    assert [step.kind.value for step in steps] == ["model", "tool", "model"]
    assert steps[1].name == "agent_add"
    assert steps[1].input_content == '{"left": 2, "right": 3}'
    assert steps[1].output_content == "5"


def test_agent_stops_before_exceeding_tool_call_budget(monkeypatch) -> None:
    async def run() -> list[str]:
        invoked: list[str] = []

        async def fake_completion(messages, model=None, tools=None, provider=None):
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": "first", "arguments": "{}"},
                    },
                    {
                        "id": "call-2",
                        "function": {"name": "second", "arguments": "{}"},
                    },
                ],
            }

        async def invoke(name: str, _arguments: str) -> ToolInvocationResult:
            invoked.append(name)
            return ToolInvocationResult(
                content="ok",
                failed=False,
                ledger_input="{}",
                ledger_output="ok",
            )

        monkeypatch.setattr(agent, "completion", fake_completion)
        with pytest.raises(AgentToolCallLimitError) as captured:
            await agent.run_agent(
                "use tools",
                max_steps=2,
                max_tool_calls=1,
                available_tools=[],
                tool_invoker=invoke,
            )
        assert captured.value.metadata == {"budget": "tool_calls", "limit": 1}
        return invoked

    assert asyncio.run(run()) == ["first"]


def test_agent_projects_model_turn_exhaustion_as_limit(monkeypatch) -> None:
    async def run() -> None:
        async def fake_completion(messages, model=None, tools=None, provider=None):
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": "agent_add", "arguments": "{}"},
                    }
                ],
            }

        async def invoke(_name: str, _arguments: str) -> ToolInvocationResult:
            return ToolInvocationResult(
                content="ok",
                failed=False,
                ledger_input="{}",
                ledger_output="ok",
            )

        monkeypatch.setattr(agent, "completion", fake_completion)
        with pytest.raises(AgentModelTurnLimitError) as captured:
            await agent.run_agent(
                "keep going",
                max_steps=1,
                max_tool_calls=1,
                available_tools=[],
                tool_invoker=invoke,
            )
        assert captured.value.metadata == {"budget": "model_turns", "limit": 1}

    asyncio.run(run())
