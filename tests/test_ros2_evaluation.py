"""The robot simulator acceptance report must be replayable and fail closed."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from aigc_lite_ros2.evaluation import (
    load_bundled_scenario,
    load_scenario,
    replay_report,
    run_scenario,
    validate_report,
    validate_scenario,
)

SCENARIO = (
    Path(__file__).resolve().parents[1]
    / "extensions"
    / "ros2-bridge"
    / "src"
    / "aigc_lite_ros2"
    / "scenarios"
    / "navigation_v1.json"
)


def test_simulation_fault_matrix_and_replay() -> None:
    scenario = load_scenario(SCENARIO)
    assert load_bundled_scenario() == scenario
    report = asyncio.run(run_scenario(scenario))
    assert validate_report(report) is report
    assert report["authority"] == "simulation_evidence_only"
    assert report["runtime"]["simulator_contract_version"] == "simulator.v1"
    assert report["metrics"]["trial_count"] == 9
    assert report["metrics"]["contract_pass_rate"] == 1
    assert report["metrics"]["unresolved_stop_count"] == 3
    assert report["metrics"]["failure_types"]["feedback_stall"] == 1
    assert report["metrics"]["task_success_count"] == 2
    by_id = {item["trial_id"]: item for item in report["trials"]}
    assert by_id["baseline"]["at_goal"] is True
    assert by_id["goal-rejection"]["feedback_count"] == 0
    assert by_id["feedback-stall"]["feedback_count"] == 2
    assert by_id["cancel-confirmed"]["status"] == "cancelled"
    assert by_id["cancel-unconfirmed"]["status"] == "indeterminate"
    assert by_id["action-timeout"]["error_code"] == "action_timeout"
    assert by_id["localization-loss"]["final_state"]["localized"] is False
    assert all(item["steps"] for item in report["trials"])
    assert all(
        item["receipt"]["contract_version"] == "robot.action-receipt.v1"
        for item in report["trials"]
    )
    uncertain = [item for item in report["trials"] if item["stop_confirmed"] is False]
    assert all(item["motion_blocked"] for item in uncertain)
    assert all(item["blocked_probe"]["error"]["code"] == "robot_busy" for item in uncertain)
    replay = asyncio.run(replay_report(report))
    assert replay["outcomes_match"] is True
    assert replay["runtime_match"] is True
    assert replay["original_signature_sha256"] == replay["replayed_signature_sha256"]


def test_report_tamper_and_unknown_scenario_field_are_rejected() -> None:
    scenario = load_scenario(SCENARIO)
    report = asyncio.run(run_scenario(scenario))
    report["trials"][0]["status"] = "failed"
    with pytest.raises(ValueError, match="digest"):
        validate_report(report)
    scenario["trials"][0]["unsafe_shell"] = "true"
    with pytest.raises(ValueError, match="unexpected"):
        validate_scenario(scenario)


def test_eval_cli_writes_new_report_and_replay(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    source = project / "extensions" / "ros2-bridge" / "src"
    env = {**os.environ, "PYTHONPATH": str(source)}
    report = tmp_path / "report.json"
    replay = tmp_path / "replay.json"
    run = subprocess.run(
        (
            sys.executable,
            "-m",
            "aigc_lite_ros2.eval_cli",
            "run",
            "--output",
            str(report),
        ),
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stderr
    checked = subprocess.run(
        (
            sys.executable,
            "-m",
            "aigc_lite_ros2.eval_cli",
            "replay",
            "--report",
            str(report),
            "--output",
            str(replay),
        ),
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert checked.returncode == 0, checked.stderr
    assert json.loads(replay.read_text())["outcomes_match"] is True
    repeated = subprocess.run(
        (
            sys.executable,
            "-m",
            "aigc_lite_ros2.eval_cli",
            "run",
            "--output",
            str(report),
        ),
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert repeated.returncode != 0
    assert "FileExistsError" in repeated.stderr
