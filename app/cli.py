"""Small command adapter for serving and offline assurance verification."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

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
    math_case = subcommands.add_parser(
        "math-case-003", help="run the pinned zeta theorem qualification slice"
    )
    math_case.add_argument("action", choices=("snapshot", "replay", "evaluate"))
    math_case.add_argument("--workspace", required=True)
    math_case.add_argument("--principal", default="local:deployer")
    math_case.add_argument("--claim-id")
    math_case.add_argument("--snapshot-id")
    math_case.add_argument("--project-root")
    math_case.add_argument("--lake")
    math_case.add_argument("--lean")
    math_case.add_argument("--comparator")
    math_case.add_argument("--sandbox")
    math_case.add_argument("--sandbox-arg", action="append", default=[])
    math_case.add_argument("--sandbox-profile", choices=("manual", "bwrap-v1"), default="manual")
    math_case.add_argument("--comparator-root")
    math_case.add_argument("--landrun")
    math_case.add_argument("--lean4export")
    math_case.add_argument("--dependency-scratch-root")
    math_case.add_argument("--timeout-seconds", type=int, default=3600)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command in {None, "serve"}:
        from .main import run

        run()
        return
    if args.command == "math-case-003":
        _math_case_003(args)
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


def _math_case_003(args: argparse.Namespace) -> None:
    from .adapters.kernel_verification.math_project import MathProjectComparatorBackend
    from .core.contracts import RequestContext
    from .core.qualification import canonical_hash
    from .database import get_repository
    from .profiles.math_project import PROFILE_ID
    from .services.math_project import MathProjectSliceService
    from .services.qualification import QualificationService

    context = RequestContext(str(uuid4()), args.workspace, args.principal)
    repository = get_repository()
    provider = lambda: repository  # noqa: E731
    service = MathProjectSliceService(provider)
    if args.action == "snapshot":
        snapshot = service.snapshot(context)
        claim = service.freeze_claim(context, snapshot["snapshot"]["id"])["claim"]
        result = {
            "claim_revision_id": claim["id"],
            "claim_semantic_hash": claim["semantic_hash"],
            "source_snapshot_artifact_id": snapshot["snapshot"]["id"],
            "source_snapshot_hash": snapshot["snapshot"]["content_hash"],
            "review_input_digest": canonical_hash({
                "claim": claim["semantic_hash"],
                "snapshot": snapshot["snapshot"]["content_hash"],
            }),
            "qualification": "not_evaluated",
            "current_use_binding": None,
        }
    elif args.action == "replay":
        if not 60 <= args.timeout_seconds <= 7200:
            raise SystemExit("--timeout-seconds must be between 60 and 7200")
        required = ("claim_id", "snapshot_id", "project_root", "lake", "lean", "comparator")
        required += (
            ("comparator_root", "landrun", "lean4export", "dependency_scratch_root")
            if args.sandbox_profile == "bwrap-v1" else ("sandbox",)
        )
        missing = [name for name in required if not getattr(args, name)]
        if missing:
            raise SystemExit("Missing replay arguments: " + ", ".join(missing))
        if args.sandbox_profile == "bwrap-v1":
            from .adapters.kernel_verification.math_project_sandbox import (
                bubblewrap_math_project_args,
            )

            if args.sandbox or args.sandbox_arg:
                raise SystemExit("bwrap-v1 does not accept manual sandbox overrides")
            sandbox_executable = Path("/usr/bin/bwrap")
            sandbox_fixed_args = bubblewrap_math_project_args(
                Path(args.project_root),
                lake_executable=Path(args.lake),
                lean_executable=Path(args.lean),
                comparator_root=Path(args.comparator_root),
                comparator_executable=Path(args.comparator),
                landrun_executable=Path(args.landrun),
                lean4export_executable=Path(args.lean4export),
                dependency_scratch_root=Path(args.dependency_scratch_root),
            )
        else:
            sandbox_executable = Path(args.sandbox)
            sandbox_fixed_args = tuple(args.sandbox_arg)
        backend = MathProjectComparatorBackend(
            Path(args.project_root),
            lake_executable=Path(args.lake),
            lean_executable=Path(args.lean),
            comparator_executable=Path(args.comparator),
            sandbox_executable=sandbox_executable,
            sandbox_fixed_args=sandbox_fixed_args,
            timeout_seconds=args.timeout_seconds,
        )
        replay = asyncio.run(
            service.verify(context, args.claim_id, args.snapshot_id, backend=backend)
        )
        result = {
            "run_id": replay["run"]["id"],
            "run_status": replay["run"]["status"],
            "verification_attempt_id": replay["verification_attempt"]["id"],
            "verification_receipt_artifact_id": replay["receipt_artifact"]["id"],
            "qualification_granted": False,
        }
    else:
        if not args.claim_id:
            raise SystemExit("--claim-id is required for evaluate")
        evaluation = QualificationService(provider).evaluate(context, args.claim_id, PROFILE_ID)
        result = {
            "evaluation_id": evaluation["evaluation"]["id"],
            "verdict": evaluation["evaluation"]["verdict"],
            "blockers": evaluation["evaluation"]["blockers"],
            "qualification_receipt": evaluation["qualification_receipt"],
            "current_use_binding": evaluation["current_use_binding"],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
