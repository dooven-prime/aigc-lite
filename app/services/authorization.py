"""Execution authorization gate for side-effecting tool capabilities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ..core.authorization import grant_allows, invocation_view
from ..core.contracts import RequestContext, ToolSpec
from ..core.enforcement import (
    authorization_invocation_digest,
    authorization_invocation_facts,
    enforcement_payload_hash,
    tool_spec_value,
)
from ..database import get_repository
from ..repository import Repository, utc_now
from .qualification import QualificationService

RepositoryProvider = Callable[[], Repository]


@dataclass(frozen=True, slots=True)
class ToolAuthorizationRequirement:
    action: str
    target: str


@dataclass(frozen=True, slots=True)
class ToolAuthorizationInvocation:
    """Server-derived facts used to evaluate a narrow execution grant."""

    scope: dict[str, Any]
    conditions: dict[str, Any]
    budget: dict[str, Any]


def tool_authorization_requirement(
    spec: ToolSpec,
) -> ToolAuthorizationRequirement | None:
    """Derive a host-enforced action/target pair from capability metadata."""

    extensions = spec.extensions if isinstance(spec.extensions, dict) else {}
    declared = extensions.get("authority_requirement")
    declared = declared if isinstance(declared, dict) else {}
    capability = extensions.get("capability")
    capability = capability if isinstance(capability, dict) else {}
    physical_effect = (
        capability.get("execution_class") == "physical"
        and capability.get("effect_class") != "observation"
    )
    robot_effect = not spec.hints.read_only and any(
        scope.startswith("robot:") for scope in spec.required_scopes
    )
    known_robot_action = spec.native_name in {
        "robot_navigate_to",
        "robot_cancel_action",
    }
    if not (
        declared.get("required") is True
        or physical_effect
        or robot_effect
        or known_robot_action
    ):
        return None

    action = spec.native_name
    declared_target = declared.get("target")
    provider_target = f"provider:{spec.provider_id}"
    target = provider_target
    if isinstance(declared_target, str) and declared_target.strip():
        candidate = f"{provider_target}/{declared_target.strip()}"
        if len(candidate) <= 1_000:
            target = candidate
    return ToolAuthorizationRequirement(action, target)


class ToolAuthorizationGate:
    """Consume a narrow grant before a physical provider receives a command."""

    def __init__(self, repository_provider: RepositoryProvider = get_repository) -> None:
        self._repository_provider = repository_provider
        self._qualification = QualificationService(repository_provider)

    def authorize_and_consume(
        self,
        context: RequestContext,
        spec: ToolSpec,
        arguments: dict[str, Any] | None = None,
        *,
        run_id: str | None = None,
        dispatch_binding: dict[str, str] | None = None,
        tool_spec_hash: str | None = None,
        dispatch_spec: ToolSpec | None = None,
    ) -> dict[str, Any] | None:
        requirement = tool_authorization_requirement(spec)
        if requirement is None:
            return {}
        if not context.principal_id:
            return None
        if dispatch_binding is not None and (not run_id or not tool_spec_hash):
            return None
        repository = self._repository_provider()
        invocation = _authorization_invocation(context, spec, arguments or {})
        candidates = [
            item
            for item in repository.list_authorization_grants(context.workspace_id)
            if item.get("actor_id") == context.principal_id
            and item.get("action") == requirement.action
            and item.get("target") == requirement.target
            and item.get("state") == "active"
            and int(item.get("calls_used") or 0) < int(item.get("max_calls") or 0)
        ]
        for grant in candidates:
            self._qualification.refresh_receipt_binding(
                context, grant["qualification_receipt_id"]
            )
            if not grant_allows(
                grant,
                {"scope": invocation.scope, "conditions": invocation.conditions,
                 "budget": invocation.budget},
            ):
                continue
            invocation_values = None
            if dispatch_binding is not None:
                facts = authorization_invocation_facts(
                    workspace_id=context.workspace_id,
                    actor_id=context.principal_id,
                    request_id=context.request_id,
                    run_id=run_id,
                    action=requirement.action,
                    target=requirement.target,
                    provider_id=spec.provider_id,
                    native_name=spec.native_name,
                    public_name=spec.name,
                    source=spec.source.value,
                    binding_id=dispatch_binding["binding_id"],
                    binding_hash=dispatch_binding["binding_hash"],
                    adapter_id=dispatch_binding["adapter_id"],
                    proposal_id=dispatch_binding["proposal_id"],
                    policy_hash=dispatch_binding["policy_hash"],
                    adapter_target_type=dispatch_binding["adapter_target_type"],
                    adapter_target_id=dispatch_binding["adapter_target_id"],
                    tool_spec_hash=tool_spec_hash,
                    tool_timeout_seconds=(dispatch_spec or spec).timeout_seconds,
                    arguments_digest=enforcement_payload_hash(arguments or {}),
                )
                invocation_values = {
                    "id": str(uuid4()),
                    **facts,
                    "invocation_digest": authorization_invocation_digest(facts),
                    "spec_snapshot": tool_spec_value(dispatch_spec or spec),
                    "arguments": arguments or {},
                }
            consumed = repository.consume_authorization_grant(
                context.workspace_id,
                context.principal_id,
                requirement.action,
                requirement.target,
                utc_now(),
                grant_id=grant["id"],
                invocation=invocation_values,
            )
            if consumed is not None:
                if invocation_values is not None:
                    consumed = {
                        **consumed,
                        "authorization_invocation_id": invocation_values["id"],
                        "invocation_digest": invocation_values["invocation_digest"],
                    }
                return consumed
        return None


def _authorization_invocation(
    context: RequestContext,
    spec: ToolSpec,
    arguments: dict[str, Any],
) -> ToolAuthorizationInvocation:
    """Build a closed, non-model-writable view of the concrete invocation."""
    view = invocation_view(context.workspace_id, tool_spec_value(spec), arguments)
    return ToolAuthorizationInvocation(
        scope=view["scope"],
        conditions=view["conditions"],
        budget=view["budget"],
    )
