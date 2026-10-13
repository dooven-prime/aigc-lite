"""Regression for the source-pinned, same-checker RIME replay revision."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from scripts.verify_rime_replay_package import verify_before_execution
from scripts.verify_rime_replay_zip import safely_extract_delivery

ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "replay_packages" / "rime-consumer-r2.zip"
HOST_RUNNER = ROOT / "scripts" / "verify_rime_replay_package.py"
ZIP_SHA256 = "11f6c61f430e5adedbfc488b02765d146ca652afa277e4d952988e10702c84c6"
MANIFEST_SHA256 = "fd75add881becc2e37ceb79ed5999200132d90ed958f2ddb66df637b30ae46f6"


def _package_copy(tmp_path: Path) -> Path:
    result = safely_extract_delivery(DELIVERY, tmp_path / "unpacked", ZIP_SHA256, MANIFEST_SHA256)
    return Path(result["package_path"])


def _run_witness(package: Path, witness: dict) -> dict:
    code = "import json,sys; from router import run_witness; print(json.dumps(run_witness(json.load(sys.stdin))))"
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code],
        input=json.dumps(witness), text=True, encoding="utf-8",
        capture_output=True, cwd=package / "checker", check=True,
    )
    return json.loads(completed.stdout)


def _host_run(package: Path, digest: str, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(HOST_RUNNER), "--package", str(package),
         "--expected-manifest-sha256", digest, "--out", str(output)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )


def test_pinned_package_replays_with_host_receipt(tmp_path: Path) -> None:
    package = _package_copy(tmp_path)
    digest, manifest = verify_before_execution(package, MANIFEST_SHA256)
    assert digest == MANIFEST_SHA256
    assert manifest["checker_revision"] == "r2"
    output = tmp_path / "receipt.json"
    completed = _host_run(package, digest, output)
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["manifest_sha256"] == digest
    assert receipt["report"]["verification"]["manifest_sha256"] == digest
    assert receipt["report"]["summary"]["positive_pass_count"] == 3
    assert receipt["report"]["summary"]["negative_control_match_count"] == 7
    assert "no independent validation" in receipt["scope"]


def test_tampered_checker_never_executes(tmp_path: Path) -> None:
    package = _package_copy(tmp_path)
    marker = tmp_path / "executed"
    runner = package / "checker" / "run.py"
    runner.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    completed = _host_run(package, MANIFEST_SHA256, tmp_path / "receipt.json")
    assert completed.returncode != 0
    assert "mismatch" in completed.stderr
    assert not marker.exists()
    assert not (tmp_path / "receipt.json").exists()


def test_expected_reason_is_part_of_replay_contract(tmp_path: Path) -> None:
    package = _package_copy(tmp_path)
    expected_path = package / "expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["negative_controls"][0]["expected"]["reason"] = "not-the-real-failure"
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["path"] == "expected.json":
            raw = expected_path.read_bytes()
            entry["size"] = len(raw)
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    completed = _host_run(package, digest, tmp_path / "receipt.json")
    assert completed.returncode != 0
    assert "replay exited 1" in completed.stderr
    assert not (tmp_path / "receipt.json").exists()


def test_complete_report_and_ordered_ports_are_checked(tmp_path: Path) -> None:
    package = _package_copy(tmp_path)
    witness = json.loads((package / "examples" / "witness_with_reports.json").read_text(encoding="utf-8"))
    assert _run_witness(package, witness)["verdict"] == "PASS"
    mutations = {
        "partition": lambda report: report["partition"][0]["block"].append(4),
        "selected_block": lambda report: report.update(selected_block=[0]),
        "origin": lambda report: report["origin"].update(ports=[6, 0], input_blocks=[[6], [0]]),
        "absorptions": lambda report: report["absorptions"].append({
            "letter": "d", "carrier_port": 0, "carrier_before": [0],
            "other_port": 6, "other_block": [6], "collision_image": 0,
            "carrier_after": [0, 6],
        }),
        "carrier": lambda report: report["carrier"].update(q=1),
    }
    for field, mutate in mutations.items():
        changed = copy.deepcopy(witness)
        mutate(changed["candidate_reports"][0])
        result = _run_witness(package, changed)
        assert result["verdict"] != "PASS", (field, result)
        if field != "partition":  # malformed blocks are invalid before semantic comparison
            assert field in (result["failure_reason"] or ""), (field, result)


def test_absorption_and_command_guard_are_distinct(tmp_path: Path) -> None:
    package = _package_copy(tmp_path)
    witness = json.loads((package / "examples" / "absorb_witness.json").read_text(encoding="utf-8"))
    result = _run_witness(package, witness)
    assert result["verdict"] == "PASS"
    assert len(result["route_summaries"]["log_oracle"]["absorptions"]) == 1
    wrong = copy.deepcopy(witness)
    wrong["commands"][0]["kind"] = "Carry"
    result = _run_witness(package, wrong)
    assert result["verdict"] == "FAIL"
    assert "Carry guard fails" in result["failure_reason"]


def test_unrelated_carry_fusion_updates_full_history_without_absorption(tmp_path: Path) -> None:
    package = _package_copy(tmp_path)
    witness = json.loads((package / "examples" / "witness.json").read_text(encoding="utf-8"))
    result = _run_witness(package, witness)
    assert result["verdict"] == "PASS"
    prefix = result["steps"][len(witness["prefix"])]["routes"]["log_oracle"]
    final = result["route_summaries"]["log_oracle"]
    assert final["fusion_event_count"] == prefix["fusion_event_count"] + 1
    assert final["partition"] != prefix["partition"]
    assert final["absorptions"] == prefix["absorptions"] == []


def test_malformed_environment_is_invalid_request_not_checker_crash(tmp_path: Path) -> None:
    package = _package_copy(tmp_path)
    witness = json.loads((package / "examples" / "witness.json").read_text(encoding="utf-8"))
    witness["environment"]["ufe_enumeration"][0] = []
    result = _run_witness(package, witness)
    assert result["verdict"] == "INVALID_REQUEST"
    witness["environment"]["ufe_enumeration"][0] = 0
    witness["environment"]["d"][6] = 1
    result = _run_witness(package, witness)
    assert result["verdict"] == "INVALID_REQUEST"


def test_single_witness_cli_uses_expected_entry(tmp_path: Path) -> None:
    package = _package_copy(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-B", str(package / "checker" / "run.py"),
         "--package", str(package), "--witness", "examples/witness.json"],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["summary"]["witness_count"] == 1
    assert report["results"][0]["expected_match"] is True
