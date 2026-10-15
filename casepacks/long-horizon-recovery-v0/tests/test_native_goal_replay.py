"""A native Goal binding changes identity, not replay authority."""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aigc_lite_casepacks.long_horizon_recovery_v0.host_projection_replay import (  # noqa: E402
    replay_host_projection,
)
from aigc_lite_casepacks.long_horizon_recovery_v0.replay import (  # noqa: E402
    ContractError,
    digest,
)
from test_host_projection_replay import _goal, _hash, _projection, _step  # noqa: E402


def _native_hash(domain: bytes, value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(domain + raw).hexdigest()


def _seal_projection(projection: dict) -> None:
    source = projection["source"]
    source["sha256"] = _hash({
        "workspace_id": projection["workspace_id"],
        "run_id": projection["run_id"],
        "rows": source["rows"],
        "association_basis": source["association_basis"],
        "omissions": source["omissions"],
    })
    projection["sha256"] = _hash({
        key: value for key, value in projection.items() if key != "sha256"
    })


def _bound_projection(goal: dict) -> dict:
    projection = _projection(steps=[_step(1)])
    projection["contract"] = "HostLedgerProjection.v1"
    projection["signals"]["goal_binding"] = "NATIVE_BOUND"
    projection["source"]["omissions"] = [{
        "source": "scheduled_task_firings",
        "reason": "no_durable_run_id_association",
    }]
    projection["omissions"] = [
        *projection["source"]["omissions"],
        *projection["omissions"][1:],
    ]
    protocol_row_hash = _hash({"stored-protocol": "p-1"})
    projection["source"]["rows"]["goal_protocols"] = [{
        "id": goal["protocol"]["id"], "sha256": protocol_row_hash,
    }]
    projection["source"]["association_basis"]["goal_protocol_id"] = goal["protocol"]["id"]
    projection["native_goal"] = {
        "status": "NATIVE_BOUND",
        "binding": {
            "contract": "NativeGoalBinding.v0",
            "goal_contract": goal["contract"],
            "protocol_id": goal["protocol"]["id"],
            "protocol_version": goal["protocol"]["version"],
            "protocol_sha256": _native_hash(
                b"aigc-lite.native-goal-protocol.v0\n", goal["protocol"]),
            "goal_sha256": _native_hash(b"aigc-lite.native-goal.v0\n", goal),
            "identity_scope": "CREATION_TIME_BYTES_ONLY",
        },
        "source_protocol_row_sha256": protocol_row_hash,
    }
    _seal_projection(projection)
    return projection


def _replay(goal: dict, projection: dict, *, goal_pin: str | None = None) -> dict:
    return replay_host_projection(
        goal, projection,
        expected_goal_sha256=goal_pin or _native_hash(b"aigc-lite.native-goal.v0\n", goal),
        expected_projection_sha256=projection["sha256"],
    )


def test_bound_goal_replay_is_versioned_and_candidate_only() -> None:
    goal = _goal()
    projection = _bound_projection(goal)
    result = _replay(goal, projection)

    assert result == _replay(goal, projection)
    assert result["kind"] == "HostProjectionReplay.v1"
    assert result["goal_binding"] == "NATIVE_BOUND"
    assert result["native_goal"] == projection["native_goal"]
    assert result["goal_sha256"] == projection["native_goal"]["binding"]["goal_sha256"]
    assert result["progress_assessment"]["U"]["goal_binding"] == "NATIVE_BOUND"
    assert result["progress_assessment"]["E"]["projected_verified_count"] == 0
    assert result["progress_assessment"]["completion_evaluated"] is False
    assert result["recovery_decision"]["state"] == "PROPOSED"
    assert result["recovery_decision"]["action"] == "REASSESS"
    assert result["recovery_decision"]["execution_authorized"] is False
    assert not any(result["authority_effects_of_replay"].values())
    body = {key: value for key, value in result.items() if key != "sha256"}
    assert result["sha256"] == _native_hash(b"aigc-lite.host-projection-replay.v1\n", body)


def test_bound_goal_requires_native_pin_and_exact_frozen_goal_bytes() -> None:
    goal = _goal()
    projection = _bound_projection(goal)
    with pytest.raises(ContractError, match="Pinned native Goal digest mismatch"):
        _replay(goal, projection, goal_pin=digest(goal))

    altered_goal = deepcopy(goal)
    altered_goal["target"] = "different-target"
    with pytest.raises(ContractError, match="Native Goal binding differs"):
        _replay(altered_goal, projection)

    altered_goal = deepcopy(goal)
    altered_goal["protocol"]["purpose"] = "different-purpose"
    with pytest.raises(ContractError, match="Native Goal binding differs"):
        _replay(altered_goal, projection)


@pytest.mark.parametrize("field,value", [
    ("goal_contract", "AnotherGoal.v0"),
    ("protocol_version", 2),
    ("protocol_sha256", "a" * 64),
    ("goal_sha256", "b" * 64),
])
def test_resealed_native_binding_cannot_change_pinned_identity(field: str, value: object) -> None:
    goal = _goal()
    projection = _bound_projection(goal)
    projection["native_goal"]["binding"][field] = value
    _seal_projection(projection)
    with pytest.raises(ContractError, match="Native Goal binding differs"):
        _replay(goal, projection)


def test_goal_protocol_row_and_association_must_match_binding() -> None:
    goal = _goal()
    projection = _bound_projection(goal)
    projection["source"]["rows"]["goal_protocols"][0]["sha256"] = "c" * 64
    _seal_projection(projection)
    with pytest.raises(ContractError, match="Goal Protocol source reference"):
        _replay(goal, projection)

    projection = _bound_projection(goal)
    projection["source"]["association_basis"]["goal_protocol_id"] = "another-protocol"
    _seal_projection(projection)
    with pytest.raises(ContractError, match="Goal Protocol association"):
        _replay(goal, projection)


def test_matching_source_refs_cannot_substitute_another_protocol_id() -> None:
    goal = _goal()
    projection = _bound_projection(goal)
    projection["native_goal"]["binding"]["protocol_id"] = "p-2"
    projection["source"]["rows"]["goal_protocols"][0]["id"] = "p-2"
    projection["source"]["association_basis"]["goal_protocol_id"] = "p-2"
    _seal_projection(projection)
    with pytest.raises(ContractError, match="Native Goal binding differs"):
        _replay(goal, projection)


def test_inconsistent_or_ambiguous_projection_never_replays() -> None:
    goal = _goal()
    projection = _bound_projection(goal)
    projection["native_goal"]["status"] = "INCONSISTENT"
    _seal_projection(projection)
    with pytest.raises(ContractError, match="not consistent and bound"):
        _replay(goal, projection)

    projection = _bound_projection(goal)
    projection["signals"]["goal_binding"] = "NATIVE_UNBOUND"
    _seal_projection(projection)
    with pytest.raises(ContractError, match="signals conflict"):
        _replay(goal, projection)

    projection = _bound_projection(goal)
    projection["source"]["rows"]["goal_protocols"].append({
        "id": "p-2", "sha256": "d" * 64,
    })
    _seal_projection(projection)
    with pytest.raises(ContractError, match="Goal Protocol source reference"):
        _replay(goal, projection)


def test_v0_does_not_accept_native_fields_and_keeps_legacy_pin() -> None:
    goal = _goal()
    projection = _projection()
    result = replay_host_projection(
        goal, projection,
        expected_goal_sha256=digest(goal),
        expected_projection_sha256=projection["sha256"],
    )
    assert result["kind"] == "HostProjectionReplay.v0"
    assert result["goal_binding"] == "HOST_ASSERTED_NOT_NATIVE"
    assert "native_goal" not in result

    with pytest.raises(ContractError, match="Pinned goal digest mismatch"):
        _replay(goal, projection)

    projection["native_goal"] = _bound_projection(goal)["native_goal"]
    _seal_projection(projection)
    with pytest.raises(ContractError, match="Malformed HostLedgerProjection"):
        replay_host_projection(
            goal, projection, expected_goal_sha256=digest(goal),
            expected_projection_sha256=projection["sha256"],
        )
