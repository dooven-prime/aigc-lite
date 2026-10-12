"""Small command adapter for serving and offline assurance verification."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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
    math_case.add_argument(
        "action", choices=("snapshot", "replay", "evaluate", "review-candidate", "receipt")
    )
    math_case.add_argument("--workspace", required=True)
    math_case.add_argument("--principal", default="local:deployer")
    math_case.add_argument("--claim-id")
    math_case.add_argument("--snapshot-id")
    math_case.add_argument("--receipt-id")
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
    math_case.add_argument("--patched-closure-root")
    math_case.add_argument("--build-work-root")
    math_case.add_argument("--review-file")
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
    elif args.action == "review-candidate":
        if not args.review_file:
            raise SystemExit("--review-file is required for review-candidate")
        stored = service.store_rtheta_candidate_review(
            context, Path(args.review_file).read_bytes()
        )
        result = {
            "candidate_review_artifact_id": stored["artifact"]["id"],
            "candidate_review_sha256": stored["artifact"]["content_hash"],
            "qualification_granted": False,
            "current_use_binding": None,
        }
    elif args.action == "replay":
        if not 60 <= args.timeout_seconds <= 7200:
            raise SystemExit("--timeout-seconds must be between 60 and 7200")
        required = ("claim_id", "snapshot_id", "project_root", "lake", "lean", "comparator")
        required += (
            ("comparator_root", "landrun", "lean4export")
            if args.sandbox_profile == "bwrap-v1" else ("sandbox",)
        )
        missing = [name for name in required if not getattr(args, name)]
        if missing:
            raise SystemExit("Missing replay arguments: " + ", ".join(missing))
        if args.sandbox_profile == "bwrap-v1" and bool(args.dependency_scratch_root) == bool(
            args.patched_closure_root
        ):
            raise SystemExit(
                "bwrap-v1 requires exactly one of --patched-closure-root or "
                "--dependency-scratch-root"
            )
        if args.sandbox_profile == "manual" and (
            args.dependency_scratch_root or args.patched_closure_root or args.build_work_root
        ):
            raise SystemExit("Manual sandbox does not accept bwrap-v1 storage options")
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
                dependency_scratch_root=(
                    Path(args.dependency_scratch_root) if args.dependency_scratch_root else None
                ),
                patched_closure_root=(
                    Path(args.patched_closure_root) if args.patched_closure_root else None
                ),
                build_work_root=Path(args.build_work_root) if args.build_work_root else None,
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
            patched_closure_root=(
                Path(args.patched_closure_root) if args.patched_closure_root else None
            ),
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
    elif args.action == "receipt":
        from .adapters.research_import.math_case_003 import SOURCE_COMMIT, THEOREM_NAME
        from .core.errors import InvalidEvidenceError, ResourceNotFoundError
        from .profiles.math_project import PROJECT_RECEIPT_VERSION

        if not args.receipt_id:
            raise SystemExit("--receipt-id is required for receipt")
        artifact = repository.get_artifact(context.workspace_id, args.receipt_id)
        if artifact is None:
            raise ResourceNotFoundError("artifact", args.receipt_id)
        content = artifact.get("content_text")
        if (
            artifact.get("metadata", {}).get("role") != "project_verification_receipt"
            or not isinstance(content, str)
            or hashlib.sha256(content.encode("utf-8")).hexdigest() != artifact["content_hash"]
        ):
            raise InvalidEvidenceError("receipt", "Project receipt artifact is not byte-closed")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise InvalidEvidenceError("receipt", "Malformed project receipt") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("contract_version") != PROJECT_RECEIPT_VERSION
            or payload.get("source_commit") != SOURCE_COMMIT
            or payload.get("theorem_name") != THEOREM_NAME
        ):
            raise InvalidEvidenceError("receipt", "Unexpected project receipt contract")
        result = {
            "artifact_id": artifact["id"],
            "content_sha256": artifact["content_hash"],
            "receipt": payload,
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
