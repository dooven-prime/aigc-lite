"""Server-owned capability sets for model-driven Chat tool execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import ToolRisk, ToolSource
from .qualification import canonical_hash

CHAT_CAPABILITY_CONTRACT_VERSION = "chat.capability-policy.v1"
DEFAULT_CHAT_CAPABILITY_SET_ID = "chat.read-only.v1"
DELEGATED_CHAT_CAPABILITY_SET_ID = "chat.delegated.v1"


@dataclass(frozen=True, slots=True)
class ChatCapabilitySet:
    """One immutable, host-owned tool visibility and authorization policy."""

    capability_set_id: str
    version: int
    description: str
    allowed_sources: tuple[ToolSource, ...]
    maximum_risk: ToolRisk
    required_caller_scopes: frozenset[str] = frozenset()
    preserve_caller_scopes: bool = False
    require_read_only: bool = False
    require_non_destructive: bool = False
    require_closed_world: bool = False
    authorize_remote_tools: bool = True
    authorize_side_effects: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CHAT_CAPABILITY_CONTRACT_VERSION,
            "capability_set_id": self.capability_set_id,
            "version": self.version,
            "description": self.description,
            "allowed_sources": [item.value for item in self.allowed_sources],
            "maximum_risk": self.maximum_risk.value,
            "required_caller_scopes": sorted(self.required_caller_scopes),
            "preserve_caller_scopes": self.preserve_caller_scopes,
            "require_read_only": self.require_read_only,
            "require_non_destructive": self.require_non_destructive,
            "require_closed_world": self.require_closed_world,
            "authorize_remote_tools": self.authorize_remote_tools,
            "authorize_side_effects": self.authorize_side_effects,
        }

    @property
    def content_hash(self) -> str:
        return canonical_hash(self.as_dict())


READ_ONLY_CHAT_CAPABILITY = ChatCapabilitySet(
    capability_set_id=DEFAULT_CHAT_CAPABILITY_SET_ID,
    version=1,
    description=(
        "Default Chat policy: only low-risk, read-only, non-destructive, closed-world "
        "local and workspace capabilities; caller administration scopes are removed."
    ),
    allowed_sources=(ToolSource.LOCAL, ToolSource.WORKSPACE),
    maximum_risk=ToolRisk.LOW,
    preserve_caller_scopes=False,
    require_read_only=True,
    require_non_destructive=True,
    require_closed_world=True,
)

DELEGATED_CHAT_CAPABILITY = ChatCapabilitySet(
    capability_set_id=DELEGATED_CHAT_CAPABILITY_SET_ID,
    version=1,
    description=(
        "Explicit delegated Chat policy: retains caller Tool scopes, permits remote "
        "providers, and requires a narrow AuthorizationGrant for every remote, "
        "side-effecting, destructive, or medium/high-risk invocation."
    ),
    allowed_sources=(ToolSource.LOCAL, ToolSource.WORKSPACE, ToolSource.MCP),
    maximum_risk=ToolRisk.HIGH,
    required_caller_scopes=frozenset({"tools:write"}),
    preserve_caller_scopes=True,
    authorize_remote_tools=True,
    authorize_side_effects=True,
)
