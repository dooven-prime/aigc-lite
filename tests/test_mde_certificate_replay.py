"""Frozen synthetic inputs replay full candidate certificates, not just hashes."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from aigc_lite_casepacks.mde_v0.certificate_common import (
    CERTIFICATE_CONTRACT_SHA256,
    parse_certificate,
)
from aigc_lite_casepacks.mde_v0.certificate_replay import (
    parse_replay_input,
    replay_input,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "casepacks/mde-v0/fixtures/certificate-replay-v0"
LOCK = json.loads((FIXTURES / "replay-lock.json").read_text(encoding="utf-8"))


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _ablations(certificate: dict) -> dict[str, dict]:
    return {
        item.get("unit_id", item.get("evidence_unit", {}).get("sample_id")): item
        for item in certificate["ablations"]
    }


@pytest.mark.parametrize("entry", LOCK["cases"], ids=lambda item: item["input_file"])
def test_frozen_input_replays_exact_candidate_certificate(entry: dict) -> None:
    raw = (FIXTURES / entry["input_file"]).read_bytes()
    assert _sha256(raw) == entry["input_sha256"]
    assert parse_replay_input(raw)["case"] == entry["case"]
    encoded = replay_input(
        raw,
        expected_input_sha256=entry["input_sha256"],
        expected_certificate_json_sha256=entry["certificate_json_sha256"],
    )
    assert _sha256(encoded) == entry["certificate_json_sha256"]
    certificate = parse_certificate(encoded)
    assert certificate["certificate_sha256"] == entry["certificate_field_sha256"]
    assert certificate["certificate_contract_sha256"] == CERTIFICATE_CONTRACT_SHA256
    assert certificate["certificate_kind"] == entry["case"]
    assert certificate["candidate_only"] is True
    assert certificate["qualification_granted"] is False
    assert certificate["current_use_bound"] is False
    assert certificate["authorization_granted"] is False
    assert certificate["unverified_assumptions"]

    status = (
        certificate["decision"]["status"]
        if "decision" in certificate
        else certificate["baseline"]["diagnostic"]["status"]
    )
    assert status == entry["expected_status"]
    actual = _ablations(certificate)
    assert {unit: item["classification"] for unit, item in actual.items()} == (
        entry["expected_ablation_classification"]
    )
    for unit, item in actual.items():
        if item["classification"] in {
            "NECESSARY_IN_THIS_BUNDLE", "REDUNDANT_IN_THIS_BUNDLE"
        }:
            assert item["diagnostic"] is not None, unit
            assert item["verification"].startswith("SIGNED_EXACT_"), unit
            assert (
                item.get("ablation_context_authenticated") is False
                if "decision" in certificate
                else item.get("origin_label_authenticated") is False
            ), unit
        else:
            assert item["classification"] == "UNTESTED", unit
    if "decision" in certificate:
        assert certificate["decision"]["finite_model"]["model_nonempty"] is True
        assert any("physical" in text for text in certificate["unverified_assumptions"])
    else:
        assert certificate["baseline"]["conditional_finite_model"]["model_nonempty"]
        assert any("not independently" in text for text in certificate["unverified_assumptions"])


def test_lock_pins_replay_code_and_version_without_pretending_to_authenticate_it() -> None:
    source = ROOT / (
        "casepacks/mde-v0/src/aigc_lite_casepacks/mde_v0/certificate_replay.py"
    )
    assert _sha256(source.read_bytes()) == LOCK["certificate_replay_source_sha256"]
    assert LOCK["certificate_contract_sha256"] == CERTIFICATE_CONTRACT_SHA256
    assert LOCK["casepack_research_version"] == "0.2.0.dev0"
    assert "not independently authenticate" in LOCK["trust_boundary"]


def test_replay_requires_external_input_and_certificate_expectations() -> None:
    entry = LOCK["cases"][0]
    raw = (FIXTURES / entry["input_file"]).read_bytes()
    with pytest.raises(ValueError, match="input SHA-256 does not match"):
        replay_input(
            raw,
            expected_input_sha256="0" * 64,
            expected_certificate_json_sha256=entry["certificate_json_sha256"],
        )
    with pytest.raises(ValueError, match="certificate JSON SHA-256 does not match"):
        replay_input(
            raw,
            expected_input_sha256=entry["input_sha256"],
            expected_certificate_json_sha256="0" * 64,
        )
    forged = {**json.loads(raw), "claimed_status": "PASSED"}
    forged_raw = json.dumps(forged, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ValueError, match="invalid field set"):
        replay_input(
            forged_raw,
            expected_input_sha256=_sha256(forged_raw),
            expected_certificate_json_sha256=entry["certificate_json_sha256"],
        )


def test_source_replay_cli_from_outside_checkout_has_only_canonical_stdout(
    tmp_path: Path,
) -> None:
    entry = LOCK["cases"][1]
    fixture = FIXTURES / entry["input_file"]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT / "casepacks/mde-v0/src"), str(ROOT))
    )
    command = [
        sys.executable, "-m", "aigc_lite_casepacks.mde_v0.certificate_replay",
        str(fixture), "--expected-input-sha256", entry["input_sha256"],
        "--expected-certificate-json-sha256", entry["certificate_json_sha256"],
    ]
    result = subprocess.run(
        command, cwd=tmp_path, env=environment, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stderr == b""
    assert _sha256(result.stdout) == entry["certificate_json_sha256"]
    assert result.stdout == json.dumps(
        parse_certificate(result.stdout), sort_keys=True,
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    bad = subprocess.run(
        [*command[:-1], "0" * 64], cwd=tmp_path, env=environment,
        capture_output=True, check=False,
    )
    assert bad.returncode != 0
    assert bad.stdout == b""


def test_duplicate_and_deep_input_fail_closed_before_domain_replay() -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_replay_input(b'{"case":"a","case":"b"}')
    nested: object = []
    for _ in range(80):
        nested = [nested]
    raw = json.dumps({"replay_version": "mde.certificate-replay.v0", "case": nested}).encode()
    with pytest.raises(ValueError, match="structure limits"):
        parse_replay_input(raw)


def test_replay_refuses_excessive_citation_ablations() -> None:
    entry = LOCK["cases"][1]
    request = json.loads((FIXTURES / entry["input_file"]).read_bytes())
    request["citation"]["reduced_lineage_by_unit"] = {
        f"unit-{index}": {} for index in range(33)
    }
    raw = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ValueError, match="bounded object"):
        replay_input(
            raw,
            expected_input_sha256=_sha256(raw),
            expected_certificate_json_sha256=entry["certificate_json_sha256"],
        )
