"""One narrow PDF-to-Comparator mathematical qualification case.

This service stores the pinned bytes and freezes a candidate. It deliberately
does not turn a source snapshot or a successful compile into a qualification.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from typing import Any

from ..adapters.kernel_verification.math_project import MathProjectComparatorBackend
from ..adapters.research_import.math_case_003 import (
    CHALLENGE_PATH,
    CONFIG_PATH,
    LAKEFILE_PATH,
    MANIFEST_PATH,
    PDF_PATH,
    SOLUTION_PATH,
    SOURCE_COMMIT,
    SOURCE_REPOSITORY,
    THEOREM_NAME,
    TOOLCHAIN_PATH,
    OpenAIMathCase003Source,
    PinnedSourceFile,
    validate_snapshot_files,
)
from ..core.artifacts import ArtifactDraft, ArtifactKind
from ..core.contracts import RequestContext, RunStatus, StepKind, StepStatus
from ..core.errors import InvalidEvidenceError, ResourceNotFoundError
from ..core.qualification import (
    MathTheoremCandidateDraft,
    ValidationModality,
    canonical_hash,
    claim_semantic_hash,
)
from ..core.research import VerificationAttemptDraft, VerificationKind, VerificationOutcome
from ..database import get_repository
from ..profiles.math_project import PROJECT_RECEIPT_VERSION, SNAPSHOT_VERSION
from ..repository import Repository
from .artifacts import ArtifactService
from .qualification import QualificationService
from .research_registry import ResearchRegistryService

RepositoryProvider = Callable[[], Repository]
_SOURCE_CHUNK_BYTES = 450_000


class MathProjectSliceService:
    """Import exactly case 003; no user-provided endpoint or source path."""

    def __init__(
        self,
        repository_provider: RepositoryProvider = get_repository,
        *,
        source: OpenAIMathCase003Source | None = None,
        artifacts: ArtifactService | None = None,
        qualification: QualificationService | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._source = source or OpenAIMathCase003Source()
        self._artifacts = artifacts or ArtifactService(repository_provider=repository_provider)
        self._qualification = qualification or QualificationService(
            repository_provider, artifact_service=self._artifacts
        )
        self._research = ResearchRegistryService(
            repository_provider, artifact_service=self._artifacts
        )

    def snapshot(self, context: RequestContext) -> dict[str, Any]:
        """Download and store exact bytes, chunking large PDFs and patches."""

        return self.store_snapshot(context, self._source.fetch())

    def store_snapshot(
        self, context: RequestContext, files: tuple[PinnedSourceFile, ...]
    ) -> dict[str, Any]:
        from ..adapters.research_import.math_case_003 import PINNED_FILES

        if {item.path for item in files} != set(PINNED_FILES) or len(files) != len(PINNED_FILES):
            raise InvalidEvidenceError("source", "Snapshot requires the exact pinned file set")
        for item in files:
            if (
                item.sha256 != PINNED_FILES[item.path]
                or hashlib.sha256(item.content).hexdigest() != item.sha256
            ):
                raise InvalidEvidenceError("source", "Snapshot source hash mismatch")
        entries: list[dict[str, Any]] = []
        source_artifacts = []
        for item in files:
            chunks = [
                item.content[offset : offset + _SOURCE_CHUNK_BYTES]
                for offset in range(0, len(item.content), _SOURCE_CHUNK_BYTES)
            ]
            artifact_ids = []
            for ordinal, chunk in enumerate(chunks):
                content = base64.b64encode(chunk).decode("ascii")
                artifact = self._artifacts.create_artifact(
                    context,
                    ArtifactDraft(
                        name=(item.path.rsplit("/", 1)[-1] + (f".part{ordinal}" if len(chunks) > 1 else "")),
                        kind=ArtifactKind.FILE,
                        media_type="application/pdf+base64"
                        if item.path == PDF_PATH else "application/octet-stream+base64",
                        content_text=content,
                        metadata={
                            "role": "source_snapshot_file",
                            "source_repository": SOURCE_REPOSITORY,
                            "source_commit": SOURCE_COMMIT,
                            "source_path": item.path,
                            "raw_chunk_sha256": hashlib.sha256(chunk).hexdigest(),
                            "encoding": "base64",
                            "chunk_index": ordinal,
                        },
                    ),
                )
                if artifact["content_text"] != content:
                    raise InvalidEvidenceError("source", "Snapshot byte content was modified")
                source_artifacts.append(artifact)
                artifact_ids.append(artifact["id"])
            entries.append(
                {
                    "path": item.path,
                    "sha256": item.sha256,
                    "size_bytes": len(item.content),
                    "artifact_ids": artifact_ids,
                    "encoding": "base64-chunks" if len(chunks) > 1 else "base64",
                }
            )
        manifest = {
            "contract_version": SNAPSHOT_VERSION,
            "source_repository": SOURCE_REPOSITORY,
            "source_commit": SOURCE_COMMIT,
            "source_commit_signature_verified": False,
            "git_tree_independently_reconstructed": False,
            "files": sorted(entries, key=lambda item: item["path"]),
            "limitations": [
                "The fixed commit and raw SHA-256 values do not authenticate the entire Git tree.",
                "Paper-to-Lean semantic alignment and proof validity are not established by a snapshot.",
            ],
        }
        snapshot = self._artifacts.create_artifact(
            context,
            ArtifactDraft(
                name="openai-math-003-source-snapshot.json",
                kind=ArtifactKind.JSON,
                media_type="application/json",
                content_text=json.dumps(manifest, sort_keys=True, separators=(",", ":")),
                metadata={"role": "source_snapshot", "source_commit": SOURCE_COMMIT},
            ),
        )
        return {"snapshot": snapshot, "source_artifacts": source_artifacts, "manifest": manifest}

    def freeze_claim(
        self, context: RequestContext, snapshot_artifact_id: str
    ) -> dict[str, Any]:
        """Freeze only the zeta subclaim, not the full Hecke/Dirichlet paper."""

        snapshot, manifest, artifact_ids = self._load_snapshot(
            context, snapshot_artifact_id
        )
        registered = self._qualification.register_math_theorem(
            context,
            MathTheoremCandidateDraft(
                claim_key="OAI-MATH-003-ZETA-7-8",
                name="Riemann zeta zero-free half-plane Re(s) > 7/8",
                statement=(
                    "For every complex number s, if Re(s) > 7/8, then the Riemann "
                    "zeta function is nonzero at s."
                ),
                scope=(
                    "Only the Riemann zeta subclaim of Theorem 1.1, page 4, in the "
                    "pinned PDF; not the Dirichlet or Hecke claims or later applications."
                ),
                definitions=(
                    "Domain: s ranges over all complex numbers; 7/8 is the exact real threshold.",
                    f"Comparator challenge declaration: {THEOREM_NAME}.",
                    f"Paper path: {PDF_PATH}; challenge path: {CHALLENGE_PATH}.",
                    f"Solution path: {SOLUTION_PATH}; config path: {CONFIG_PATH}.",
                    f"Frozen Lean inputs: {TOOLCHAIN_PATH}, {MANIFEST_PATH}, {LAKEFILE_PATH}.",
                ),
                negative_boundaries=(
                    "No claim about the boundary Re(s) = 7/8 or the Riemann hypothesis.",
                    "No automatic inheritance by other family-003 theorems or manuscripts.",
                    "The principal pole at s=1 and the formal function's value there require semantic review.",
                    "Candidate registration is not qualification or current-use admission.",
                ),
            ),
        )
        snapshot_attempt = self._research.record_verification_attempt(
            RequestContext(
                request_id=context.request_id,
                workspace_id=context.workspace_id,
                principal_id="system:source-snapshot:openai-math-003",
                scopes=context.scopes,
            ),
            registered["claim"]["id"],
            VerificationAttemptDraft(
                kind=VerificationKind.CALCULATION,
                outcome=VerificationOutcome.PASSED,
                validation_modality=ValidationModality.EXACT_REPLAY,
                method="Fetch the fixed case-003 source allowlist and rehash every raw byte.",
                scope="Source identity only; no proof or paper-to-Lean semantic validation.",
                input_digest=canonical_hash({
                    "source_commit": SOURCE_COMMIT,
                    "files": [item["sha256"] for item in manifest["files"]],
                }),
                output_digest=snapshot["content_hash"],
                artifact_ids=tuple(artifact_ids),
            ),
        )
        return {**registered, "source_snapshot_attempt": snapshot_attempt}

    async def verify(
        self,
        context: RequestContext,
        claim_id: str,
        snapshot_artifact_id: str,
        *,
        backend: MathProjectComparatorBackend,
    ) -> dict[str, Any]:
        """Replay in the configured sandbox and record both passing and failed evidence."""

        repository = self._repository_provider()
        claim = repository.get_research_claim(context.workspace_id, claim_id)
        if claim is None:
            raise ResourceNotFoundError("research_claim", claim_id)
        if claim.get("claim_key") != "OAI-MATH-003-ZETA-7-8":
            raise InvalidEvidenceError("claim_id", "This runner is restricted to case 003 zeta")
        semantic_hash = claim_semantic_hash(claim)
        if claim.get("semantic_hash") != semantic_hash:
            raise InvalidEvidenceError("claim_id", "Frozen ClaimRevision has drifted")
        snapshot, manifest, artifact_ids = self._load_snapshot(context, snapshot_artifact_id)
        file_index = {item["path"]: item for item in manifest["files"]}

        run = repository.create_run(
            context.workspace_id, "", context.request_id, None, "kernel/math-project"
        )
        try:
            observation = await backend.verify()
        except Exception:
            repository.finish_run(
                context.workspace_id, run["id"], RunStatus.FAILED.value, "project_replay_failed"
            )
            raise
        passed = observation.kernel_exit_code == 0 and observation.comparator_exit_code == 0
        receipt_payload = {
            "contract_version": PROJECT_RECEIPT_VERSION,
            "claim_revision_id": claim_id,
            "claim_semantic_hash": semantic_hash,
            "source_commit": SOURCE_COMMIT,
            "source_snapshot_artifact_id": snapshot_artifact_id,
            "source_snapshot_hash": snapshot["content_hash"],
            "paper_sha256": file_index[PDF_PATH]["sha256"],
            "challenge_sha256": file_index[CHALLENGE_PATH]["sha256"],
            "config_sha256": file_index[CONFIG_PATH]["sha256"],
            "solution_sha256": file_index[SOLUTION_PATH]["sha256"],
            "lean_toolchain_sha256": file_index[TOOLCHAIN_PATH]["sha256"],
            "lake_manifest_sha256": file_index[MANIFEST_PATH]["sha256"],
            "lakefile_sha256": file_index[LAKEFILE_PATH]["sha256"],
            "theorem_name": THEOREM_NAME,
            "worktree_clean": observation.worktree_clean,
            "dependencies": {
                "closed": observation.dependencies_closed and observation.patch_closure_verified,
                "all_pinned_revisions_match": observation.dependencies_closed,
                "patch_closure_verified": observation.patch_closure_verified,
                "patched_closure_sha256": observation.patched_closure_sha256,
                "mathlib_rev": observation.mathlib_rev,
                "clean_build_exit_code": observation.clean_build_exit_code,
            },
            "kernel": {
                "status": "passed" if observation.kernel_exit_code == 0 else "undetermined",
                "exit_code": observation.kernel_exit_code,
                "lean_executable_hash": observation.lean_executable_hash,
                "lake_executable_hash": observation.lake_executable_hash,
                "lean_version": observation.lean_version,
                "lake_version": observation.lake_version,
                "sorry_present": False if passed else None,
                "axioms_permitted": passed,
                "stdout_sha256": observation.kernel_stdout_hash,
                "stderr_sha256": observation.kernel_stderr_hash,
            },
            "comparator": {
                "statement_identity": "passed"
                if observation.comparator_exit_code == 0
                else "undetermined",
                "kernel_check": "passed" if observation.comparator_exit_code == 0 else "undetermined",
                "exit_code": observation.comparator_exit_code,
                "executable_hash": observation.executable_hash,
                "toolchain_hash": canonical_hash(
                    {
                        "comparator": observation.executable_hash,
                        "lean": observation.lean_executable_hash,
                        "lake": observation.lake_executable_hash,
                        "sandbox": observation.sandbox_hash,
                        "lean_toolchain": file_index[TOOLCHAIN_PATH]["sha256"],
                        "lake_manifest": file_index[MANIFEST_PATH]["sha256"],
                    }
                ),
                "sandbox_executable_hash": observation.sandbox_hash,
                "isolation_enforced": True,
                "command": list(observation.command),
                "stdout_sha256": observation.comparator_stdout_hash,
                "stderr_sha256": observation.comparator_stderr_hash,
            },
            "limitations": list(observation.limitations),
        }
        step = repository.append_run_step(
            context.workspace_id,
            run["id"],
            1,
            StepKind.TOOL.value,
            "math-project.comparator",
            StepStatus.SUCCEEDED.value if passed else StepStatus.FAILED.value,
            json.dumps({"claim_revision_id": claim_id, "snapshot_hash": snapshot["content_hash"]}),
            json.dumps(
                {
                    "kernel_exit_code": observation.kernel_exit_code,
                    "comparator_exit_code": observation.comparator_exit_code,
                }
            ),
            {"toolchain_hash": receipt_payload["comparator"]["toolchain_hash"]},
        )
        receipt = self._artifacts.create_artifact(
            context,
            ArtifactDraft(
                name="openai-math-003-project-verification.json",
                kind=ArtifactKind.JSON,
                media_type="application/json",
                content_text=json.dumps(receipt_payload, sort_keys=True, separators=(",", ":")),
                metadata={
                    "role": "project_verification_receipt",
                    "claim_revision_id": claim_id,
                    "data_snapshot_hash": snapshot["content_hash"],
                    "environment_hash": receipt_payload["comparator"]["toolchain_hash"],
                },
            ),
            run_id=run["id"],
            step_id=step["id"],
        )
        verifier_context = RequestContext(
            request_id=context.request_id,
            workspace_id=context.workspace_id,
            principal_id="system:verifier:math-project:comparator",
            scopes=context.scopes,
        )
        attempt = self._research.record_verification_attempt(
            verifier_context,
            claim_id,
            VerificationAttemptDraft(
                kind=VerificationKind.CALCULATION,
                outcome=(
                    VerificationOutcome.PASSED if passed else
                    VerificationOutcome.INCONCLUSIVE if observation.comparator_exit_code is None else
                    VerificationOutcome.FAILED
                ),
                validation_modality=ValidationModality.KERNEL_CHECK,
                method="Isolated project replay using the pinned Comparator challenge.",
                scope="Only the case-003 Riemann zeta 7/8 declaration.",
                input_digest=canonical_hash(
                    {"claim": semantic_hash, "snapshot": snapshot["content_hash"]}
                ),
                output_digest=receipt["content_hash"],
                run_id=run["id"],
                artifact_ids=tuple([*artifact_ids, receipt["id"]]),
            ),
        )
        repository.finish_run(
            context.workspace_id,
            run["id"],
            RunStatus.SUCCEEDED.value if passed else RunStatus.FAILED.value,
            None if passed else (
                "project_replay_timeout" if observation.comparator_exit_code is None
                else "statement_or_kernel_check_failed"
            ),
        )
        return {
            "run": repository.get_run(context.workspace_id, run["id"]),
            "step": step,
            "receipt_artifact": receipt,
            "verification_attempt": attempt,
            "qualification_granted": False,
        }

    def _load_snapshot(
        self, context: RequestContext, snapshot_artifact_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        repository = self._repository_provider()
        snapshot = repository.get_artifact(context.workspace_id, snapshot_artifact_id)
        if snapshot is None or snapshot.get("metadata", {}).get("role") != "source_snapshot":
            raise InvalidEvidenceError("source_snapshot", "Pinned source snapshot is required")
        try:
            manifest = json.loads(snapshot["content_text"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise InvalidEvidenceError("source_snapshot", "Malformed snapshot manifest") from exc
        if not isinstance(manifest, dict) or (
            manifest.get("contract_version") != SNAPSHOT_VERSION
            or manifest.get("source_commit") != SOURCE_COMMIT
        ):
            raise InvalidEvidenceError(
                "source_snapshot", "Snapshot identity does not match case 003"
            )
        artifact_ids = [snapshot_artifact_id]
        files = manifest.get("files") or []
        if not isinstance(files, list):
            raise InvalidEvidenceError("source_snapshot", "Malformed snapshot file index")
        for file in files:
            if not isinstance(file, dict):
                raise InvalidEvidenceError("source_snapshot", "Malformed snapshot file entry")
            artifact_ids.extend(file.get("artifact_ids") or [])
        artifacts = {
            artifact_id: repository.get_artifact(context.workspace_id, artifact_id)
            for artifact_id in artifact_ids
        }
        if not validate_snapshot_files(files, artifacts):
            raise InvalidEvidenceError("source_snapshot", "Snapshot byte closure is incomplete")
        return snapshot, manifest, artifact_ids
