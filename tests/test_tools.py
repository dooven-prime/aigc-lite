import asyncio

from app.tools import invoke, registration, schemas, tool


@tool("add")
def add(left: int, right: int) -> int:
    """Add two integers."""
    return left + right


@tool(
    "typed_search",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
def typed_search(query: str, limit: int = 5, exact: bool = False) -> list[str]:
    """Search typed test records."""
    return [query] if exact or limit else []


def test_invoke_tool() -> None:
    assert asyncio.run(invoke("add", '{"left": 2, "right": 3}')) == "5"


def test_unknown_tool() -> None:
    assert "Unknown tool" in asyncio.run(invoke("missing", "{}"))


def test_tool_schema_tracks_signature_and_docstring() -> None:
    schema = next(item for item in schemas() if item["function"]["name"] == "typed_search")
    function = schema["function"]
    assert function["description"] == "Search typed test records."
    assert function["parameters"] == {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 5},
            "exact": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
        "required": ["query"],
    }

    hints = registration("typed_search")[1]["hints"]
    assert hints.read_only is True
    assert hints.destructive is False
    assert hints.idempotent is True
    assert hints.open_world is False
