"""Pure, server-side checks for a frozen authorization invocation."""

from __future__ import annotations

from typing import Any


def invocation_view(
    workspace_id: str, spec: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    effective_arguments = dict(arguments)
    properties = spec.get("input_schema", {}).get("properties", {})
    if isinstance(properties, dict):
        for name, schema in properties.items():
            if name not in effective_arguments and isinstance(schema, dict) and "default" in schema:
                effective_arguments[name] = schema["default"]
    extensions = spec.get("extensions")
    extensions = extensions if isinstance(extensions, dict) else {}
    capability = extensions.get("capability")
    capability = capability if isinstance(capability, dict) else {}
    numeric_budget = {
        key: value
        for key, value in effective_arguments.items()
        if key.endswith("timeout_seconds")
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    }
    return {
        "scope": {
            "workspace_id": workspace_id,
            "provider_id": spec["provider_id"],
            "tool_name": spec["native_name"],
            "public_tool_name": spec["name"],
            "required_scopes": sorted(spec["required_scopes"]),
            "risk": spec["risk"],
        },
        "conditions": {"arguments": effective_arguments, "capability": capability},
        "budget": {
            "tool_timeout_seconds": spec["timeout_seconds"],
            "arguments": numeric_budget,
        },
    }


def grant_allows(grant: dict[str, Any], view: dict[str, Any]) -> bool:
    return (
        _constraint_matches(grant.get("scope") or {}, view["scope"])
        and _constraint_matches(grant.get("conditions") or {}, view["conditions"])
        and _budget_within(grant.get("budget") or {}, view["budget"])
    )


def _constraint_matches(expected: Any, actual: Any) -> bool:
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
            for operator, comparator in (
                ("$lte", lambda a, b: a <= b),
                ("$gte", lambda a, b: a >= b),
            ):
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
        return isinstance(actual, dict) and all(
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
