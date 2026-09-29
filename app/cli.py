"""Small command adapter for serving and offline assurance verification."""

from __future__ import annotations

import argparse
import json

from .core.errors import InvalidAssuranceBundleError
from .services.assurance import AssuranceBundleVerifier


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aigc-lite")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("serve", help="start the HTTP/MCP runtime")
    verify = subcommands.add_parser("verify", help="verify a portable assurance bundle offline")
    verify.add_argument("bundle", help="bundle directory or ZIP path")
    verify.add_argument(
        "--require-authorized",
        action="store_true",
        help="return exit code 2 unless authority_state is authorized",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command in {None, "serve"}:
        from .main import run

        run()
        return
    try:
        report = AssuranceBundleVerifier().verify(args.bundle)
    except InvalidAssuranceBundleError as exc:
        print(
            json.dumps(
                {
                    "contract_version": "assurance.verification-report.v1",
                    "valid": False,
                    "error": {
                        "code": exc.code.value,
                        "message": str(exc),
                        "metadata": exc.metadata,
                    },
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        raise SystemExit(1) from exc
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if not report["valid"]:
        raise SystemExit(1)
    if args.require_authorized and report["assurance"]["authority_state"]["status"] != "authorized":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
