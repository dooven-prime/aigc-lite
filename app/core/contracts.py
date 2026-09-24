"""Transport-neutral application contracts.

These values deliberately contain no FastAPI, HTTP, database, or provider
objects. Transports translate their input into commands and project results
back into their own response format.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum


class RunStatus(StrEnum):
    """Lifecycle of one observable Agent execution attempt."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LIMIT_REACHED = "limit_reached"


class StepKind(StrEnum):
    """Stable categories used by the searchable execution ledger."""

    AGENT = "agent"
    MODEL = "model"
    TOOL = "tool"
    RETRIEVAL = "retrieval"


class StepStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolSource(StrEnum):
    LOCAL = "local"
    MCP = "mcp"


class ToolRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Authenticated identity and trace information for one operation."""

    request_id: str
    workspace_id: str
    principal_id: str | None = None
    api_key_id: str | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    tool_hops: int = 0


@dataclass(frozen=True, slots=True)
class ChatCommand:
    """Request to run the existing bounded chat/agent use case."""

    prompt: str
    system: str = "You are a helpful assistant."
    requested_model: str | None = None
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChatResult:
    """Transport-independent result of a completed chat operation."""

    content: str
    session_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class ChatStreamResult:
    """Identity plus the lazy content stream for one observable run."""

    session_id: str
    run_id: str
    chunks: AsyncIterator[str]


@dataclass(frozen=True, slots=True)
class AgentStepRecord:
    """One model, tool, or retrieval fact emitted by the Agent runtime."""

    kind: StepKind
    name: str
    status: StepStatus
    input_content: str = ""
    output_content: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Workspace-visible tool metadata independent of its provider transport."""

    name: str
    native_name: str
    description: str
    input_schema: dict
    source: ToolSource
    provider_id: str
    workspace_id: str | None = None
    risk: ToolRisk = ToolRisk.LOW
    required_scopes: frozenset[str] = field(default_factory=frozenset)
    enabled: bool = True
    timeout_seconds: float = 30.0

    def model_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolProviderResult:
    """Raw result returned by a local or remote provider."""

    content: str
    failed: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolInvocationResult:
    """Separate model-visible and ledger-safe projections of a tool call."""

    content: str
    failed: bool
    ledger_input: str
    ledger_output: str
    metadata: dict = field(default_factory=dict)
