"""Installed-style command-line replay must require out-of-band identities."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "casepacks/long-horizon-recovery-v0/src"
sys.path.insert(0, str(SRC))

from aigc_lite_casepacks.long_horizon_recovery_v0 import digest  # noqa: E402
from aigc_lite_casepacks.long_horizon_recovery_v0.__main__ import (  # noqa: E402
    _checker_source_sha256,
)


def _minimal_input() -> dict:
    goal = {
        "contract": "LongHorizonGoal.v0",
        "protocol": {
            "id": "p", "workspace_id": "w", "version": 1, "status": "frozen",
            "purpose": "Preserve the goal", "scope": "one test run",
            "completion_predicate": "External review required",
            "stop_conditions": ["budget exhausted"],
            "budget": {"max_steps": 5, "max_wall_seconds": 100},
        },
        "target": "candidate-only replay",
        "policy": {
            "milestones": [], "evidence_criteria": [], "resource_keys": [],
            "critical_observations": [], "freshness_seconds": 10,
            "stagnation_step_limit": 3, "forbidden_actions": ["grant-authority"],
        },
    }
    ledger = {
        "contract": "LongHorizonLedger.v0", "workspace_id": "w",
        "run": {
            "id": "r", "workspace_id": "w", "protocol_id": "p",
            "protocol_version": 1, "goal_sha256": digest(goal),
            "started_at": "2026-10-14T00:00:00+00:00", "status": "running",
            "status_at": "2026-10-14T00:00:00+00:00",
        },
        "steps": [{
            "id": "s", "run_id": "r", "sequence": 1, "kind": "research",
            "status": "succeeded", "finished_at": "2026-10-14T00:00:10+00:00",
        }],
        "receipts": [], "artifacts": [], "scheduler_firings": [],
    }
    return {
        "goal": goal, "ledger": ledger,
        "checkpoints": {
            "previous_sequence": 0, "current_sequence": 1,
            "previous_at": "2026-10-14T00:00:01+00:00",
            "current_at": "2026-10-14T00:00:11+00:00",
        },
    }


def test_cli_replay_checks_source_input_and_result_pins(tmp_path: Path) -> None:
    data = _minimal_input()
    input_path = tmp_path / "replay.json"
    input_path.write_text(json.dumps(data), encoding="utf-8")
    package = "aigc_lite_casepacks.long_horizon_recovery_v0"
    contract_bytes = files(package).joinpath("contract_v0.json").read_bytes()
    command = [
        sys.executable, "-m", package, "--input", str(input_path),
        "--goal-sha256", digest(data["goal"]),
        "--ledger-sha256", digest(data["ledger"]),
        "--checkpoints-sha256", digest(data["checkpoints"]),
        "--checker-sha256", _checker_source_sha256(),
        "--contract-sha256", hashlib.sha256(contract_bytes).hexdigest(),
    ]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(SRC)
    passed = subprocess.run(command, cwd=tmp_path, env=environment,
                            capture_output=True, text=True, check=False)
    assert passed.returncode == 0, passed.stderr
    bundle = json.loads(passed.stdout)
    assert bundle["contract"] == "LongHorizonRecoveryReplay.v0"
    assert bundle["recovery_decision"]["state"] == "PROPOSED"
    assert bundle["recovery_decision"]["execution_authorized"] is False

    wrong_result = subprocess.run(
        [*command, "--expected-result-sha256", "0" * 64], cwd=tmp_path,
        env=environment, capture_output=True, text=True, check=False,
    )
    assert wrong_result.returncode == 2
    assert wrong_result.stdout == ""
    assert "Result digest mismatch" in wrong_result.stderr

    wrong_source = command.copy()
    wrong_source[wrong_source.index("--checker-sha256") + 1] = "0" * 64
    rejected = subprocess.run(wrong_source, cwd=tmp_path, env=environment,
                              capture_output=True, text=True, check=False)
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert "Checker source digest mismatch" in rejected.stderr

    altered = dict(data)
    altered["checkpoints"] = dict(data["checkpoints"])
    altered["checkpoints"]["current_at"] = "2026-10-14T00:00:12+00:00"
    input_path.write_text(json.dumps(altered), encoding="utf-8")
    rejected_checkpoint = subprocess.run(
        command, cwd=tmp_path, env=environment,
        capture_output=True, text=True, check=False,
    )
    assert rejected_checkpoint.returncode == 2
    assert rejected_checkpoint.stdout == ""
    assert "Pinned checkpoints digest mismatch" in rejected_checkpoint.stderr
