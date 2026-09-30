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
    ) -> dict[str, Any] | None:
        requirement = tool_authorization_requirement(spec)
        if requirement is None:
            return {}
        if not context.principal_id:
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
            if not _grant_allows(grant, invocation):
                continue
            consumed = repository.consume_authorization_grant(
                context.workspace_id,
                context.principal_id,
                requirement.action,
                requirement.target,
                utc_now(),
                grant_id=grant["id"],
            )
            if consumed is not None:
                return consumed
        return None


def _authorization_invocation(
    context: RequestContext,
    spec: ToolSpec,
    arguments: dict[str, Any],
) -> ToolAuthorizationInvocation:
    """Build a closed, non-model-writable view of the concrete invocation."""

    effective_arguments = dict(arguments)
    properties = spec.input_schema.get("properties", {})
    if isinstance(properties, dict):
        for name, schema in properties.items():
            if (
                name not in effective_arguments
                and isinstance(schema, dict)
                and "default" in schema
            ):
                effective_arguments[name] = schema["default"]
    extensions = spec.extensions if isinstance(spec.extensions, dict) else {}
    capability = extensions.get("capability")
    capability = capability if isinstance(capability, dict) else {}
    numeric_budget = {
        key: value
        for key, value in effective_arguments.items()
        if key.endswith("timeout_seconds")
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    }
    return ToolAuthorizationInvocation(
        scope={
            "workspace_id": context.workspace_id,
            "provider_id": spec.provider_id,
            "tool_name": spec.native_name,
            "public_tool_name": spec.name,
            "required_scopes": sorted(spec.required_scopes),
            "risk": spec.risk.value,
        },
        conditions={
            "arguments": effective_arguments,
            "capability": capability,
        },
        budget={
            "tool_timeout_seconds": spec.timeout_seconds,
            "arguments": numeric_budget,
        },
    )


def _grant_allows(
    grant: dict[str, Any], invocation: ToolAuthorizationInvocation
) -> bool:
    return (
        _constraint_matches(grant.get("scope") or {}, invocation.scope)
        and _constraint_matches(
            grant.get("conditions") or {}, invocation.conditions
        )
        and _budget_within(grant.get("budget") or {}, invocation.budget)
    )


def _constraint_matches(expected: Any, actual: Any) -> bool:
    """Match a small deterministic subset/range constraint language."""

    if isinstance(expected, dict):
        operators = {key for key in expected if str(key).startswith("$")}
        if operators:
            if operators != set(expected):
                return False
            if "$eq" in expected and actual != expected["$eq"]:
                return False
            if "$in" in expected:
                values = expected["$in"]
                if not isinstance(values, list) or actual not in values:
                    return False
            for operator, comparator in (("$lte", lambda a, b: a <= b), ("$gte", lambda a, b: a >= b)):
                if operator not in expected:
                    continue
                limit = expected[operator]
                if (
                    isinstance(actual, bool)
                    or isinstance(limit, bool)
                    or not isinstance(actual, (int, float))
                    or not isinstance(limit, (int, float))
                    or not comparator(actual, limit)
                ):
                    return False
            return operators.issubset({"$eq", "$in", "$lte", "$gte"})
        if not isinstance(actual, dict):
            return False
        return all(
            key in actual and _constraint_matches(value, actual[key])
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(item in actual for item in expected)
    return expected == actual


def _budget_within(limit: Any, actual: Any) -> bool:
    if isinstance(limit, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            key in actual and _budget_within(value, actual[key])
            for key, value in limit.items()
        )
    if (
        isinstance(limit, (int, float))
        and not isinstance(limit, bool)
        and isinstance(actual, (int, float))
        and not isinstance(actual, bool)
    ):
        return actual <= limit
    return limit == actual
