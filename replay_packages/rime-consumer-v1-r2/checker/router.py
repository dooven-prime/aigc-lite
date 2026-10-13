"""Finite, three-route comparison for the frozen consumer replay contract.

Only a checked finite witness can PASS. This is not a proof of the general
consumer theorem, checker correctness, or independent validation.
"""

from __future__ import annotations

from log_oracle import LogOracleState
from placed_forest import PlacedForestState
from placed_ufe import PlacedUFEState
from utils import check_witness_shape, command_guard

COMPARE_FIELDS = (
    "partition", "origin_event_index", "absorptions", "carrier",
    "fusion_event_count", "fusion_events",
)


def _snapshot(route) -> dict:
    state = route.to_summary()
    state["fusion_events"] = [
        event.to_dict() if hasattr(event, "to_dict") else dict(event)
        for event in route.fusion_events
    ]
    return state


def _expected_report(state: dict, selected_block: list[int]) -> dict | None:
    origin_index = state["origin_event_index"]
    if origin_index is None or state["carrier"] is None:
        return None
    origin = state["fusion_events"][origin_index]
    return {
        "partition": [
            {"q": int(q), "block": block}
            for q, block in sorted(state["partition"].items(), key=lambda item: int(item[0]))
        ],
        "selected_block": selected_block,
        "origin": {
            "letter": "d",
            "ports": [origin["k_0"], origin["k_1"]],
            "input_blocks": [origin["b_0"], origin["b_1"]],
            "collision_image": origin["c"],
            "fresh_block": origin["fresh_block"],
        },
        "absorptions": [
            {
                "letter": "d",
                "carrier_port": item["carrier_port"],
                "carrier_before": item["carrier_before"],
                "other_port": item["other_port"],
                "other_block": item["other_block"],
                "collision_image": item["c"],
                "carrier_after": item["carrier_after"],
            }
            for item in state["absorptions"]
        ],
        "carrier": {"q": state["carrier"]["pos"], "block": state["carrier"]["block"]},
    }


def run_witness(witness: dict) -> dict:
    result = {
        "verdict": "UNRESOLVED",
        "failure_position": None,
        "failure_reason": None,
        "steps": [],
        "route_summaries": {},
        "report_check": None,
    }
    ok, reason = check_witness_shape(witness)
    if not ok:
        result.update(verdict="INVALID_REQUEST", failure_reason=f"shape: {reason}")
        return result

    env = witness["environment"]
    selected = witness["selected_block"]
    try:
        routes = {
            "log_oracle": LogOracleState(env=env, F=selected),
            "placed_forest": PlacedForestState(env=env, F=selected),
            "placed_ufe": PlacedUFEState(env=env, F=selected),
        }
    except Exception as exc:
        result["failure_reason"] = f"route init failed: {exc}"
        return result

    snapshots: list[dict[str, dict]] = []

    def compare_step(label: str) -> bool:
        index = len(snapshots)
        try:
            state = {name: _snapshot(route) for name, route in routes.items()}
        except Exception as exc:
            result["failure_position"] = index
            result["failure_reason"] = f"snapshot failed at {label}: {exc}"
            return False
        snapshots.append(state)
        oracle = state["log_oracle"]
        disagreements = [
            f"{name}.{field}"
            for name in ("placed_forest", "placed_ufe")
            for field in COMPARE_FIELDS
            if state[name][field] != oracle[field]
        ]
        enumeration = {atom: i for i, atom in enumerate(env["ufe_enumeration"])}
        expected_unions = [
            [min(enumeration[atom] for atom in event["b_0"]),
             min(enumeration[atom] for atom in event["b_1"])]
            for event in oracle["fusion_events"]
        ]
        actual_unions = [list(pair) for pair in routes["placed_ufe"].ufe.ordered_unions]
        if actual_unions != expected_unions:
            disagreements.append("placed_ufe.ordered_unions")
        result["steps"].append({
            "step": index, "label": label, "agreed": not disagreements,
            "disagreements": disagreements, "routes": state,
            "ordered_unions": actual_unions,
        })
        if disagreements:
            result.update(
                verdict="FAIL", failure_position=index,
                failure_reason=f"3 routes disagree at step {index}: {', '.join(disagreements)}",
            )
            return False
        return True

    if not compare_step("initial"):
        return result
    for letter in witness["prefix"]:
        try:
            for route in routes.values():
                route.apply_letter(letter)
        except Exception as exc:
            result["failure_position"] = len(snapshots)
            result["failure_reason"] = f"letter {letter!r} crashed: {exc}"
            return result
        if not compare_step(f"after {letter!r}"):
            return result

    for command in witness["commands"]:
        before = snapshots[-1]
        guards = {
            name: command_guard(env, state["partition"], state["carrier"], command)
            for name, state in before.items()
        }
        if len(set(guards.values())) != 1:
            result.update(
                verdict="FAIL", failure_position=len(snapshots) - 1,
                failure_reason=f"command guard disagreement: {guards}",
            )
            return result
        denial = guards["log_oracle"]
        if denial is not None:
            result.update(verdict="FAIL", failure_position=len(snapshots) - 1, failure_reason=denial)
            return result
        try:
            applied = {name: route.apply_command(command) for name, route in routes.items()}
        except Exception as exc:
            result["failure_position"] = len(snapshots)
            result["failure_reason"] = f"command {command} crashed: {exc}"
            return result
        if any(not item.get("applied") for item in applied.values()):
            result.update(
                verdict="FAIL", failure_position=len(snapshots) - 1,
                failure_reason=f"command application disagreement: {applied}",
            )
            return result
        if not compare_step(f"after {command}"):
            return result

    final = snapshots[-1]["log_oracle"]
    if final["origin_event_index"] is None:
        result.update(verdict="FAIL", failure_reason="F never registered as a fresh block")
        return result
    if final["carrier"] is None:
        result.update(verdict="FAIL", failure_reason="carrier not found at final state")
        return result

    reports = witness.get("candidate_reports")
    if reports is None:
        result["report_check"] = {"checked": 0, "omitted": True}
    else:
        report_indices = [len(witness["prefix"]) + i for i in range(len(reports))]
        for report_index, (state_index, submitted) in enumerate(zip(report_indices, reports, strict=True)):
            expected = _expected_report(snapshots[state_index]["log_oracle"], selected)
            if expected is None:
                result.update(
                    verdict="FAIL", failure_position=state_index,
                    failure_reason=f"candidate_reports[{report_index}] has no registered state",
                    report_check={"checked": report_index, "all_match": False},
                )
                return result
            for field in expected:
                if submitted[field] != expected[field]:
                    result.update(
                        verdict="FAIL", failure_position=state_index,
                        failure_reason=f"candidate_reports[{report_index}] {field} mismatch",
                        report_check={"checked": report_index + 1, "all_match": False,
                                      "field": field, "expected": expected[field],
                                      "submitted": submitted[field]},
                    )
                    return result
        result["report_check"] = {"checked": len(reports), "all_match": True}

    result["verdict"] = "PASS"
    result["route_summaries"] = snapshots[-1]
    return result
