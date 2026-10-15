"""Native, creation-time Goal identity; never a domain or authority verdict.

The host hashes the complete Goal envelope and a frozen Protocol snapshot.
The Protocol's caller-supplied ``content_hash`` is deliberately not used as
proof of these bytes. A domain casepack must still validate target and policy.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from .contracts import NativeGoalDraft

GOAL_BINDING_CONTRACT = "NativeGoalBinding.v0"
_GOAL_DOMAIN = b"aigc-lite.native-goal.v0\n"
_PROTOCOL_DOMAIN = b"aigc-lite.native-goal-protocol.v0\n"
_CONTRACT = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,99}$")
_MAX_GOAL_BYTES = 32_768
_MAX_DEPTH = 24
_MAX_NODES = 10_000
_FIELDS = (
    "goal_contract", "goal_protocol_id", "goal_protocol_version",
    "goal_protocol_sha256", "goal_sha256", "goal_json",
)


def _json_tree(value: Any) -> None:
    """Reject Python-only values and resource-exhausting nested proposals."""
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_NODES or depth > _MAX_DEPTH:
            raise ValueError("Goal exceeds structural limits")
        if item is None or type(item) in (str, bool, int):
            continue
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("Goal contains a non-finite number")
            continue
        if type(item) is list:
            stack.extend((member, depth + 1) for member in item)
            continue
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("Goal object keys must be strings")
            stack.extend((member, depth + 1) for member in item.values())
            continue
        raise ValueError("Goal must contain only JSON values")


def _canonical(value: Any, *, max_bytes: int = _MAX_GOAL_BYTES) -> str:
    _json_tree(value)
    try:
        content = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        encoded = content.encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as exc:
        raise ValueError("Goal is not canonical UTF-8 JSON") from exc
    if len(encoded) > max_bytes:
        raise ValueError("Goal exceeds byte limit")
    return content


def _json_column(value: Any, expected: type, field: str) -> Any:
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"Invalid frozen Protocol {field}") from exc
    _json_tree(decoded)
    if type(decoded) is not expected:
        raise ValueError(f"Invalid frozen Protocol {field}")
    return decoded


def protocol_goal_snapshot(protocol_row: dict[str, Any], workspace_id: str) -> dict[str, Any]:
    """Project only actual stored Protocol semantics, not its asserted digest."""
    if (
        protocol_row.get("tenant_id") != workspace_id
        or protocol_row.get("status") != "frozen"
        or not protocol_row.get("frozen_at")
    ):
        raise ValueError("Goal requires a same-workspace frozen Protocol")
    version = protocol_row.get("version")
    if type(version) is not int or version < 1:
        raise ValueError("Invalid frozen Protocol version")
    return {
        "id": protocol_row["id"],
        "workspace_id": workspace_id,
        "version": version,
        "status": "frozen",
        "purpose": protocol_row["purpose"],
        "scope": protocol_row["scope"],
        "completion_predicate": protocol_row["completion_predicate"],
        "stop_conditions": _json_column(protocol_row["stop_conditions"], list, "stop_conditions"),
        "budget": _json_column(protocol_row["budget"], dict, "budget"),
    }


def prepare_native_goal_binding(
    draft: NativeGoalDraft, protocol_row: dict[str, Any], workspace_id: str,
) -> dict[str, Any]:
    """Validate the frozen reference and compute host-owned creation values."""
    if not isinstance(draft, NativeGoalDraft):
        raise ValueError("Invalid Goal binding draft")
    protocol = protocol_goal_snapshot(protocol_row, workspace_id)
    if (
        draft.protocol_id != protocol["id"]
        or type(draft.protocol_version) is not int
        or draft.protocol_version != protocol["version"]
    ):
        raise ValueError("Goal Protocol identity or version mismatch")
    goal = draft.goal
    if type(goal) is not dict or set(goal) != {"contract", "protocol", "target", "policy"}:
        raise ValueError("Goal requires a complete versioned envelope")
    contract = goal["contract"]
    if type(contract) is not str or not _CONTRACT.fullmatch(contract):
        raise ValueError("Invalid Goal contract identity")
    protocol_json = _canonical(protocol)
    if _canonical(goal["protocol"]) != protocol_json:
        raise ValueError("Goal Protocol snapshot differs from frozen source")
    if type(goal["target"]) is not str or not goal["target"].strip():
        raise ValueError("Goal target is required")
    if type(goal["policy"]) is not dict or not goal["policy"]:
        raise ValueError("Goal policy is required")
    goal_json = _canonical(goal)
    return {
        "goal_contract": contract,
        "goal_protocol_id": protocol["id"],
        "goal_protocol_version": protocol["version"],
        "goal_protocol_sha256": hashlib.sha256(
            _PROTOCOL_DOMAIN + protocol_json.encode("utf-8")
        ).hexdigest(),
        "goal_sha256": hashlib.sha256(
            _GOAL_DOMAIN + goal_json.encode("utf-8")
        ).hexdigest(),
        "goal_json": goal_json,
    }


def assess_native_goal_binding(
    run: dict[str, Any], protocol_row: dict[str, Any] | None,
    workspace_id: str,
) -> tuple[str, dict[str, Any] | None]:
    """Fail closed on partial, altered, or missing native source records."""
    values = [run.get(field) for field in _FIELDS]
    if all(value is None for value in values):
        return "NATIVE_UNBOUND", None
    if any(value is None for value in values) or protocol_row is None:
        return "INCONSISTENT", None
    try:
        goal = json.loads(run["goal_json"])
        prepared = prepare_native_goal_binding(
            NativeGoalDraft(
                protocol_id=run["goal_protocol_id"],
                protocol_version=run["goal_protocol_version"],
                goal=goal,
            ),
            protocol_row,
            workspace_id,
        )
    except (TypeError, ValueError, KeyError, RecursionError):
        return "INCONSISTENT", None
    if any(prepared[field] != run[field] for field in _FIELDS):
        return "INCONSISTENT", None
    return "NATIVE_BOUND", {
        "contract": GOAL_BINDING_CONTRACT,
        "goal_contract": prepared["goal_contract"],
        "protocol_id": prepared["goal_protocol_id"],
        "protocol_version": prepared["goal_protocol_version"],
        "protocol_sha256": prepared["goal_protocol_sha256"],
        "goal_sha256": prepared["goal_sha256"],
        "identity_scope": "CREATION_TIME_BYTES_ONLY",
    }
