"""Same-checker finite replay; invoke through the host-owned verifier.

This entry point's self-check is diagnostic. It cannot authenticate its own
source before Python starts executing it. No authority is granted by PASS.
"""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path

from verifier import verify_package

SCHEMA = "rime.consumer-replay.cross-host-report.v2"
SCOPE = (
    "SAME-CHECKER CROSS-HOST REPLAY; not independent implementation validation, "
    "institutional audit, qualification, or current-use admission."
)


def main() -> int:
    parser = argparse.ArgumentParser(prog="rime-consumer-replay")
    parser.add_argument("--package", type=Path, default=Path("."))
    parser.add_argument("--witness", help="one expected.json-listed witness path")
    args = parser.parse_args()
    package = args.package.resolve()
    verification = verify_package(package)
    if not verification["ok"]:
        print(json.dumps({
            "schema": SCHEMA, "scope": SCOPE, "verification": verification,
            "summary": {"all_expected_match": False}, "results": [],
        }, sort_keys=True))
        return 1

    # The host verifier checked all package bytes before this process began.
    # Keep this import after the in-package diagnostic check as defense in depth.
    from router import run_witness

    expected_manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    expected = json.loads((package / "expected.json").read_text(encoding="utf-8"))
    entries = [
        ("positive", entry) for entry in expected["positive_witnesses"]
    ] + [
        ("negative", entry) for entry in expected["negative_controls"]
    ]
    listed = {item["path"] for item in expected_manifest["files"]}
    for _, entry in entries:
        rel = entry.get("path")
        if (not isinstance(rel, str) or rel not in listed
                or not rel.endswith(".json")
                or not rel.startswith(("examples/", "negative_controls/"))):
            raise ValueError(f"expected.json references an unlisted witness: {rel!r}")
    if args.witness:
        entries = [(kind, entry) for kind, entry in entries if entry["path"] == args.witness]
        if len(entries) != 1:
            parser.error("--witness must name exactly one expected.json-listed witness")

    results = []
    for kind, entry in entries:
        path = package.joinpath(*entry["path"].split("/"))
        witness = json.loads(path.read_text(encoding="utf-8"))
        replay = run_witness(witness)
        expected_verdict = entry["expected"]["verdict"]
        expected_reason = entry["expected"].get("reason")
        reason_match = expected_reason is None or expected_reason in (replay["failure_reason"] or "")
        results.append({
            "witness_kind": kind, "witness_path": entry["path"],
            "witness_sha256": next(x["sha256"] for x in expected_manifest["files"] if x["path"] == entry["path"]),
            "expected_verdict": expected_verdict, "expected_reason": expected_reason,
            "reason_match": reason_match,
            "expected_match": replay["verdict"] == expected_verdict and reason_match,
            "replay": replay,
        })

    all_match = bool(results) and all(item["expected_match"] for item in results)
    report = {
        "schema": SCHEMA, "scope": SCOPE,
        "replay_kind": "SAME_CHECKER_CROSS_HOST_REPLAY",
        "package_dir": str(package), "verification": verification,
        "environment": {
            "python": platform.python_version(), "platform": platform.platform(),
            "implementation": platform.python_implementation(),
        },
        "results": results,
        "summary": {
            "verification_passed": True,
            "all_expected_match": all_match,
            "witness_count": len(results),
            "positive_pass_count": sum(
                item["witness_kind"] == "positive" and item["replay"]["verdict"] == "PASS"
                for item in results
            ),
            "negative_control_match_count": sum(
                item["witness_kind"] == "negative" and item["expected_match"]
                for item in results
            ),
        },
    }
    print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    return 0 if all_match else 1


if __name__ == "__main__":
    raise SystemExit(main())
