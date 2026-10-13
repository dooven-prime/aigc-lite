"""Compare the separate finite origin oracle with the pinned r2 checker.

This harness may import both implementations. The reference oracle itself
must not import r2 code. A matching report is finite implementation agreement,
not independently authenticated provenance or a general mathematical proof.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "replay_packages" / "rime-consumer-r2.zip"
CORPUS = ROOT / "reference_oracles" / "finite_corpus_v0.json"
ZIP_SHA256 = "11f6c61f430e5adedbfc488b02765d146ca652afa277e4d952988e10702c84c6"
MANIFEST_SHA256 = "fd75add881becc2e37ceb79ed5999200132d90ed958f2ddb66df637b30ae46f6"
sys.dont_write_bytecode = True  # the frozen package inventory must stay byte-for-byte clean
sys.path.insert(0, str(ROOT))

from reference_oracles.rime_consumer_origin_v0 import replay  # noqa: E402
from scripts.verify_rime_replay_package import verify_before_execution  # noqa: E402
from scripts.verify_rime_replay_zip import safely_extract_delivery  # noqa: E402


def _r2_property(state: dict[str, Any]) -> dict[str, Any]:
    event_index = state["origin_event_index"]
    event = None if event_index is None else state["fusion_events"][event_index]
    origin = None if event is None else {
        "ports": [event["k_0"], event["k_1"]],
        "input_blocks": [event["b_0"], event["b_1"]],
        "collision_image": event["c"],
        "fresh_block": event["fresh_block"],
    }
    return {
        "partition": [
            {"q": int(q), "block": block}
            for q, block in sorted(state["partition"].items(), key=lambda item: int(item[0]))
        ],
        "origin": origin,
        "absorptions": [
            {"carrier_port": item["carrier_port"], "carrier_before": item["carrier_before"],
             "other_port": item["other_port"], "other_block": item["other_block"],
             "collision_image": item["c"], "carrier_after": item["carrier_after"]}
            for item in state["absorptions"]
        ],
        "carrier": None if state["carrier"] is None else {
            "q": state["carrier"]["pos"], "block": state["carrier"]["block"]
        },
    }


def compare_one(
    case_id: str, witness: dict[str, Any], r2_runner: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    reference = replay(witness)
    r2 = r2_runner(witness)
    first_difference = None
    for index, (ours, theirs) in enumerate(zip(reference["states"], r2["steps"], strict=False)):
        if not theirs["agreed"]:
            first_difference = {
                "prefix_step": index, "label": ours["label"],
                "reason": "r2 internal routes disagree",
                "reference_raw_state": ours, "r2_raw_state": theirs["routes"],
            }
            break
        left = {key: ours[key] for key in ("partition", "origin", "absorptions", "carrier")}
        right = _r2_property(theirs["routes"]["log_oracle"])
        if left != right:
            first_difference = {
                "prefix_step": index, "label": ours["label"],
                "reference_raw_state": ours, "r2_raw_state": theirs["routes"]["log_oracle"],
            }
            break
    if first_difference is None and len(reference["states"]) != len(r2["steps"]):
        first_difference = {
            "prefix_step": min(len(reference["states"]), len(r2["steps"])),
            "reference_raw_state": reference["states"][-1] if reference["states"] else None,
            "r2_raw_state": r2["steps"][-1]["routes"]["log_oracle"] if r2["steps"] else None,
            "reason": "different number of replayed prefixes",
        }
    if first_difference is None and reference["verdict"] != r2["verdict"]:
        first_difference = {
            "prefix_step": len(reference["states"]) - 1,
            "reference_raw_state": reference["states"][-1] if reference["states"] else None,
            "r2_raw_state": r2["steps"][-1]["routes"]["log_oracle"] if r2["steps"] else None,
            "reason": f"verdict differs: {reference['verdict']} vs {r2['verdict']}",
        }
    return {
        "case_id": case_id,
        "reference_verdict": reference["verdict"],
        "r2_verdict": r2["verdict"],
        "prefix_count": len(reference["states"]),
        "agreed": first_difference is None,
        "first_difference": first_difference,
    }


def compare_all() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="rime-origin-v0-") as scratch:
        unpacked = safely_extract_delivery(
            DELIVERY, Path(scratch) / "unpacked", ZIP_SHA256, MANIFEST_SHA256,
        )
        package = Path(unpacked["package_path"])
        verify_before_execution(package, MANIFEST_SHA256)
        checker_path = str(package / "checker")
        sys.path.insert(0, checker_path)
        try:
            r2_runner = importlib.import_module("router").run_witness
            expected = json.loads((package / "expected.json").read_text(encoding="utf-8"))
            entries = expected["positive_witnesses"] + expected["negative_controls"]
            cases = [
                (entry["path"], json.loads((package / entry["path"]).read_text(encoding="utf-8")))
                for entry in entries
            ]
            corpus_bytes = CORPUS.read_bytes()
            corpus = json.loads(corpus_bytes)
            if corpus.get("schema") != "rime.consumer-origin.finite-corpus.v0":
                raise ValueError("unexpected finite corpus schema")
            cases.extend((item["case_id"], item) for item in corpus["witnesses"])
            results = [compare_one(name, witness, r2_runner) for name, witness in cases]
        finally:
            sys.path.remove(checker_path)
    return {
        "schema": "rime.consumer-origin.comparison.v0",
        "scope": "finite independent-code-path comparison; same author/trust domain",
        "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "r2_cases": len(entries),
        "predeclared_cases": len(corpus["witnesses"]),
        "all_agree": all(result["agreed"] for result in results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="optional runtime report path outside source")
    args = parser.parse_args()
    report = compare_all()
    serialized = json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0 if report["all_agree"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
