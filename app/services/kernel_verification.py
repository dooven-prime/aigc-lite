"""Application orchestration for real Lean/Coq kernel verification."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from ..core.artifacts import ArtifactDraft, ArtifactKind
from ..core.contracts import RequestContext, RunStatus, StepKind, StepStatus
from ..core.errors import (
    ErrorCode,
    InvalidKernelVerificationError,
    ResourceNotFoundError,
)
from ..core.kernel_verification import (
    KERNEL_EXECUTION_CONTRACT_VERSION,
    KernelBackendKind,
    KernelExecutionResult,
    KernelExecutionStatus,
    KernelVerificationDraft,
)
from ..core.qualification import ValidationModality, canonical_hash, claim_semantic_hash
from ..core.research import (
    VerificationAttemptDraft,
    VerificationKind,
    VerificationOutcome,
)
from ..database import get_repository
from ..ports.kernel_verification import KernelVerifierSource
from ..profiles.math_theorem import CLAIM_KIND, KERNEL_CERTIFICATE_VERSION
from ..redaction import redact_record_text
from ..repository import Repository
from .artifacts import ArtifactService
from .research_registry import ResearchRegistryService

RepositoryProvider = Callable[[], Repository]
_DECLARATION = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*$")


class KernelVerificationService:
    """Bind kernel execution to ClaimRevision, Run, Artifact, and Receipt ledgers."""

    def __init__(
        self,
        *,
        registry: KernelVerifierSource,
        repository_provider: RepositoryProvider = get_repository,
        artifact_service: ArtifactService | None = None,
        research_registry_service: ResearchRegistryService | None = None,
        max_source_bytes: int = 500_000,
    ) -> None:
        self._registry = registry
        self._repository_provider = repository_provider
        self._artifacts = artifact_service or ArtifactService(
            repository_provider=repository_provider
        )
        self._research = research_registry_service or ResearchRegistryService(
            repository_provider,
            artifact_service=self._artifacts,
        )
        self._max_source_bytes = max_source_bytes

    def list_backends(self) -> list[dict[str, Any]]:
        return self._registry.list_backends()

    async def verify(
        self,
        context: RequestContext,
        claim_id: str,
        draft: KernelVerificationDraft,
    ) -> dict[str, Any]:
        source_bytes = draft.source.encode("utf-8")
        if not source_bytes:
            raise InvalidKernelVerificationError("source", "Proof source is required")
        if len(source_bytes) > self._max_source_bytes:
            raise InvalidKernelVerificationError(
                "source",
                f"Proof source exceeds {self._max_source_bytes} UTF-8 bytes",
            )
        if "\x00" in draft.source:
            raise InvalidKernelVerificationError(
                "source", "Proof source must not contain NUL bytes"
            )
        if redact_record_text(draft.source) != draft.source:
            raise InvalidKernelVerificationError(
                "source",
                "Proof source matches a credential redaction pattern and cannot be "
                "content-bound without modification",
            )
        if not _DECLARATION.fullmatch(draft.declaration_name):
            raise InvalidKernelVerificationError(
                "declaration_name", "Declaration name must be a dotted ASCII identifier"
            )
        backend = self._registry.get(draft.backend)
        repository = self._repository_provider()
        claim = repository.get_research_claim(context.workspace_id, claim_id)
        if claim is None:
            raise ResourceNotFoundError("research_claim", claim_id)
        if claim.get("claim_type") != CLAIM_KIND:
            raise InvalidKernelVerificationError(
                "claim_id", "Kernel verification requires a mathematical theorem revision"
            )
        semantic_hash = claim_semantic_hash(claim)
        if claim.get("semantic_hash") != semantic_hash:
            raise InvalidKernelVerificationError(
                "claim_id", "ClaimRevision semantic fields no longer match its frozen hash"
            )

        run = repository.create_run(
            context.workspace_id,
            "",
            context.request_id,
            None,
            f"kernel/{draft.backend.value}",
        )
        proof = self._artifacts.create_artifact(
            context,
            ArtifactDraft(
                name=f"{draft.declaration_name}.{_extension(draft.backend)}",
                kind=ArtifactKind.FILE,
                media_type=_media_type(draft.backend),
                content_text=draft.source,
                metadata={
                    "role": "formal_proof",
                    "language": draft.backend.value,
                    "claim_revision_id": claim_id,
                    "claim_semantic_hash": semantic_hash,
                    "declaration_name": draft.declaration_name,
                },
            ),
            run_id=run["id"],
        )
        frozen_draft = KernelVerificationDraft(
            backend=draft.backend,
            declaration_name=draft.declaration_name,
            source=proof["content_text"],
        )
        try:
            result = await backend.verify(frozen_draft)
        except asyncio.CancelledError:
            repository.append_run_step(
                context.workspace_id,
                run["id"],
                1,
                StepKind.TOOL.value,
                f"kernel.verify.{draft.backend.value}",
                StepStatus.CANCELLED.value,
                self._ledger_input(claim_id, semantic_hash, proof, draft),
                "",
                {
                    "backend": draft.backend.value,
                    "cancelled": True,
                    "error_code": ErrorCode.AGENT_CANCELLED.value,
                },
            )
            repository.finish_run(
                context.workspace_id,
                run["id"],
                RunStatus.CANCELLED.value,
                ErrorCode.AGENT_CANCELLED.value,
            )
            raise
        except Exception:
            repository.append_run_step(
                context.workspace_id,
                run["id"],
                1,
                StepKind.TOOL.value,
                f"kernel.verify.{draft.backend.value}",
                StepStatus.FAILED.value,
                self._ledger_input(claim_id, semantic_hash, proof, draft),
                "",
                {
                    "backend": draft.backend.value,
                    "error_code": ErrorCode.KERNEL_VERIFICATION_FAILED.value,
                },
            )
            repository.finish_run(
                context.workspace_id,
                run["id"],
                RunStatus.FAILED.value,
                ErrorCode.KERNEL_VERIFICATION_FAILED.value,
            )
            raise

        passed = result.status is KernelExecutionStatus.PASSED
        error_code = _error_code(result.status)
        step = repository.append_run_step(
            context.workspace_id,
            run["id"],
            1,
            StepKind.TOOL.value,
            f"kernel.verify.{draft.backend.value}",
            StepStatus.SUCCEEDED.value if passed else StepStatus.FAILED.value,
            self._ledger_input(claim_id, semantic_hash, proof, draft),
            json.dumps(
                {
                    "status": result.status.value,
                    "exit_code": result.exit_code,
                    "axioms": list(result.axioms),
                    "dependencies": list(result.dependencies),
                    "sorry_present": result.sorry_present,
                    "output_truncated": result.output_truncated,
                    "stdout": redact_record_text(result.stdout, max_chars=20_000),
                    "stderr": redact_record_text(result.stderr, max_chars=20_000),
                },
                ensure_ascii=False,
            ),
            {
                "backend": draft.backend.value,
                "verifier_id": result.verifier_id,
                "toolchain_hash": result.toolchain_hash,
                "duration_ms": result.duration_ms,
                "cancellation_mode": "hard",
                "error_code": error_code,
            },
        )
        certificate_payload = self._certificate(
            claim_id=claim_id,
            semantic_hash=semantic_hash,
            declaration_name=draft.declaration_name,
            proof=proof,
            result=result,
        )
        certificate = self._artifacts.create_artifact(
            context,
            ArtifactDraft(
                name=f"{draft.declaration_name}.{draft.backend.value}.kernel-certificate.json",
                kind=ArtifactKind.JSON,
                media_type="application/json",
                content_text=json.dumps(
                    certificate_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                metadata={
                    "role": "kernel_certificate",
                    "claim_revision_id": claim_id,
                    "claim_semantic_hash": semantic_hash,
                    "backend": draft.backend.value,
                    "toolchain_hash": result.toolchain_hash,
                    "environment_hash": canonical_hash(result.isolation),
                },
            ),
            run_id=run["id"],
            step_id=step["id"],
        )
        input_digest = canonical_hash(
            {
                "claim_revision_id": claim_id,
                "claim_semantic_hash": semantic_hash,
                "proof_artifact_hash": proof["content_hash"],
                "backend": draft.backend.value,
                "declaration_name": draft.declaration_name,
            }
        )
        verifier_context = RequestContext(
            request_id=context.request_id,
            workspace_id=context.workspace_id,
            principal_id=f"system:verifier:{result.verifier_id}",
            scopes=context.scopes,
        )
        try:
            attempt = self._research.record_verification_attempt(
                verifier_context,
                claim_id,
                VerificationAttemptDraft(
                    kind=VerificationKind.CALCULATION,
                    outcome=(
                        VerificationOutcome.PASSED
                        if passed
                        else VerificationOutcome.FAILED
                    ),
                    validation_modality=ValidationModality.KERNEL_CHECK,
                    method=(
                        f"Execute the frozen proof with the configured "
                        f"{result.checker_name} kernel backend."
                    ),
                    scope=(
                        "Single proof file and named declaration bound to one exact "
                        "ClaimRevision; host package closure is not bundled."
                    ),
                    input_digest=input_digest,
                    output_digest=certificate["content_hash"],
                    run_id=run["id"],
                    artifact_ids=(proof["id"], certificate["id"]),
                    metadata={
                        "backend": draft.backend.value,
                        "declaration_name": draft.declaration_name,
                        "kernel_status": result.status.value,
                    },
                ),
            )
        except Exception:
            repository.finish_run(
                context.workspace_id,
                run["id"],
                RunStatus.FAILED.value,
                ErrorCode.INTERNAL_ERROR.value,
            )
            raise
        repository.finish_run(
            context.workspace_id,
            run["id"],
            RunStatus.SUCCEEDED.value if passed else RunStatus.FAILED.value,
            error_code,
        )
        return {
            "backend": backend.describe(),
            "status": result.status.value,
            "kernel_evidence_eligible": passed,
            "qualification_granted": False,
            "run": repository.get_run(context.workspace_id, run["id"]),
            "step": step,
            "proof_artifact": proof,
            "certificate_artifact": certificate,
            "verification_attempt": attempt,
        }

    @staticmethod
    def _ledger_input(
        claim_id: str,
        semantic_hash: str,
        proof: dict[str, Any],
        draft: KernelVerificationDraft,
    ) -> str:
        return json.dumps(
            {
                "claim_revision_id": claim_id,
                "claim_semantic_hash": semantic_hash,
                "proof_artifact_id": proof["id"],
                "proof_artifact_hash": proof["content_hash"],
                "backend": draft.backend.value,
                "declaration_name": draft.declaration_name,
            },
            sort_keys=True,
        )

    @staticmethod
    def _certificate(
        *,
        claim_id: str,
        semantic_hash: str,
        declaration_name: str,
        proof: dict[str, Any],
        result: KernelExecutionResult,
    ) -> dict[str, Any]:
        return {
            "contract_version": KERNEL_CERTIFICATE_VERSION,
            "execution_contract_version": KERNEL_EXECUTION_CONTRACT_VERSION,
            "claim_revision_id": claim_id,
            "claim_semantic_hash": semantic_hash,
            "statement_hash": semantic_hash,
            "declaration_name": declaration_name,
            "proof_artifact_id": proof["id"],
            "proof_artifact_hash": proof["content_hash"],
            "checker": {
                "backend": result.backend.value,
                "name": result.checker_name,
                "version": result.checker_version,
                "executable_hash": result.executable_hash,
                "toolchain_hash": result.toolchain_hash,
                "verifier_id": result.verifier_id,
            },
            "status": result.status.value,
            "axioms": list(result.axioms),
            "sorry_present": result.sorry_present,
            "dependencies": [
                {"name": item, "closure": "unbound"}
                for item in result.dependencies
            ],
            "invocation": {
                "command": list(result.command),
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "output_truncated": result.output_truncated,
                "stdout_hash": hashlib.sha256(result.stdout.encode()).hexdigest(),
                "stderr_hash": hashlib.sha256(result.stderr.encode()).hexdigest(),
            },
            "isolation": result.isolation,
            "limitations": list(result.limitations),
        }


def _extension(backend: KernelBackendKind) -> str:
    return "lean" if backend is KernelBackendKind.LEAN4 else "v"


def _media_type(backend: KernelBackendKind) -> str:
    return "text/x-lean" if backend is KernelBackendKind.LEAN4 else "text/x-coq"


def _error_code(status: KernelExecutionStatus) -> str | None:
    if status is KernelExecutionStatus.PASSED:
        return None
    if status is KernelExecutionStatus.TIMEOUT:
        return ErrorCode.KERNEL_VERIFIER_TIMEOUT.value
    return ErrorCode.KERNEL_VERIFICATION_FAILED.value
