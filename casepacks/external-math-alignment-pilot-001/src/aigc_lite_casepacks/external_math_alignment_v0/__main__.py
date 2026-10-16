"""Offline CLI for the development-only source-bound alignment diagnosis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import InputIdentityError, audit_sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manuscript-pdf", type=Path, required=True)
    parser.add_argument("--formal-source", type=Path, required=True)
    parser.add_argument(
        "--expected-lock-sha256",
        required=True,
        help="Expected package source-lock SHA-256 from independent operator configuration",
    )
    arguments = parser.parse_args(argv)
    try:
        report = audit_sources(
            arguments.manuscript_pdf,
            arguments.formal_source,
            expected_lock_sha256=arguments.expected_lock_sha256,
        )
    except InputIdentityError as exc:
        print(f"Input identity rejected: {exc}", file=sys.stderr)
        return 2
    # ASCII JSON remains deterministic on consoles whose output encoding is
    # not UTF-8 (notably a default Windows PowerShell GBK console).
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
