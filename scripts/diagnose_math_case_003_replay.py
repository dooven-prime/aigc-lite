"""Print bounded Comparator diagnostics inside the case-003 sandbox.

This is an operator diagnostic, not a verification attempt or qualification
receipt. The authoritative replay must still use ``math-case-003 replay``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.kernel_verification.math_project import _run  # noqa: E402
from app.adapters.kernel_verification.math_project_sandbox import (  # noqa: E402
    bubblewrap_math_project_args,
)
from app.adapters.research_import.math_case_003 import CONFIG_PATH  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "project-root", "lake", "lean", "comparator-root", "comparator",
        "landrun", "lean4export",
    ):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--dependency-scratch-root", type=Path)
    parser.add_argument("--patched-closure-root", type=Path)
    parser.add_argument("--build-work-root", type=Path)
    args = parser.parse_args()
    project = args.project_root.resolve(strict=True)
    policy = bubblewrap_math_project_args(
        project,
        lake_executable=args.lake,
        lean_executable=args.lean,
        comparator_root=args.comparator_root,
        comparator_executable=args.comparator,
        landrun_executable=args.landrun,
        lean4export_executable=args.lean4export,
        dependency_scratch_root=args.dependency_scratch_root,
        patched_closure_root=args.patched_closure_root,
        build_work_root=args.build_work_root,
    )
    command = (
        "/usr/bin/bwrap", *policy, str(args.lake.resolve(strict=True)), "env",
        str(args.comparator.resolve(strict=True)), CONFIG_PATH.removeprefix("lean/"),
    )
    code, stdout, stderr = asyncio.run(_run(command, project / "lean", args.timeout_seconds))
    print(f"Comparator exit code: {code}")
    print("--- stdout (last 20000 characters) ---")
    print(stdout.decode("utf-8", errors="replace")[-20_000:])
    print("--- stderr (last 20000 characters) ---")
    print(stderr.decode("utf-8", errors="replace")[-20_000:])
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
