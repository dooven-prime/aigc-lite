"""Resolve caller identity into a bounded, server-owned Chat capability set."""

from __future__ import annotations

from typing import Any

from ..core.chat_capabilities import (
    DEFAULT_CHAT_CAPABILITY_SET_ID,
    DELEGATED_CHAT_CAPABILITY,
    READ_ONLY_CHAT_CAPABILITY,
    ChatCapabilitySet,
)
from ..core.contracts import RequestContext, ToolRisk, ToolSource, ToolSpec
from ..core.errors import ChatCapabilityDeniedError, ResourceNotFoundError

_RISK_ORDER = {
    ToolRisk.LOW: 0,
    ToolRisk.MEDIUM: 1,
    ToolRisk.HIGH: 2,
}


class ResolvedChatCapability:
    """A capability set bound to one authenticated request context."""

    def __init__(self, capability_set: ChatCapabilitySet, context: RequestContext):
        self.capability_set = capability_set
        self.context = RequestContext(
            request_id=context.request_id,
            workspace_id=context.workspace_id,
            principal_id=context.principal_id,
            api_key_id=context.api_key_id,
            scopes=(
                context.scopes
                if capability_set.preserve_caller_scopes
                else frozenset()
            ),
            tool_hops=context.tool_hops,
        )

    @property
    def policy_id(self) -> str:
        return self.capability_set.capability_set_id

    @property
    def policy_hash(self) -> str:
        return self.capability_set.content_hash

    def allows(self, spec: ToolSpec) -> bool:
        policy = self.capability_set
        if spec.source not in policy.allowed_sources:
            return False
        if _RISK_ORDER[spec.risk] > _RISK_ORDER[policy.maximum_risk]:
            return False
        if policy.require_read_only and not spec.hints.read_only:
            return False
        if policy.require_non_destructive and spec.hints.destructive:
            return False
        return not (policy.require_closed_world and spec.hints.open_world)

    def requires_authorization(self, spec: ToolSpec) -> bool:
        policy = self.capability_set
        if policy.authorize_remote_tools and spec.source is ToolSource.MCP:
            return True
        return policy.authorize_side_effects and (
            spec.risk is not ToolRisk.LOW
            or not spec.hints.read_only
            or spec.hints.destructive
        )

    def ledger_metadata(self) -> dict[str, Any]:
        return {
            "contract_version": "chat.capability-decision.v1",
            "capability_set_id": self.policy_id,
            "capability_policy_hash": self.policy_hash,
            "effective_scopes": sorted(self.context.scopes),
        }


class ChatCapabilityPolicy:
    """Registry and resolver for model-visible Chat capabilities."""

    def __init__(self, capability_sets: tuple[ChatCapabilitySet, ...] | None = None):
        values = capability_sets or (
            READ_ONLY_CHAT_CAPABILITY,
            DELEGATED_CHAT_CAPABILITY,
        )
        self._sets: dict[str, ChatCapabilitySet] = {}
        for value in values:
            if value.capability_set_id in self._sets:
                raise ValueError(
                    f"Chat capability set already registered: {value.capability_set_id}"
                )
            self._sets[value.capability_set_id] = value

    def list_capability_sets(self) -> list[dict[str, Any]]:
        return [
            value.as_dict() | {"capability_policy_hash": value.content_hash}
            for _, value in sorted(self._sets.items())
        ]

    def resolve(
        self,
        context: RequestContext,
        capability_set_id: str | None = None,
    ) -> ResolvedChatCapability:
        selected_id = capability_set_id or DEFAULT_CHAT_CAPABILITY_SET_ID
        capability_set = self._sets.get(selected_id)
        if capability_set is None:
            raise ResourceNotFoundError("chat_capability_set", selected_id)
        if not capability_set.required_caller_scopes.issubset(context.scopes):
            raise ChatCapabilityDeniedError(
                selected_id,
                capability_set.required_caller_scopes,
            )
        return ResolvedChatCapability(capability_set, context)
