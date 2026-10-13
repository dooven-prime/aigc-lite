from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

from reference_oracles.rime_consumer_origin_v0 import replay
from scripts import compare_rime_reference_oracle as comparison

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "replay_packages" / "rime-consumer-v1-r2"


def test_reference_oracle_has_no_r2_imports() -> None:
    source = (ROOT / "reference_oracles" / "rime_consumer_origin_v0.py").read_text()
    imports = [
        node.module for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    ]
    assert set(imports) == {"__future__", "collections", "typing"}


def test_frozen_and_predeclared_finite_cases_agree() -> None:
    result = comparison.compare_all()
    assert result["r2_cases"] == 10
    assert result["predeclared_cases"] == 4
    assert result["all_agree"] is True
    assert all(item["first_difference"] is None for item in result["results"])


def test_unrelated_fusion_updates_partition_without_selected_absorption() -> None:
    witness = json.loads((PACKAGE / "examples" / "witness.json").read_text())
    result = replay(witness)
    assert result["verdict"] == "PASS"
    before, after = result["states"][-2:]
    assert before["partition"] != after["partition"]
    assert before["origin"] == after["origin"]
    assert before["absorptions"] == after["absorptions"] == []
    assert before["carrier"] == after["carrier"]


def test_first_divergence_retains_both_raw_states(monkeypatch) -> None:
    genuine = comparison.replay

    def corrupted(value):
        result = copy.deepcopy(genuine(value))
        if len(result["states"]) > 1 and result["states"][1]["carrier"] is not None:
            result["states"][1]["carrier"]["q"] = 999
        return result

    monkeypatch.setattr(comparison, "replay", corrupted)
    result = comparison.compare_all()["results"][0]
    assert result["agreed"] is False
    difference = result["first_difference"]
    assert difference["prefix_step"] == 1
    assert difference["reference_raw_state"]["carrier"]["q"] == 999
    assert difference["r2_raw_state"]["carrier"]["pos"] == 0
