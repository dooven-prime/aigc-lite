"""One bounded, operator-run Nav2/Gazebo graph acceptance path.

This module never launches hardware or grants an Agent physical authority. Run
it only against the documented isolated TurtleBot3 simulation ROS domain.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from math import hypot
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from . import __version__
from .contracts import ExecutionEnvironment, Pose2D
from .nav2 import Nav2Backend
from .nav2_graph import SimulationGraphProbe
from .service import RobotCapabilityService

CONTRACT = "robot.nav2-graph-eval.v1"
START = Pose2D(-2.0, -0.5, 0.0, frame_id="map")
BASELINE_GOAL = Pose2D(-1.5, -0.5, 0.0, frame_id="map")
DISTANT_GOAL = Pose2D(-0.5, -0.5, 0.0, frame_id="map")


def _utc() -> str:
    return datetime.now(UTC).isoformat()


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _resource_hashes() -> dict[str, str]:
    if os.getenv("ROS_DISTRO") != "jazzy":
        raise ValueError("This acceptance scenario is pinned to ROS 2 Jazzy")
    root = Path("/opt/ros/jazzy/share")
    names = (
        "nav2_bringup/maps/tb3_sandbox.yaml",
        "nav2_bringup/maps/tb3_sandbox.pgm",
        "nav2_bringup/params/nav2_params.yaml",
        "nav2_minimal_tb3_sim/worlds/tb3_sandbox.sdf.xacro",
        "nav2_minimal_tb3_sim/urdf/gz_waffle.sdf.xacro",
    )
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}


def _bridge_source_hashes() -> dict[str, str]:
    root = Path(__file__).parent
    names = ("nav2.py", "nav2_graph.py", "nav2_graph_eval.py", "service.py")
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}


def _metrics(trials: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "trial_count": len(trials),
        "passed_count": sum(item["passed"] for item in trials),
        "succeeded_count": sum(item["receipt"]["status"] == "succeeded" for item in trials),
        "cancelled_count": sum(item["receipt"]["status"] == "cancelled" for item in trials),
        "unresolved_stop_count": sum(
            item["receipt"]["stop_confirmed"] is not True for item in trials
        ),
        "max_observed_stop_drift_m": max(
            (item["stop_observation"]["drift_m"] for item in trials if "stop_observation" in item),
            default=None,
        ),
    }


async def _invoke(
    service: RobotCapabilityService,
    steps: list[dict[str, Any]],
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    started = _utc()
    began = monotonic()
    result = await service.invoke(name, arguments)
    steps.append(
        {
            "step_id": str(uuid4()),
            "sequence": len(steps) + 1,
            "capability": name,
            "arguments": arguments,
            "started_at": started,
            "completed_at": _utc(),
            "elapsed_ms": round((monotonic() - began) * 1000, 3),
            "failed": result.failed,
            "result": result.payload,
        }
    )
    return result.payload


def _require_receipt(receipt: dict[str, Any], *, status: str, phase: str) -> None:
    if (
        receipt.get("contract_version") != "robot.action-receipt.v1"
        or receipt.get("environment") != "simulation"
        or receipt.get("status") != status
        or receipt.get("stop_confirmed") is not True
        or not receipt.get("provider_action_id")
    ):
        raise RuntimeError(f"{phase} did not produce a confirmed Nav2 {status} receipt")


async def _stable_stop(backend: Nav2Backend, *, max_drift_m: float = 0.25) -> dict:
    before = await backend.get_state()
    await asyncio.sleep(1.0)
    after = await backend.get_state()
    if before.pose is None or after.pose is None:
        raise RuntimeError("Stop verification requires two TF poses")
    drift = hypot(after.pose.x - before.pose.x, after.pose.y - before.pose.y)
    result = {
        "first": before.as_dict(),
        "second": after.as_dict(),
        "drift_m": round(drift, 4),
        "max_drift_m": max_drift_m,
        "active_action_cleared": after.active_action_id is None,
    }
    if drift > max_drift_m or after.active_action_id is not None:
        raise RuntimeError("TF does not confirm a stable stop after Nav2 cancellation")
    return result


async def _wait_for_action(backend: Nav2Backend, timeout_seconds: float) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        state = await backend.get_state()
        if state.active_action_id is not None:
            return
        await asyncio.sleep(0.1)
    raise RuntimeError("Nav2 did not accept the evaluation goal before the deadline")


async def run_graph_acceptance(*, execute_motion: bool, domain_id: int) -> dict[str, Any]:
    """Preflight first; motion needs an explicit flag and isolated ROS domain."""

    if not 1 <= domain_id <= 232 or os.getenv("ROS_DOMAIN_ID") != str(domain_id):
        raise ValueError("An explicit non-default ROS_DOMAIN_ID must match --domain-id")
    if os.getenv("ROS_AUTOMATIC_DISCOVERY_RANGE") != "LOCALHOST" or os.getenv("ROS_STATIC_PEERS"):
        raise ValueError("LOCALHOST discovery without ROS_STATIC_PEERS is required")
    if os.getenv("AIGC_LITE_ROS2_ENVIRONMENT") != "simulation":
        raise ValueError("AIGC_LITE_ROS2_ENVIRONMENT=simulation is required")
    report: dict[str, Any] = {
        "contract_version": CONTRACT,
        "evaluation_run_id": str(uuid4()),
        "created_at": _utc(),
        "environment": "nav2_gazebo_simulation",
        "authority": "simulation_evidence_only",
        "bridge_version": __version__,
        "bridge_source_hashes": _bridge_source_hashes(),
        "ros_distro": "jazzy",
        "ros_domain_id": domain_id,
        "map_hashes": _resource_hashes(),
        "fixed_start": START.as_dict(),
        "fixed_goals": {
            "baseline": BASELINE_GOAL.as_dict(),
            "cancel_and_timeout": DISTANT_GOAL.as_dict(),
        },
        "graph": None,
        "trials": [],
        "steps": [],
        "metrics": {},
        "status": "blocked",
        "blocker": None,
    }
    backend = Nav2Backend(
        robot_id="tb3-sandbox-eval",
        environment=ExecutionEnvironment.SIMULATION,
        map_id="tb3_sandbox",
    )
    probe = SimulationGraphProbe(backend)
    service = RobotCapabilityService(backend)
    try:
        report["graph"] = await probe.prepare(START, timeout_seconds=90)
        pose = report["graph"]["observed_pose"]
        if hypot(pose["x"] - START.x, pose["y"] - START.y) > 0.75:
            raise RuntimeError("Observed robot pose does not match the fixed simulation start")
        if not execute_motion:
            report["status"] = "ready"
            return report

        async def navigate(goal: Pose2D, key: str, timeout: float) -> dict:
            return await _invoke(
                service,
                report["steps"],
                "robot_navigate_to",
                {
                    "idempotency_key": key,
                    "x": goal.x,
                    "y": goal.y,
                    "yaw": goal.yaw,
                    "frame_id": goal.frame_id,
                    "action_timeout_seconds": timeout,
                },
            )

        prefix = report["evaluation_run_id"]
        baseline = await navigate(BASELINE_GOAL, f"{prefix}-baseline", 90)
        _require_receipt(baseline, status="succeeded", phase="baseline")
        after_baseline = await backend.get_state()
        if (
            after_baseline.pose is None
            or hypot(
                after_baseline.pose.x - BASELINE_GOAL.x,
                after_baseline.pose.y - BASELINE_GOAL.y,
            )
            > 0.3
        ):
            raise RuntimeError("Nav2 success did not match the TF goal tolerance")
        report["trials"].append(
            {
                "name": "baseline",
                "receipt": baseline,
                "goal_tolerance_m": 0.3,
                "final_state": after_baseline.as_dict(),
                "passed": True,
            }
        )

        cancel_key = f"{prefix}-cancel"
        task = asyncio.create_task(navigate(DISTANT_GOAL, cancel_key, 90))
        try:
            await _wait_for_action(backend, 15)
            await asyncio.sleep(0.5)
            cancelled = await _invoke(
                service,
                report["steps"],
                "robot_cancel_action",
                {
                    "idempotency_key": cancel_key,
                    "timeout_seconds": 10,
                },
            )
            await asyncio.wait_for(task, timeout=15)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        _require_receipt(cancelled, status="cancelled", phase="explicit cancellation")
        cancel_stop = await _stable_stop(backend)
        report["trials"].append(
            {
                "name": "cancel",
                "receipt": cancelled,
                "stop_observation": cancel_stop,
                "passed": True,
            }
        )

        timed_out = await navigate(DISTANT_GOAL, f"{prefix}-timeout", 1)
        _require_receipt(timed_out, status="cancelled", phase="action timeout")
        if (timed_out.get("error") or {}).get("code") != "action_timeout":
            raise RuntimeError("The timeout trial did not record its cancellation cause")
        timeout_stop = await _stable_stop(backend)
        report["trials"].append(
            {
                "name": "timeout",
                "receipt": timed_out,
                "declared_timeout_seconds": 1,
                "stop_observation": timeout_stop,
                "passed": True,
            }
        )
        report["status"] = "passed"
    except Exception as exc:  # noqa: BLE001 - preserve a blocked, auditable report
        report["blocker"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        shutdown_errors = []
        try:
            probe.close()
        except Exception as exc:  # noqa: BLE001 - shutdown uncertainty is evidence
            shutdown_errors.append(f"probe: {type(exc).__name__}: {exc}")
        try:
            await backend.close()
        except Exception as exc:  # noqa: BLE001 - shutdown uncertainty is evidence
            shutdown_errors.append(f"backend: {type(exc).__name__}: {exc}")
        if shutdown_errors:
            report["status"] = "blocked"
            report["blocker"] = {
                "type": "shutdown_uncertain",
                "message": "; ".join(shutdown_errors),
            }
        report["completed_at"] = _utc()
        report["metrics"] = _metrics(report["trials"])
        report["report_sha256"] = _digest(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain-id", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execute-motion", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; evaluation evidence is never overwritten")
    report = asyncio.run(
        run_graph_acceptance(execute_motion=args.execute_motion, domain_id=args.domain_id)
    )
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "report": str(args.output.absolute())}))
    if report["status"] not in {"ready", "passed"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
