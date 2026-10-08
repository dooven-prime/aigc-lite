"""Deterministic simulator acceptance scenarios and portable replay reports.

This is an operator-run simulation evaluator, not a physical safety controller
or a source of execution authority. Each trial owns a fresh simulator.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from importlib.resources import files
from math import hypot, isfinite
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any
from uuid import uuid4

from . import __version__
from .contracts import Pose2D
from .service import RobotCapabilityService
from .simulator import SimulatorBackend

SCENARIO_VERSION = "robot.eval-scenario.v1"
REPORT_VERSION = "robot.eval-report.v1"
REPLAY_VERSION = "robot.eval-replay.v1"
FAULTS = frozenset(
    {
        "success",
        "failure",
        "indeterminate",
        "goal_rejected",
        "feedback_stall",
        "transport_loss",
        "localization_loss",
        "cancel_unconfirmed",
    }
)
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{2,63}\Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _number(value: Any, name: str, *, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not isfinite(result) or not low <= result <= high:
        raise ValueError(f"{name} must be finite and between {low} and {high}")
    return result


def _fields(value: Any, required: set[str], optional: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - required - optional or required - set(value):
        raise ValueError(f"{label} has missing or unexpected fields")
    return value


def validate_scenario(value: Any) -> dict[str, Any]:
    """Fail closed on version drift, unknown fields, and unbounded scenarios."""

    scenario = _fields(
        value,
        {
            "contract_version",
            "scenario_id",
            "robot_id",
            "map_id",
            "seed",
            "start_pose",
            "goal_tolerance_m",
            "trials",
        },
        set(),
        "scenario",
    )
    if scenario["contract_version"] != SCENARIO_VERSION:
        raise ValueError("Unsupported scenario contract")
    for name in ("scenario_id", "robot_id", "map_id"):
        if not isinstance(scenario[name], str) or not _IDENTIFIER.fullmatch(scenario[name]):
            raise ValueError(f"Invalid {name}")
    if (
        isinstance(scenario["seed"], bool)
        or not isinstance(scenario["seed"], int)
        or not 0 <= scenario["seed"] <= 2**32 - 1
    ):
        raise ValueError("seed must be a bounded integer")
    start = _fields(scenario["start_pose"], {"x", "y", "yaw"}, set(), "start_pose")
    for name in ("x", "y", "yaw"):
        _number(start[name], f"start_pose.{name}", low=-1000, high=1000)
    _number(scenario["goal_tolerance_m"], "goal_tolerance_m", low=0, high=10)
    trials = scenario["trials"]
    if not isinstance(trials, list) or not 1 <= len(trials) <= 50:
        raise ValueError("Scenario needs 1–50 trials")
    seen = set()
    for trial in trials:
        item = _fields(
            trial,
            {
                "id",
                "fault",
                "operation",
                "goal",
                "travel_seconds",
                "action_timeout_seconds",
                "expected",
            },
            {"cancel_after_seconds"},
            "trial",
        )
        if (
            not isinstance(item["id"], str)
            or not _IDENTIFIER.fullmatch(item["id"])
            or item["id"] in seen
        ):
            raise ValueError("Trial IDs must be unique and bounded")
        seen.add(item["id"])
        if (
            not isinstance(item["fault"], str)
            or item["fault"] not in FAULTS
            or not isinstance(item["operation"], str)
            or item["operation"] not in {"navigate", "cancel"}
        ):
            raise ValueError("Unsupported fault or operation")
        goal = _fields(item["goal"], {"x", "y", "yaw"}, set(), "goal")
        for name in ("x", "y", "yaw"):
            _number(goal[name], f"goal.{name}", low=-1000, high=1000)
        _number(item["travel_seconds"], "travel_seconds", low=0.01, high=10)
        _number(item["action_timeout_seconds"], "action_timeout_seconds", low=1, high=120)
        if item["operation"] == "cancel":
            _number(item.get("cancel_after_seconds"), "cancel_after_seconds", low=0, high=5)
            if item["cancel_after_seconds"] >= item["travel_seconds"]:
                raise ValueError("Cancellation must precede simulated arrival")
        elif "cancel_after_seconds" in item:
            raise ValueError("Only cancel trials may set cancel_after_seconds")
        expected = _fields(
            item["expected"],
            {"status", "stop_confirmed", "error_code", "motion_blocked", "feedback_class"},
            set(),
            "expected",
        )
        if not isinstance(expected["status"], str) or expected["status"] not in {
            "succeeded",
            "failed",
            "cancelled",
            "indeterminate",
        }:
            raise ValueError("Invalid expected status")
        if not isinstance(expected["stop_confirmed"], bool) or not isinstance(
            expected["motion_blocked"], bool
        ):
            raise ValueError("Expected stop and motion flags must be boolean")
        if expected["error_code"] is not None and not isinstance(expected["error_code"], str):
            raise ValueError("Expected error_code must be a string or null")
        if not isinstance(expected["feedback_class"], str) or expected["feedback_class"] not in {
            "normal",
            "sparse",
            "none",
            "variable",
        }:
            raise ValueError("Invalid feedback class")
    return scenario


def load_scenario(path: Path) -> dict[str, Any]:
    return validate_scenario(json.loads(path.read_text(encoding="utf-8")))


def load_bundled_scenario() -> dict[str, Any]:
    """Load the versioned acceptance scenario from an installed wheel."""

    resource = files("aigc_lite_ros2").joinpath("scenarios/navigation_v1.json")
    return validate_scenario(json.loads(resource.read_text(encoding="utf-8")))


def _feedback_class(count: int, operation: str) -> str:
    if operation == "cancel":
        return "variable"
    if count == 0:
        return "none"
    if count <= 2:
        return "sparse"
    return "normal"


def _signature(trial: dict[str, Any]) -> dict[str, Any]:
    """Only deterministic observations enter replay comparison, never timings/UUIDs."""

    signature = {
        key: trial[key]
        for key in (
            "trial_id",
            "status",
            "stop_confirmed",
            "error_code",
            "motion_blocked",
            "feedback_class",
            "at_goal",
            "task_success",
            "contract_pass",
            "failure_type",
        )
    }
    state = trial["final_state"]
    pose = state.get("pose") if trial["status"] == "succeeded" else None
    signature["final_state"] = {
        "localized": state.get("localized"),
        "navigation_status": state.get("navigation_status"),
        "action_active": state.get("active_action_id") is not None,
        "pose": None
        if pose is None
        else {
            "x": round(pose["x"], 6),
            "y": round(pose["y"], 6),
            "yaw": round(pose["yaw"], 6),
            "frame_id": pose["frame_id"],
        },
    }
    return signature


async def _invoke(
    service: RobotCapabilityService,
    steps: list[dict[str, Any]],
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    began = perf_counter()
    started = _timestamp()
    result = await service.invoke(name, arguments)
    steps.append(
        {
            "step_id": str(uuid4()),
            "sequence": len(steps) + 1,
            "capability": name,
            "arguments": arguments,
            "started_at": started,
            "completed_at": _timestamp(),
            "elapsed_ms": round((perf_counter() - began) * 1000, 3),
            "failed": result.failed,
            "result": result.payload,
        }
    )
    return result.payload


async def _one_trial(scenario: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    backend = SimulatorBackend(
        robot_id=scenario["robot_id"],
        map_id=scenario["map_id"],
        travel_seconds=spec["travel_seconds"],
        outcome=spec["fault"],
        initial_pose=Pose2D(**scenario["start_pose"], frame_id=scenario["map_id"]),
    )
    service = RobotCapabilityService(backend)
    steps: list[dict[str, Any]] = []
    began = perf_counter()
    args = {
        "idempotency_key": f"eval-{spec['id']}-primary",
        "x": spec["goal"]["x"],
        "y": spec["goal"]["y"],
        "yaw": spec["goal"]["yaw"],
        "frame_id": scenario["map_id"],
        "action_timeout_seconds": spec["action_timeout_seconds"],
    }
    if spec["operation"] == "navigate":
        receipt = await _invoke(service, steps, "robot_navigate_to", args)
    else:
        running = asyncio.create_task(_invoke(service, steps, "robot_navigate_to", args))
        try:
            async with asyncio.timeout(2):
                while (await backend.get_state()).active_action_id is None:
                    await asyncio.sleep(0.001)
            await asyncio.sleep(spec["cancel_after_seconds"])
            cancellation = await _invoke(
                service,
                steps,
                "robot_cancel_action",
                {"idempotency_key": args["idempotency_key"], "timeout_seconds": 2},
            )
            navigation = await running
            receipt = navigation if "action_id" in navigation else cancellation
            # Tool completion order can differ; the cancellation result is the
            # canonical stop observation when it carries the action receipt.
            if "action_id" in cancellation:
                receipt = cancellation
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
    status = receipt.get("status")
    stop_confirmed = receipt.get("stop_confirmed")
    error = receipt.get("error") or {}
    error_code = error.get("code")
    feedback_count = len(receipt.get("feedback") or [])
    feedback_class = _feedback_class(feedback_count, spec["operation"])
    motion_blocked = False
    blocked_probe = None
    if stop_confirmed is False:
        blocked_probe = await _invoke(
            service,
            steps,
            "robot_navigate_to",
            {**args, "idempotency_key": f"eval-{spec['id']}-probe"},
        )
        motion_blocked = (blocked_probe.get("error") or {}).get("code") == "robot_busy"
    state = await _invoke(service, steps, "robot_get_state", {})
    pose = state.get("pose")
    at_goal = (
        pose is not None
        and hypot(
            pose["x"] - spec["goal"]["x"],
            pose["y"] - spec["goal"]["y"],
        )
        <= scenario["goal_tolerance_m"]
    )
    task_success = status == "succeeded" and at_goal
    expected = spec["expected"]
    contract_pass = all(
        (
            status == expected["status"],
            stop_confirmed is expected["stop_confirmed"],
            error_code == expected["error_code"],
            motion_blocked is expected["motion_blocked"],
            feedback_class == expected["feedback_class"],
            task_success == (status == "succeeded"),
        )
    )
    failure_type = (
        "none"
        if task_success and feedback_class == "normal"
        else "feedback_stall"
        if task_success and feedback_class == "sparse"
        else error_code or "unknown_failure"
    )
    result = {
        "trial_id": spec["id"],
        "fault": spec["fault"],
        "operation": spec["operation"],
        "status": status,
        "stop_confirmed": stop_confirmed,
        "error_code": error_code,
        "feedback_count": feedback_count,
        "feedback_class": feedback_class,
        "motion_blocked": motion_blocked,
        "blocked_probe": blocked_probe,
        "final_state": state,
        "at_goal": at_goal,
        "task_success": task_success,
        "contract_pass": contract_pass,
        "failure_type": failure_type,
        "elapsed_ms": round((perf_counter() - began) * 1000, 3),
        "receipt": receipt,
        "steps": steps,
    }
    await backend.close()
    return result


def _metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = sorted(item["elapsed_ms"] for item in results)
    count = len(results)
    failures = Counter(item["failure_type"] for item in results if item["failure_type"] != "none")
    return {
        "trial_count": count,
        "task_success_count": sum(item["task_success"] for item in results),
        "task_success_rate": round(sum(item["task_success"] for item in results) / count, 4),
        "contract_pass_count": sum(item["contract_pass"] for item in results),
        "contract_pass_rate": round(sum(item["contract_pass"] for item in results) / count, 4),
        "unresolved_stop_count": sum(item["stop_confirmed"] is False for item in results),
        "failure_types": dict(sorted(failures.items())),
        "latency_ms": {
            "p50": round(median(latencies), 3),
            "p95": latencies[max(0, (95 * count + 99) // 100 - 1)],
        },
    }


async def run_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    scenario = validate_scenario(scenario)
    results = [await _one_trial(scenario, spec) for spec in scenario["trials"]]
    report = {
        "contract_version": REPORT_VERSION,
        "environment": "deterministic_simulator",
        "runtime": {
            "bridge_version": __version__,
            "simulator_contract_version": "simulator.v1",
        },
        "evaluation_run_id": str(uuid4()),
        "created_at": _timestamp(),
        "scenario": scenario,
        "scenario_sha256": _hash(scenario),
        "trials": results,
        "metrics": _metrics(results),
        "replay_signature_sha256": _hash([_signature(item) for item in results]),
        "authority": "simulation_evidence_only",
    }
    report["report_sha256"] = _hash(report)
    return report


def validate_report(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict) or report.get("contract_version") != REPORT_VERSION:
        raise ValueError("Unsupported evaluation report")
    signed = {key: value for key, value in report.items() if key != "report_sha256"}
    if _hash(signed) != report.get("report_sha256"):
        raise ValueError("Evaluation report bytes do not match its digest")
    scenario = validate_scenario(report.get("scenario"))
    if _hash(scenario) != report.get("scenario_sha256"):
        raise ValueError("Evaluation scenario digest mismatch")
    trials = report.get("trials")
    if not isinstance(trials, list) or len(trials) != len(scenario["trials"]):
        raise ValueError("Evaluation trial count mismatch")
    if _hash([_signature(item) for item in trials]) != report.get("replay_signature_sha256"):
        raise ValueError("Evaluation signature mismatch")
    if report.get("metrics") != _metrics(trials):
        raise ValueError("Evaluation metrics do not match trials")
    return report


async def replay_report(report: dict[str, Any]) -> dict[str, Any]:
    original = validate_report(report)
    replayed = await run_scenario(original["scenario"])
    runtime_match = replayed["runtime"] == original.get("runtime")
    matched = (
        runtime_match and replayed["replay_signature_sha256"] == original["replay_signature_sha256"]
    )
    result = {
        "contract_version": REPLAY_VERSION,
        "original_report_sha256": original["report_sha256"],
        "scenario_sha256": original["scenario_sha256"],
        "replayed_report_sha256": replayed["report_sha256"],
        "outcomes_match": matched,
        "runtime_match": runtime_match,
        "original_signature_sha256": original["replay_signature_sha256"],
        "replayed_signature_sha256": replayed["replay_signature_sha256"],
        "limitations": [
            "Wall-clock latency and generated IDs are not deterministic replay invariants.",
            "Simulator results do not establish Nav2/Gazebo or hardware behavior.",
            "SHA-256 detects accidental edits but does not authenticate the report author.",
            "Version labels do not bind source trees or installed dependency bytes.",
        ],
    }
    result["verification_sha256"] = _hash(result)
    return result
