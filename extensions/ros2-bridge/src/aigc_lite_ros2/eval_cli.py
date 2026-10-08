"""Run and replay bounded simulator evaluation scenarios."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .evaluation import (
    load_bundled_scenario,
    load_scenario,
    replay_report,
    run_scenario,
)


def _write_new(path: Path, value: dict) -> None:
    # An evaluation never silently replaces an earlier report or receipt.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "replay"))
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "run":
        if args.report is not None:
            parser.error("run forbids --report")
        scenario = load_scenario(args.scenario) if args.scenario else load_bundled_scenario()
        result = asyncio.run(run_scenario(scenario))
    else:
        if args.report is None or args.scenario is not None:
            parser.error("replay requires --report and forbids --scenario")
        result = asyncio.run(replay_report(json.loads(args.report.read_text(encoding="utf-8"))))
    _write_new(args.output, result)
    print(
        json.dumps(
            {
                "output": str(args.output.absolute()),
                "status": ("matched" if result.get("outcomes_match") else "mismatch")
                if args.action == "replay"
                else ("passed" if result["metrics"]["contract_pass_rate"] == 1 else "failed"),
            }
        )
    )
    if args.action == "replay" and not result["outcomes_match"]:
        raise SystemExit(1)
    if args.action == "run" and result["metrics"]["contract_pass_rate"] != 1:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
