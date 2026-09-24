import asyncio

from app import agent
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
