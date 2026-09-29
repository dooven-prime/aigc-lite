"""Execution authorization gate for side-effecting tool capabilities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..core.contracts import RequestContext, ToolSpec
from ..database import get_repository
from ..repository import Repository, utc_now
from .qualification import QualificationService

RepositoryProvider = Callable[[], Repository]


@dataclass(frozen=True, slots=True)
class ToolAuthorizationRequirement:
    action: str
    target: str


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
    ) -> dict[str, Any] | None:
        requirement = tool_authorization_requirement(spec)
        if requirement is None:
            return {}
        if not context.principal_id:
            return None
        repository = self._repository_provider()
        matching_receipt_ids = {
            item["qualification_receipt_id"]
            for item in repository.list_authorization_grants(context.workspace_id)
            if item.get("actor_id") == context.principal_id
            and item.get("action") == requirement.action
            and item.get("target") == requirement.target
            and item.get("state") == "active"
            and int(item.get("calls_used") or 0) < int(item.get("max_calls") or 0)
        }
        for receipt_id in matching_receipt_ids:
            self._qualification.refresh_receipt_binding(context, receipt_id)
        return repository.consume_authorization_grant(
            context.workspace_id,
            context.principal_id,
            requirement.action,
            requirement.target,
            utc_now(),
        )
