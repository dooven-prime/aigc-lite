"""Build or verify the read-only content-addressed case-003 dependency closure."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.kernel_verification.math_project_closure import (  # noqa: E402
    prepare_patched_closure,
    verify_patched_closure,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("project", type=Path)
    parser.add_argument("path", type=Path, help="store directory for prepare, closure path for verify")
    args = parser.parse_args()
    if args.action == "prepare":
        closure = prepare_patched_closure(args.project, args.path)
        print(json.dumps({"closure": str(closure), **verify_patched_closure(args.project, closure)}))
    else:
        print(json.dumps(verify_patched_closure(args.project, args.path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
