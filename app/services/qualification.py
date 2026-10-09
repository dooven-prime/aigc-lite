"""Deterministic Qualification Plane and domain verifier registry."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from ..core.artifacts import ArtifactDraft, ArtifactKind
from ..core.contracts import RequestContext
from ..core.errors import InvalidEvidenceError, ResourceNotFoundError
from ..core.evidence import ExecutionReceiptDraft, ProtocolDraft, ProtocolStatus
from ..core.kernel_verification import KERNEL_EXECUTION_CONTRACT_VERSION
from ..core.qualification import (
    AuthorizationGrantDraft,
    CriterionReceipt,
    CurrentUseState,
    EvidenceAxisState,
    EvidenceClosure,
    EvidenceEdgeType,
    KnowledgeAdmissionDraft,
    KnowledgeAdmissionPolicy,
    KnowledgeAdmissionReceipt,
    MathTheoremCandidateDraft,
    QualificationDecision,
    QualificationProfile,
    QualificationVerdict,
    ValidationModality,
    VerifierLineage,
    canonical_hash,
    claim_semantic_hash,
)
from ..database import get_repository
from ..profiles.math_project import MATH_PROJECT_PROFILE, PROJECT_RECEIPT_VERSION, SNAPSHOT_VERSION
from ..profiles.math_theorem import (
    CLAIM_KIND,
    KERNEL_CERTIFICATE_VERSION,
    MATH_FORMAL_PROFILE,
)
from ..redaction import redact, redact_record_text
from ..repository import Repository
from .artifacts import ArtifactService
from .evidence import EvidenceService

RepositoryProvider = Callable[[], Repository]
_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
AUTHORIZATION_POLICY_VERSION = "authorization.policy.v1"
DEFAULT_KNOWLEDGE_ADMISSION_POLICY = KnowledgeAdmissionPolicy(
    policy_id="knowledge.default.v1",
    version=1,
    description=(
        "Explicitly select one still-current QualificationReceipt for the default "
        "qualified knowledge retrieval view."
    ),
    requirements=(
        "qualification_receipt_admitted",
        "claim_semantic_identity_current",
        "dependency_closure_current",
        "workspace_admin_approval",
    ),
)


class DomainVerifier(Protocol):
    profile: QualificationProfile

    def evaluate(self, closure: EvidenceClosure) -> QualificationDecision: ...


class DomainVerifierRegistry:
    """Immutable-by-key registry for deterministic, domain-owned verifiers."""

    def __init__(self) -> None:
        self._verifiers: dict[str, DomainVerifier] = {}

    def register(self, verifier: DomainVerifier) -> None:
        key = verifier.profile.profile_id
        if key in self._verifiers:
            raise ValueError(f"Qualification profile already registered: {key}")
        self._verifiers[key] = verifier

    def get(self, profile_id: str) -> DomainVerifier:
        try:
            return self._verifiers[profile_id]
        except KeyError as exc:
            raise ResourceNotFoundError("qualification_profile", profile_id) from exc

    def list_profiles(self) -> list[dict[str, Any]]:
        return [verifier.profile.as_dict() for _, verifier in sorted(self._verifiers.items())]


class MathTheoremVerifier:
    profile = MATH_FORMAL_PROFILE

    def evaluate(self, closure: EvidenceClosure) -> QualificationDecision:
        claim = next(item for item in closure.nodes if item["node_type"] == "claim_revision")
        payload = claim["payload"]
        if payload.get("claim_type") != CLAIM_KIND:
            return QualificationDecision(
                verdict=QualificationVerdict.NOT_APPLICABLE,
                criteria=(),
                blockers=("claim_kind_mismatch",),
                evidence_vector=self._vector(),
            )
        if payload.get("claim_key") == "OAI-MATH-003-ZETA-7-8":
            return QualificationDecision(
                verdict=QualificationVerdict.NOT_APPLICABLE,
                criteria=(),
                blockers=("project_profile_required",),
                evidence_vector=self._vector(),
            )
        if "statement_drift" in closure.limitations:
            return QualificationDecision(
                verdict=QualificationVerdict.STALE,
                criteria=(
                    CriterionReceipt(
                        code="statement_identity",
                        state=EvidenceAxisState.FAILED,
                        reason=(
                            "The persisted semantic hash no longer matches the frozen "
                            "ClaimRevision fields."
                        ),
                        evidence_node_ids=(closure.claim_revision_id,),
                    ),
                ),
                blockers=("statement_drift",),
                evidence_vector=self._vector(statement_identity=False),
            )

        artifacts = {
            item["node_id"]: item["payload"]
            for item in closure.nodes
            if item["node_type"] == "artifact"
        }
        attempts = [item for item in closure.nodes if item["node_type"] == "verification_attempt"]
        formal_artifacts = [
            (artifact_id, value)
            for artifact_id, value in artifacts.items()
            if value.get("metadata", {}).get("role") == "formal_proof"
        ]
        kernel_attempts = [
            item
            for item in attempts
            if not item["payload"].get("invalidated_by")
            and item["payload"].get("validation_modality") == ValidationModality.KERNEL_CHECK.value
            and item["payload"].get("outcome") == "passed"
            and item["payload"].get("verifier_lineage", {}).get("runtime_derived") is True
            and str(
                item["payload"].get("verifier_lineage", {}).get("principal_id") or ""
            ).startswith("system:verifier:")
            and not item["payload"].get("verifier_lineage", {}).get("model_route")
        ]
        alignment_attempts = [
            item
            for item in attempts
            if not item["payload"].get("invalidated_by")
            and item["payload"].get("validation_modality") == ValidationModality.EXPERT_REVIEW.value
            and item["payload"].get("outcome") == "passed"
            and not item["payload"].get("verifier_lineage", {}).get("model_route")
        ]
        certificate, certificate_artifact_id = self._kernel_certificate(
            kernel_attempts,
            artifacts,
            claim_revision_id=closure.claim_revision_id,
            claim_semantic_hash=closure.claim_semantic_hash,
        )
        identity_ok = (
            bool(payload.get("semantic_hash"))
            and payload.get("semantic_hash") == closure.claim_semantic_hash
            and certificate is not None
            and certificate.get("claim_revision_id") == closure.claim_revision_id
            and certificate.get("claim_semantic_hash") == closure.claim_semantic_hash
        )
        formal_ok = bool(formal_artifacts)
        kernel_ok = False
        axioms_ok = False
        certificate_blocker = None
        if certificate is not None:
            proof_id = certificate.get("proof_artifact_id")
            proof = artifacts.get(proof_id)
            checker = certificate.get("checker")
            invocation = certificate.get("invocation")
            isolation = certificate.get("isolation")
            kernel_ok = bool(
                certificate.get("contract_version") == KERNEL_CERTIFICATE_VERSION
                and certificate.get("execution_contract_version")
                == KERNEL_EXECUTION_CONTRACT_VERSION
                and certificate.get("status") == "passed"
                and certificate.get("sorry_present") is False
                and certificate.get("dependencies") == []
                and isinstance(checker, dict)
                and checker.get("backend") in {"lean4", "coq"}
                and checker.get("name")
                and checker.get("version")
                and _SHA256.fullmatch(str(checker.get("executable_hash") or ""))
                and _SHA256.fullmatch(str(checker.get("toolchain_hash") or ""))
                and isinstance(invocation, dict)
                and isinstance(invocation.get("command"), list)
                and bool(invocation.get("command"))
                and invocation.get("exit_code") == 0
                and isinstance(isolation, dict)
                and isolation.get("shell") is False
                and isolation.get("request_controls_command") is False
                and proof is not None
                and proof.get("content_hash") == certificate.get("proof_artifact_hash")
            )
            axioms_ok = certificate.get("axioms") == []
            if certificate.get("sorry_present") is not False:
                certificate_blocker = "sorry_or_placeholder_present"
            elif not axioms_ok:
                certificate_blocker = "undeclared_axioms_present"
            elif certificate.get("dependencies") != []:
                certificate_blocker = "unclosed_proof_dependencies"
            elif not kernel_ok:
                certificate_blocker = "invalid_kernel_certificate"

        dependency_nodes = [
            item for item in closure.nodes if item["node_type"] == "dependency_binding"
        ]
        dependencies_ok = (certificate is None or certificate.get("dependencies") == []) and all(
            item["payload"].get("state") == CurrentUseState.CURRENT.value
            for item in dependency_nodes
        )
        criteria = (
            self._criterion(
                "statement_identity",
                identity_ok,
                "Kernel certificate binds the exact frozen semantic hash.",
                "No kernel certificate binds this exact ClaimRevision.",
                (certificate_artifact_id,) if certificate_artifact_id else (),
            ),
            self._criterion(
                "formal_artifact_present",
                formal_ok,
                "A content-addressed formal proof is present.",
                "No Artifact with role=formal_proof is in the evidence closure.",
                tuple(item[0] for item in formal_artifacts),
            ),
            self._criterion(
                "kernel_check",
                kernel_ok,
                "An orthogonal proof kernel accepted the bound proof artifact.",
                "No valid passing kernel certificate was found.",
                tuple(item["node_id"] for item in kernel_attempts),
            ),
            self._criterion(
                "undeclared_axioms",
                axioms_ok,
                "The certificate declares no additional axioms.",
                "The certificate is missing or declares additional axioms.",
                (certificate_artifact_id,) if certificate_artifact_id else (),
            ),
            self._criterion(
                "dependency_closure",
                dependencies_ok,
                "Every declared theorem dependency has a current qualification binding.",
                "At least one theorem dependency lacks a current qualification binding.",
                tuple(item["node_id"] for item in dependency_nodes),
            ),
            self._criterion(
                "semantic_alignment_review",
                bool(alignment_attempts),
                "A non-model expert review accepted semantic alignment.",
                "No passing non-model semantic alignment review is present.",
                tuple(item["node_id"] for item in alignment_attempts),
            ),
        )
        blockers = tuple(
            item
            for item in (
                certificate_blocker,
                "stale_dependency" if not dependencies_ok else None,
            )
            if item is not None
        )
        missing = [item.code for item in criteria if item.state is not EvidenceAxisState.SATISFIED]
        if not missing:
            verdict = QualificationVerdict.ADMITTED
        elif blockers:
            verdict = QualificationVerdict.BLOCKED
        else:
            verdict = QualificationVerdict.UNRESOLVED
        vector = self._vector(
            statement_identity=identity_ok,
            artifact_integrity=formal_ok and kernel_ok,
            local_correctness=kernel_ok and axioms_ok,
            semantic_alignment=bool(alignment_attempts),
            replayability=bool(certificate and certificate.get("checker")),
            orthogonal_verification=kernel_ok,
            disconfirmation=bool(alignment_attempts),
        )
        return QualificationDecision(
            verdict=verdict,
            criteria=criteria,
            blockers=tuple(dict.fromkeys((*blockers, *missing))),
            evidence_vector=vector,
        )

    @staticmethod
    def _kernel_certificate(
        attempts: list[dict],
        artifacts: dict[str, dict],
        *,
        claim_revision_id: str,
        claim_semantic_hash: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        fallback: tuple[dict[str, Any], str] | None = None
        for attempt in reversed(attempts):
            for artifact_id in reversed(attempt["payload"].get("artifact_ids", [])):
                artifact = artifacts.get(artifact_id)
                if not artifact or artifact.get("metadata", {}).get("role") != "kernel_certificate":
                    continue
                try:
                    value = json.loads(artifact.get("content_text") or "")
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    candidate = (value, artifact_id)
                    if fallback is None:
                        fallback = candidate
                    if MathTheoremVerifier._valid_kernel_certificate(
                        value,
                        artifacts,
                        claim_revision_id=claim_revision_id,
                        claim_semantic_hash=claim_semantic_hash,
                    ):
                        return candidate
        return fallback or (None, None)

    @staticmethod
    def _valid_kernel_certificate(
        certificate: dict[str, Any],
        artifacts: dict[str, dict],
        *,
        claim_revision_id: str,
        claim_semantic_hash: str,
    ) -> bool:
        checker = certificate.get("checker")
        invocation = certificate.get("invocation")
        isolation = certificate.get("isolation")
        proof = artifacts.get(certificate.get("proof_artifact_id"))
        return bool(
            certificate.get("contract_version") == KERNEL_CERTIFICATE_VERSION
            and certificate.get("execution_contract_version") == KERNEL_EXECUTION_CONTRACT_VERSION
            and certificate.get("claim_revision_id") == claim_revision_id
            and certificate.get("claim_semantic_hash") == claim_semantic_hash
            and certificate.get("status") == "passed"
            and certificate.get("axioms") == []
            and certificate.get("sorry_present") is False
            and certificate.get("dependencies") == []
            and isinstance(checker, dict)
            and checker.get("backend") in {"lean4", "coq"}
            and checker.get("name")
            and checker.get("version")
            and _SHA256.fullmatch(str(checker.get("executable_hash") or ""))
            and _SHA256.fullmatch(str(checker.get("toolchain_hash") or ""))
            and isinstance(invocation, dict)
            and isinstance(invocation.get("command"), list)
            and bool(invocation.get("command"))
            and invocation.get("exit_code") == 0
            and isinstance(isolation, dict)
            and isolation.get("shell") is False
            and isolation.get("request_controls_command") is False
            and proof is not None
            and proof.get("content_hash") == certificate.get("proof_artifact_hash")
        )

    @staticmethod
    def _criterion(
        code: str,
        passed: bool,
        success: str,
        failure: str,
        evidence_ids: tuple[str, ...],
    ) -> CriterionReceipt:
        return CriterionReceipt(
            code=code,
            state=(EvidenceAxisState.SATISFIED if passed else EvidenceAxisState.UNDETERMINED),
            reason=success if passed else failure,
            evidence_node_ids=evidence_ids,
        )

    @staticmethod
    def _vector(**values: bool) -> dict[str, EvidenceAxisState]:
        result = {
            key: EvidenceAxisState.UNDETERMINED
            for key in (
                "statement_identity",
                "artifact_integrity",
                "local_correctness",
                "semantic_alignment",
                "replayability",
                "orthogonal_verification",
                "independent_validation",
                "external_reality",
                "disconfirmation",
            )
        }
        result["external_reality"] = EvidenceAxisState.NOT_APPLICABLE
        result.update(
            {
                key: (EvidenceAxisState.SATISFIED if value else EvidenceAxisState.UNDETERMINED)
                for key, value in values.items()
            }
        )
        return result


class MathProjectVerifier(MathTheoremVerifier):
    """Fail-closed gate for a project replay and an exact Comparator challenge."""

    profile = MATH_PROJECT_PROFILE

    def evaluate(self, closure: EvidenceClosure) -> QualificationDecision:
        claim = next(item for item in closure.nodes if item["node_type"] == "claim_revision")
        if (
            claim["payload"].get("claim_type") != CLAIM_KIND
            or claim["payload"].get("claim_key") != "OAI-MATH-003-ZETA-7-8"
        ):
            return QualificationDecision(
                QualificationVerdict.NOT_APPLICABLE, (), ("case_003_claim_required",), self._vector()
            )
        if "statement_drift" in closure.limitations:
            return QualificationDecision(
                QualificationVerdict.STALE,
                (),
                ("statement_drift",),
                self._vector(statement_identity=False),
            )

        artifacts = {
            item["node_id"]: item["payload"]
            for item in closure.nodes
            if item["node_type"] == "artifact"
        }
        attempts = [
            item
            for item in closure.nodes
            if item["node_type"] == "verification_attempt"
            and not item["payload"].get("invalidated_by")
            and item["payload"].get("validation_modality") == ValidationModality.KERNEL_CHECK.value
            and item["payload"].get("verifier_lineage", {}).get("runtime_derived") is True
            and str(
                item["payload"].get("verifier_lineage", {}).get("principal_id") or ""
            ).startswith("system:verifier:math-project:")
            and not item["payload"].get("verifier_lineage", {}).get("model_route")
            and item["payload"].get("run_id")
        ]
        candidates: list[tuple[dict, dict, str]] = []
        for attempt in attempts:
            for artifact_id in attempt["payload"].get("artifact_ids", []):
                artifact = artifacts.get(artifact_id)
                if (
                    artifact is None
                    or artifact.get("metadata", {}).get("role") != "project_verification_receipt"
                ):
                    continue
                try:
                    receipt = json.loads(artifact.get("content_text") or "")
                except (TypeError, json.JSONDecodeError):
                    continue
                if (
                    isinstance(receipt, dict)
                    and receipt.get("claim_revision_id") == closure.claim_revision_id
                ):
                    candidates.append((receipt, attempt, artifact_id))
        selected = next(
            (
                candidate
                for candidate in candidates
                if candidate[1]["payload"].get("outcome") == "passed"
                and isinstance(candidate[0].get("comparator"), dict)
                and candidate[0]["comparator"].get("statement_identity") == "passed"
            ),
            candidates[-1] if candidates else None,
        )
        receipt, attempt, receipt_id = selected if selected else ({}, {}, "")
        receipt_artifact = artifacts.get(receipt_id, {})
        source_attempts = [
            item for item in closure.nodes
            if item["node_type"] == "verification_attempt"
            and not item["payload"].get("invalidated_by")
            and item["payload"].get("validation_modality") == ValidationModality.EXACT_REPLAY.value
            and item["payload"].get("outcome") == "passed"
            and item["payload"].get("verifier_lineage", {}).get("principal_id")
            == "system:source-snapshot:openai-math-003"
            and not item["payload"].get("verifier_lineage", {}).get("model_route")
        ]
        source_snapshot_ids = [
            artifact_id
            for source_attempt in source_attempts
            for artifact_id in source_attempt["payload"].get("artifact_ids", [])
            if artifacts.get(artifact_id, {}).get("metadata", {}).get("role") == "source_snapshot"
        ]
        snapshot_id = receipt.get("source_snapshot_artifact_id") or (
            source_snapshot_ids[0] if source_snapshot_ids else None
        )
        snapshot = artifacts.get(snapshot_id)
        manifest = {}
        if snapshot and snapshot.get("metadata", {}).get("role") == "source_snapshot":
            try:
                manifest = json.loads(snapshot.get("content_text") or "")
            except (TypeError, json.JSONDecodeError):
                pass
        if not isinstance(manifest, dict):
            manifest = {}
        files = manifest.get("files")
        from ..adapters.research_import.math_case_003 import (
            CHALLENGE_PATH,
            CONFIG_PATH,
            LAKEFILE_PATH,
            MANIFEST_PATH,
            PDF_PATH,
            SOLUTION_PATH,
            SOURCE_COMMIT,
            TOOLCHAIN_PATH,
            validate_snapshot_files,
        )

        file_index = (
            {item.get("path"): item for item in files if isinstance(item, dict)}
            if isinstance(files, list)
            else {}
        )
        snapshot_ok = bool(
            snapshot
            and snapshot_id in source_snapshot_ids
            and any(
                item["payload"].get("output_digest") == snapshot.get("content_hash")
                and snapshot_id in item["payload"].get("artifact_ids", [])
                for item in source_attempts
            )
            and hashlib.sha256(str(snapshot.get("content_text") or "").encode("utf-8")).hexdigest()
            == snapshot.get("content_hash")
            and manifest.get("contract_version") == SNAPSHOT_VERSION
            and manifest.get("source_commit") == SOURCE_COMMIT
            and validate_snapshot_files(files, artifacts)
            and (
                not receipt or snapshot.get("content_hash") == receipt.get("source_snapshot_hash")
            )
        )
        checker = receipt.get("comparator") if isinstance(receipt.get("comparator"), dict) else {}
        kernel = receipt.get("kernel") if isinstance(receipt.get("kernel"), dict) else {}
        deps = receipt.get("dependencies") if isinstance(receipt.get("dependencies"), dict) else {}
        bound = bool(
            receipt.get("contract_version") == PROJECT_RECEIPT_VERSION
            and attempt.get("payload", {}).get("outcome") in {"passed", "failed"}
            and hashlib.sha256(
                str(receipt_artifact.get("content_text") or "").encode("utf-8")
            ).hexdigest()
            == receipt_artifact.get("content_hash")
            and receipt.get("claim_semantic_hash") == closure.claim_semantic_hash
            and receipt.get("source_commit") == SOURCE_COMMIT
            and snapshot_ok
            and receipt.get("challenge_sha256") == file_index[CHALLENGE_PATH]["sha256"]
            and receipt.get("config_sha256") == file_index[CONFIG_PATH]["sha256"]
            and receipt.get("solution_sha256") == file_index[SOLUTION_PATH]["sha256"]
            and receipt.get("lean_toolchain_sha256") == file_index[TOOLCHAIN_PATH]["sha256"]
            and receipt.get("lake_manifest_sha256") == file_index[MANIFEST_PATH]["sha256"]
            and receipt.get("lakefile_sha256") == file_index[LAKEFILE_PATH]["sha256"]
            and receipt.get("paper_sha256") == file_index[PDF_PATH]["sha256"]
            and receipt.get("worktree_clean") is True
            and receipt.get("theorem_name") == "OAI.riemannZeta_ne_zero_of_seven_eighths_lt_re"
            and _SHA256.fullmatch(str(checker.get("executable_hash") or ""))
            and _SHA256.fullmatch(str(checker.get("sandbox_executable_hash") or ""))
            and checker.get("isolation_enforced") is True
            and _SHA256.fullmatch(str(kernel.get("lean_executable_hash") or ""))
            and _SHA256.fullmatch(str(kernel.get("lake_executable_hash") or ""))
            and "4.34.1" in str(kernel.get("lean_version") or "")
            and "4.34.1" in str(kernel.get("lake_version") or "")
        )
        identity_ok = bool(
            bound
            and attempt.get("payload", {}).get("outcome") == "passed"
            and checker.get("statement_identity") == "passed"
            and checker.get("exit_code") == 0
        )
        kernel_ok = bool(
            bound
            and attempt.get("payload", {}).get("outcome") == "passed"
            and checker.get("kernel_check") == "passed"
            and kernel.get("status") == "passed"
            and kernel.get("sorry_present") is False
            and kernel.get("axioms_permitted") is True
        )
        deps_ok = bool(
            bound
            and deps.get("closed") is True
            and deps.get("patch_closure_verified") is True
            and deps.get("clean_build_exit_code") == 0
            and deps.get("mathlib_rev") == "d13f23b723b8a846827a245b89c10fc7d3f11612"
            and deps.get("all_pinned_revisions_match") is True
        )
        reviews = [
            item
            for item in closure.nodes
            if item["node_type"] == "verification_attempt"
            and not item["payload"].get("invalidated_by")
            and item["payload"].get("validation_modality") == ValidationModality.EXPERT_REVIEW.value
            and item["payload"].get("outcome") == "passed"
            and not item["payload"].get("verifier_lineage", {}).get("model_route")
            and snapshot_id in item["payload"].get("artifact_ids", [])
            and item["payload"].get("input_digest")
            == canonical_hash(
                {
                    "claim": closure.claim_semantic_hash,
                    "snapshot": snapshot.get("content_hash") if snapshot else None,
                }
            )
        ]
        review_ok = bool(reviews)
        criteria = (
            self._criterion(
                "source_snapshot",
                snapshot_ok,
                "Pinned source bytes are present.",
                "Pinned source snapshot is missing or incomplete.",
                (snapshot_id,) if snapshot_id else (),
            ),
            self._criterion(
                "statement_identity",
                identity_ok,
                "Comparator matched the challenge statement.",
                "No passing, isolated Comparator statement check is bound to this revision.",
                (receipt_id,) if receipt_id else (),
            ),
            self._criterion(
                "kernel_check",
                kernel_ok,
                "Comparator observed kernel acceptance and allowed axioms.",
                "No bound passing kernel check is present.",
                (receipt_id,) if receipt_id else (),
            ),
            self._criterion(
                "dependency_closure",
                deps_ok,
                "Pinned project dependencies were checked locally.",
                "Project dependencies are not closed at pinned revisions.",
                (receipt_id,) if receipt_id else (),
            ),
            self._criterion(
                "semantic_alignment_review",
                review_ok,
                "A non-model review links the paper to this challenge.",
                "Paper-to-challenge alignment has not been reviewed.",
                tuple(item["node_id"] for item in reviews),
            ),
        )
        comparator_rejected = bool(
            bound and isinstance(checker.get("exit_code"), int) and checker["exit_code"] != 0
        )
        blockers = tuple(
            item.code for item in criteria if item.state is not EvidenceAxisState.SATISFIED
        )
        verdict = (
            QualificationVerdict.ADMITTED
            if not blockers
            else QualificationVerdict.BLOCKED
            if comparator_rejected
            else QualificationVerdict.UNRESOLVED
        )
        vector = self._vector(
            statement_identity=identity_ok,
            artifact_integrity=snapshot_ok,
            local_correctness=kernel_ok,
            semantic_alignment=review_ok,
            replayability=deps_ok,
            orthogonal_verification=kernel_ok,
        )
        if comparator_rejected:
            blockers = ("comparator_rejected", *blockers)
        return QualificationDecision(verdict, criteria, blockers, vector)


class QualificationGate:
    """The sole deterministic writer-side decision boundary for qualification."""

    def __init__(self, registry: DomainVerifierRegistry) -> None:
        self._registry = registry

    def evaluate(
        self, profile_id: str, closure: EvidenceClosure
    ) -> tuple[QualificationProfile, QualificationDecision]:
        verifier = self._registry.get(profile_id)
        return verifier.profile, verifier.evaluate(closure)


def create_default_verifier_registry() -> DomainVerifierRegistry:
    registry = DomainVerifierRegistry()
    registry.register(MathTheoremVerifier())
    registry.register(MathProjectVerifier())
    return registry


class QualificationService:
    def __init__(
        self,
        repository_provider: RepositoryProvider = get_repository,
        *,
        artifact_service: ArtifactService | None = None,
        evidence_service: EvidenceService | None = None,
        registry: DomainVerifierRegistry | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._artifacts = artifact_service or ArtifactService(
            repository_provider=repository_provider
        )
        self._evidence = evidence_service or EvidenceService(repository_provider)
        self._registry = registry or create_default_verifier_registry()
        self._gate = QualificationGate(self._registry)

    def claim_status(self, context: RequestContext, claim_id: str) -> dict:
        """Read a revision's separate qualification and current-use histories.

        Refreshing a binding applies existing dependency taint rules; it never
        creates a qualification, admission, or authorization.
        """
        repository = self._repository_provider()
        claim = repository.get_research_claim(context.workspace_id, claim_id)
        if claim is None:
            raise ResourceNotFoundError("research_claim", claim_id)
        receipts = repository.list_qualification_receipts(context.workspace_id, claim_id)
        evaluations = repository.list_qualification_evaluations(
            context.workspace_id, claim_id
        )
        profiles = {item["profile_id"] for item in receipts}
        profiles.update(item["profile_id"] for item in evaluations)
        bindings = []
        for profile_id in sorted(profiles):
            binding = repository.get_current_use_binding(
                context.workspace_id, claim_id, profile_id
            )
            if binding is not None:
                bindings.append(self._refresh_binding(context, binding))
        return {
            "claim": claim,
            "evaluations": evaluations,
            "receipts": receipts,
            "knowledge_admissions": repository.list_knowledge_admission_receipts(
                context.workspace_id, claim_id
            ),
            "current_use_bindings": bindings,
        }

    def list_profiles(self) -> list[dict[str, Any]]:
        return self._registry.list_profiles()

    @staticmethod
    def list_knowledge_admission_policies() -> list[dict[str, Any]]:
        return [DEFAULT_KNOWLEDGE_ADMISSION_POLICY.as_dict()]

    def register_math_theorem(
        self, context: RequestContext, draft: MathTheoremCandidateDraft
    ) -> dict:
        if not _KEY.fullmatch(draft.claim_key):
            raise InvalidEvidenceError("claim_key", "claim_key has an invalid format")
        name = self._text("name", draft.name, 200)
        statement = self._text("statement", draft.statement, 20_000)
        scope = self._text("scope", draft.scope, 10_000)
        definitions = tuple(self._text("definition", item, 4_000) for item in draft.definitions)
        negative_boundaries = tuple(
            self._text("negative_boundary", item, 4_000) for item in draft.negative_boundaries
        )
        if len(definitions) > 100 or len(negative_boundaries) > 100:
            raise InvalidEvidenceError(
                "claim", "definitions and negative boundaries are bounded to 100 items"
            )
        dependency_claim_ids = tuple(dict.fromkeys(draft.dependency_claim_ids))
        repository = self._repository_provider()
        for dependency_id in dependency_claim_ids:
            if repository.get_research_claim(context.workspace_id, dependency_id) is None:
                raise ResourceNotFoundError("research_claim", dependency_id)
        if (
            draft.parent_revision_id
            and repository.get_research_claim(context.workspace_id, draft.parent_revision_id)
            is None
        ):
            raise ResourceNotFoundError("research_claim", draft.parent_revision_id)

        semantic_input = {
            "claim_key": draft.claim_key,
            "revision_number": 1,
            "claim_type": CLAIM_KIND,
            "statement": statement,
            "scope": scope,
            "definitions": list(definitions),
            "negative_boundaries": list(negative_boundaries),
            "dependency_claim_ids": list(dependency_claim_ids),
            "parent_revision_id": draft.parent_revision_id,
        }
        semantic_hash = claim_semantic_hash(semantic_input)
        candidate_payload = {
            "contract_version": "math.theorem-candidate.v1",
            **semantic_input,
            "semantic_hash": semantic_hash,
            "qualification": None,
            "authority": None,
        }
        protocol = self._evidence.create_protocol(
            context,
            ProtocolDraft(
                name=name,
                profile="math.theorem",
                purpose="Register a frozen theorem candidate for later qualification.",
                scope=scope,
                completion_predicate=(
                    "The exact theorem revision is frozen and addressable; no truth or "
                    "execution authority is implied by registration."
                ),
                stop_conditions=("Semantic input is incomplete",),
                budget={"qualification_runs": 0},
                status=ProtocolStatus.FROZEN,
            ),
            content_hash=canonical_hash(candidate_payload),
        )
        artifact = self._artifacts.create_artifact(
            context,
            ArtifactDraft(
                name=f"{draft.claim_key}-revision-1.json",
                kind=ArtifactKind.JSON,
                media_type="application/json",
                content_text=json.dumps(candidate_payload, ensure_ascii=False, indent=2),
                metadata={
                    "role": "theorem_candidate",
                    "semantic_hash": semantic_hash,
                    "protocol_id": protocol["id"],
                },
            ),
        )
        receipt = self._evidence.create_receipt(
            context,
            protocol["id"],
            ExecutionReceiptDraft(
                status="candidate_registered",
                input_digest=semantic_hash,
                output_digest=artifact["content_hash"],
                runtime={"component": "qualification.math-theorem-registrar"},
                artifact_ids=(artifact["id"],),
                metadata={
                    "storage_admission_only": True,
                    "qualification_granted": False,
                    "authorization_granted": False,
                },
            ),
        )
        research_case = repository.create_research_registry(
            context.workspace_id,
            {
                "protocol_id": protocol["id"],
                "receipt_id": receipt["id"],
                "profile": "math.theorem",
                "name": name,
                "registry_id": draft.claim_key,
                "registry_version": "1",
                "authority": "candidate_only",
                "status": "frozen",
                "source_artifact_id": artifact["id"],
                "metadata": {
                    "generation_is_cheap_qualification_is_earned": True,
                    "workflow_stage_only": True,
                    "origin_lineage": VerifierLineage(
                        principal_id=context.principal_id,
                        organization_id=context.workspace_id,
                    ).as_dict(),
                },
            },
            [],
            [
                {
                    **semantic_input,
                    "method_revision": "math-theorem-registration-v1",
                    "lifecycle_status": "private",
                    "status_axes": {"workflow_stage": "registered"},
                    "closure_status": "closed",
                    "blockers": ["qualification_not_evaluated"],
                    "semantic_hash": semantic_hash,
                }
            ],
        )
        claim = repository.list_research_claims(
            context.workspace_id, research_case_id=research_case["id"], limit=1
        )[0]
        return {"case": research_case, "claim": claim, "artifact": artifact}

    def evaluate(self, context: RequestContext, claim_id: str, profile_id: str) -> dict[str, Any]:
        repository = self._repository_provider()
        claim = repository.get_research_claim(context.workspace_id, claim_id)
        if claim is None:
            raise ResourceNotFoundError("research_claim", claim_id)
        closure = self._build_closure(context, claim, profile_id)
        profile, decision = self._gate.evaluate(profile_id, closure)
        claim_invalidations = [
            item
            for item in repository.list_invalidation_decisions(context.workspace_id, claim_id)
            if item["target_scope"] == "claim_revision"
        ]
        if claim_invalidations:
            decision = QualificationDecision(
                verdict=QualificationVerdict.BLOCKED,
                criteria=decision.criteria,
                blockers=tuple(
                    dict.fromkeys(
                        (*decision.blockers, "claim_revision_invalidated")
                    )
                ),
                evidence_vector=decision.evidence_vector,
            )
        independence = self._independence_summary(closure)
        policy_hash = canonical_hash(
            {
                "profile_hash": profile.content_hash,
                "policy_version": profile.policy_version,
            }
        )
        evaluation = repository.create_qualification_evaluation(
            context.workspace_id,
            {
                "claim_revision_id": claim_id,
                "claim_semantic_hash": closure.claim_semantic_hash,
                "profile_id": profile.profile_id,
                "profile_version": profile.version,
                "profile_hash": profile.content_hash,
                "profile_snapshot": profile.as_dict(),
                "evidence_closure_hash": closure.content_hash,
                "evidence_closure": closure.as_dict(),
                "policy_version": profile.policy_version,
                "policy_hash": policy_hash,
                **decision.as_dict(),
                "independence_summary": independence,
                "evaluated_by": context.principal_id,
            },
        )
        repository.create_evidence_edges(
            context.workspace_id, evaluation["id"], list(closure.edges)
        )
        receipt = None
        if decision.verdict is QualificationVerdict.ADMITTED:
            receipt_id = str(uuid4())
            issued_at = datetime.now(UTC).isoformat()
            portable = {
                "qualification_receipt_id": receipt_id,
                "claim_revision_id": claim_id,
                "claim_semantic_hash": closure.claim_semantic_hash,
                "profile": profile.profile_id,
                "profile_version": profile.version,
                "evidence_closure_hash": closure.content_hash,
                "policy_version": profile.policy_version,
                "policy_hash": policy_hash,
                "verdict": decision.verdict.value,
                "criteria": [item.as_dict() for item in decision.criteria],
                "blockers": list(decision.blockers),
                "evidence_vector": {
                    key: value.value for key, value in decision.evidence_vector.items()
                },
                "independence_summary": independence,
                "issued_at": issued_at,
            }
            receipt = repository.create_qualification_receipt(
                context.workspace_id,
                {
                    "id": receipt_id,
                    "evaluation_id": evaluation["id"],
                    "claim_revision_id": claim_id,
                    "claim_semantic_hash": closure.claim_semantic_hash,
                    "profile_id": profile.profile_id,
                    "profile_version": profile.version,
                    "evidence_closure_hash": closure.content_hash,
                    "policy_version": profile.policy_version,
                    "policy_hash": policy_hash,
                    "verdict": decision.verdict.value,
                    "criteria": portable["criteria"],
                    "blockers": portable["blockers"],
                    "evidence_vector": portable["evidence_vector"],
                    "independence_summary": independence,
                    "receipt_hash": canonical_hash(portable),
                    "issued_at": issued_at,
                },
            )
        return {
            "evaluation": evaluation,
            "qualification_receipt": (
                self._portable_receipt(receipt) if receipt is not None else None
            ),
            "current_use_binding": None,
            "knowledge_admission_required": receipt is not None,
        }

    def get_receipt(self, context: RequestContext, receipt_id: str) -> dict:
        value = self._repository_provider().get_qualification_receipt(
            context.workspace_id, receipt_id
        )
        if value is None:
            raise ResourceNotFoundError("qualification_receipt", receipt_id)
        return self._portable_receipt(value)

    def admit_knowledge(
        self,
        context: RequestContext,
        draft: KnowledgeAdmissionDraft,
    ) -> dict[str, Any]:
        """Explicitly select a qualified historical receipt for knowledge use."""

        if not context.principal_id:
            raise InvalidEvidenceError(
                "approver", "Knowledge admission requires an authenticated approver"
            )
        if draft.admission_policy_id != DEFAULT_KNOWLEDGE_ADMISSION_POLICY.policy_id:
            raise ResourceNotFoundError("knowledge_admission_policy", draft.admission_policy_id)
        rationale = self._text("rationale", draft.rationale, 4_000)
        repository = self._repository_provider()
        receipt = repository.get_qualification_receipt(
            context.workspace_id, draft.qualification_receipt_id
        )
        if receipt is None:
            raise ResourceNotFoundError("qualification_receipt", draft.qualification_receipt_id)
        if receipt.get("verdict") != QualificationVerdict.ADMITTED.value:
            raise InvalidEvidenceError(
                "qualification_receipt_id", "Knowledge admission requires ADMITTED"
            )
        stale_reasons = self._receipt_stale_reasons(context, receipt)
        if stale_reasons:
            raise InvalidEvidenceError(
                "qualification_receipt_id",
                "Knowledge admission cannot use stale qualification evidence: "
                + ";".join(stale_reasons),
            )
        admission_id = str(uuid4())
        issued_at = datetime.now(UTC).isoformat()
        admission_receipt = KnowledgeAdmissionReceipt(
            receipt_id=admission_id,
            qualification_receipt_id=receipt["id"],
            qualification_receipt_hash=receipt["receipt_hash"],
            claim_revision_id=receipt["claim_revision_id"],
            claim_semantic_hash=receipt["claim_semantic_hash"],
            profile_id=receipt["profile_id"],
            profile_version=receipt["profile_version"],
            use_scope=DEFAULT_KNOWLEDGE_ADMISSION_POLICY.use_scope,
            admission_policy_id=DEFAULT_KNOWLEDGE_ADMISSION_POLICY.policy_id,
            admission_policy_version=DEFAULT_KNOWLEDGE_ADMISSION_POLICY.version,
            admission_policy_hash=DEFAULT_KNOWLEDGE_ADMISSION_POLICY.content_hash,
            approved_by=context.principal_id,
            rationale=rationale,
            issued_at=issued_at,
        )
        portable = admission_receipt.as_dict()
        result = repository.admit_knowledge(
            context.workspace_id,
            {
                "id": admission_id,
                **{
                    key: value
                    for key, value in portable.items()
                    if key != "knowledge_admission_receipt_id"
                },
                "receipt_hash": admission_receipt.content_hash,
            },
        )
        return {
            **result,
            "knowledge_admission_receipt": self._portable_admission_receipt(
                result["knowledge_admission_receipt"]
            ),
        }

    def list_knowledge_admissions(
        self,
        context: RequestContext,
        claim_id: str | None = None,
    ) -> list[dict]:
        return [
            self._portable_admission_receipt(item)
            for item in self._repository_provider().list_knowledge_admission_receipts(
                context.workspace_id, claim_id
            )
        ]

    def refresh_receipt_binding(
        self,
        context: RequestContext,
        receipt_id: str,
    ) -> dict | None:
        """Refresh the exact receipt's binding before an authority decision."""

        repository = self._repository_provider()
        receipt = repository.get_qualification_receipt(context.workspace_id, receipt_id)
        if receipt is None:
            return None
        binding = repository.get_current_use_binding(
            context.workspace_id,
            receipt["claim_revision_id"],
            receipt["profile_id"],
        )
        if binding is None or binding.get("qualification_receipt_id") != receipt_id:
            return binding
        return self._refresh_binding(context, binding)

    def create_authorization(
        self,
        context: RequestContext,
        actor_id: str,
        draft: AuthorizationGrantDraft,
    ) -> dict:
        repository = self._repository_provider()
        receipt = repository.get_qualification_receipt(
            context.workspace_id, draft.qualification_receipt_id
        )
        if receipt is None:
            raise ResourceNotFoundError("qualification_receipt", draft.qualification_receipt_id)
        binding = repository.get_current_use_binding(
            context.workspace_id,
            receipt["claim_revision_id"],
            receipt["profile_id"],
        )
        if binding is None or binding["state"] != CurrentUseState.CURRENT.value:
            raise InvalidEvidenceError(
                "qualification_receipt_id",
                "Authorization requires a current qualification binding",
            )
        binding = self._refresh_binding(context, binding)
        if binding["state"] != CurrentUseState.CURRENT.value:
            raise InvalidEvidenceError(
                "qualification_receipt_id",
                "Authorization cannot use a stale qualification binding",
            )
        if binding["qualification_receipt_id"] != receipt["id"]:
            raise InvalidEvidenceError(
                "qualification_receipt_id",
                "Authorization requires the receipt selected by the current binding",
            )
        if draft.max_calls < 1 or draft.max_calls > 1_000_000:
            raise InvalidEvidenceError("max_calls", "max_calls is outside policy bounds")
        if draft.expires_at is not None:
            try:
                expiry = datetime.fromisoformat(draft.expires_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise InvalidEvidenceError(
                    "expires_at", "expires_at must be an ISO-8601 timestamp"
                ) from exc
            if expiry.tzinfo is None or expiry <= datetime.now(UTC):
                raise InvalidEvidenceError(
                    "expires_at", "expires_at must be a future timezone-aware timestamp"
                )
        action = self._text("action", draft.action, 200)
        target = self._text("target", draft.target, 1_000)
        grant_payload = {
            "qualification_receipt_id": receipt["id"],
            "qualification_receipt_hash": receipt["receipt_hash"],
            "actor_id": self._text("actor_id", actor_id, 200),
            "action": action,
            "target": target,
            "scope": redact(draft.scope),
            "conditions": redact(draft.conditions),
            "expires_at": draft.expires_at,
            "budget": redact(draft.budget),
            "max_calls": draft.max_calls,
            "policy_version": AUTHORIZATION_POLICY_VERSION,
        }
        return repository.create_authorization_grant(
            context.workspace_id,
            {
                **grant_payload,
                "grant_receipt": canonical_hash(grant_payload),
                "created_by": context.principal_id,
            },
        )

    def qualified_search(
        self,
        context: RequestContext,
        query: str,
        profile_id: str,
        limit: int = 20,
    ) -> list[dict]:
        bindings = self._repository_provider().list_current_use_bindings(
            context.workspace_id, profile_id, CurrentUseState.CURRENT.value
        )
        bindings = [self._refresh_binding(context, item) for item in bindings]
        bindings = [item for item in bindings if item["state"] == CurrentUseState.CURRENT.value]
        by_claim = {item["claim_revision_id"]: item for item in bindings}
        candidates = self._repository_provider().search_memory(
            context.workspace_id, query, min(max(limit * 10, 50), 500)
        )
        results = []
        for item in candidates:
            claim_id = item.get("id") or item.get("source_id")
            if item.get("kind") != "research_claim" or claim_id not in by_claim:
                continue
            binding = by_claim[claim_id]
            receipt = self.get_receipt(context, binding["qualification_receipt_id"])
            results.append(
                {
                    **item,
                    "qualification": receipt,
                    "currentness": binding["state"],
                    "blockers": receipt["blockers"],
                    "source_closure": receipt["evidence_closure_hash"],
                }
            )
            if len(results) >= limit:
                break
        return results

    def _build_closure(
        self, context: RequestContext, claim: dict, profile_id: str
    ) -> EvidenceClosure:
        repository = self._repository_provider()
        computed_hash = claim_semantic_hash(claim)
        research_case = repository.get_research_case(
            context.workspace_id, claim["research_case_id"]
        )
        origin_lineage = (
            (research_case.get("metadata") or {}).get("origin_lineage", {})
            if research_case is not None
            else {}
        )
        nodes: list[dict[str, Any]] = [
            {
                "node_type": "claim_revision",
                "node_id": claim["id"],
                "content_hash": computed_hash,
                "payload": {
                    key: claim.get(key)
                    for key in (
                        "claim_key",
                        "revision_number",
                        "statement",
                        "claim_type",
                        "scope",
                        "definitions",
                        "negative_boundaries",
                        "dependency_claim_ids",
                        "parent_revision_id",
                        "semantic_hash",
                        "lifecycle_status",
                    )
                }
                | {"origin_lineage": origin_lineage},
            }
        ]
        edges: list[dict[str, Any]] = []
        for source in claim.get("sources", []):
            nodes.append(
                {
                    "node_type": "source",
                    "node_id": source["id"],
                    "content_hash": source["content_hash"],
                    "payload": {
                        key: source.get(key)
                        for key in ("source_key", "locator", "status", "content_hash")
                    },
                }
            )
            edges.append(
                self._edge(
                    "source",
                    source["id"],
                    "claim_revision",
                    claim["id"],
                    EvidenceEdgeType.DERIVED_FROM,
                    source["content_hash"],
                    computed_hash,
                )
            )
        attempts = repository.list_research_verification_attempts(context.workspace_id, claim["id"])
        invalidated_attempts: dict[str, list[str]] = {}
        for invalidation in repository.list_invalidation_decisions(
            context.workspace_id, claim["id"]
        ):
            if invalidation["target_scope"] == "proof_attempt":
                invalidated_attempts.setdefault(
                    invalidation["target_attempt_id"], []
                ).append(invalidation["id"])
        artifact_ids: set[str] = set()
        for attempt in attempts:
            artifact_ids.update(attempt.get("artifact_ids") or [])
            payload = {
                key: attempt.get(key)
                for key in (
                    "kind",
                    "outcome",
                    "method",
                    "scope",
                    "input_digest",
                    "output_digest",
                    "run_id",
                    "plan_id",
                    "validation_modality",
                    "verifier_lineage",
                    "artifact_ids",
                )
            }
            if attempt["id"] in invalidated_attempts:
                payload["invalidated_by"] = sorted(invalidated_attempts[attempt["id"]])
            nodes.append(
                {
                    "node_type": "verification_attempt",
                    "node_id": attempt["id"],
                    "content_hash": canonical_hash(payload),
                    "payload": payload,
                }
            )
            edges.append(
                self._edge(
                    "claim_revision",
                    claim["id"],
                    "verification_attempt",
                    attempt["id"],
                    EvidenceEdgeType.VERIFIED_BY,
                    computed_hash,
                    canonical_hash(payload),
                )
            )
        for artifact_id in sorted(artifact_ids):
            artifact = repository.get_artifact(context.workspace_id, artifact_id)
            if artifact is None:
                continue
            nodes.append(
                {
                    "node_type": "artifact",
                    "node_id": artifact_id,
                    "content_hash": artifact["content_hash"],
                    "payload": {
                        key: artifact.get(key)
                        for key in (
                            "name",
                            "kind",
                            "media_type",
                            "content_text",
                            "uri",
                            "content_hash",
                            "metadata",
                            "run_id",
                        )
                    },
                }
            )
            for attempt in attempts:
                if artifact_id in (attempt.get("artifact_ids") or []):
                    edges.append(
                        self._edge(
                            "artifact",
                            artifact_id,
                            "verification_attempt",
                            attempt["id"],
                            EvidenceEdgeType.VERIFIED_BY,
                            artifact["content_hash"],
                            None,
                        )
                    )
        for dependency_id in dict.fromkeys(claim.get("dependency_claim_ids") or []):
            binding = repository.get_current_use_binding(
                context.workspace_id, dependency_id, profile_id
            )
            if binding is not None:
                binding = self._refresh_binding(context, binding)
            payload = binding or {
                "claim_revision_id": dependency_id,
                "profile_id": profile_id,
                "state": "missing",
            }
            nodes.append(
                {
                    "node_type": "dependency_binding",
                    "node_id": dependency_id,
                    "content_hash": canonical_hash(payload),
                    "payload": payload,
                }
            )
            edges.append(
                self._edge(
                    "dependency_binding",
                    dependency_id,
                    "claim_revision",
                    claim["id"],
                    EvidenceEdgeType.DEPENDS_ON,
                    canonical_hash(payload),
                    computed_hash,
                )
            )
        nodes.sort(key=lambda item: (item["node_type"], item["node_id"]))
        edges.sort(
            key=lambda item: (
                item["source_node_type"],
                item["source_node_id"],
                item["target_node_type"],
                item["target_node_id"],
                item["edge_type"],
            )
        )
        limitations = []
        if not claim.get("semantic_hash"):
            limitations.append("legacy_claim_missing_persisted_semantic_hash")
        elif claim["semantic_hash"] != computed_hash:
            limitations.append("statement_drift")
        return EvidenceClosure(
            claim_revision_id=claim["id"],
            claim_semantic_hash=computed_hash,
            nodes=tuple(nodes),
            edges=tuple(edges),
            limitations=tuple(limitations),
        )

    def _refresh_binding(
        self,
        context: RequestContext,
        binding: dict,
        *,
        _path: frozenset[tuple[str, str]] | None = None,
    ) -> dict:
        """Lazily propagate semantic/dependency taint without erasing history."""

        if binding["state"] != CurrentUseState.CURRENT.value:
            return binding
        repository = self._repository_provider()
        binding_key = (binding["claim_revision_id"], binding["profile_id"])
        path = _path or frozenset()
        if binding_key in path:
            return repository.upsert_current_use_binding(
                context.workspace_id,
                {
                    **binding,
                    "state": CurrentUseState.STALE.value,
                    "stale_reason": "dependency_cycle",
                    "bound_by": "system:taint-propagation",
                },
            )
        receipt = repository.get_qualification_receipt(
            context.workspace_id, binding["qualification_receipt_id"]
        )
        stale_reasons = (
            ["qualification_subject_missing"]
            if receipt is None
            else self._receipt_stale_reasons(
                context,
                receipt,
                _path=path | {binding_key},
            )
        )
        if not stale_reasons:
            return binding
        return repository.upsert_current_use_binding(
            context.workspace_id,
            {
                **binding,
                "state": CurrentUseState.STALE.value,
                "stale_reason": ";".join(sorted(set(stale_reasons))),
                "bound_by": "system:taint-propagation",
            },
        )

    def _receipt_stale_reasons(
        self,
        context: RequestContext,
        receipt: dict,
        *,
        _path: frozenset[tuple[str, str]] | None = None,
    ) -> list[str]:
        """Evaluate historical receipt currentness without creating a binding."""

        repository = self._repository_provider()
        claim = repository.get_research_claim(context.workspace_id, receipt["claim_revision_id"])
        stale_reasons: list[str] = []
        for invalidation in repository.list_invalidation_decisions(
            context.workspace_id, receipt["claim_revision_id"]
        ):
            if (
                invalidation["target_scope"] == "claim_revision"
                or invalidation["target_receipt_id"] == receipt["id"]
            ):
                stale_reasons.append(f"invalidation_decision:{invalidation['id']}")
        if claim is None:
            stale_reasons.append("qualification_subject_missing")
        elif receipt["claim_semantic_hash"] != claim_semantic_hash(claim):
            stale_reasons.append("claim_semantic_hash_changed")
        evaluations = repository.list_qualification_evaluations(
            context.workspace_id,
            receipt["claim_revision_id"],
            receipt["profile_id"],
        )
        evaluation = next(
            (item for item in evaluations if item["id"] == receipt["evaluation_id"]),
            None,
        )
        if evaluation is None:
            stale_reasons.append("qualification_evaluation_missing")
            return sorted(set(stale_reasons))
        path = _path or frozenset()
        closure = evaluation.get("evidence_closure", {})
        for node in closure.get("nodes", []):
            if node.get("node_type") != "dependency_binding":
                continue
            frozen = node.get("payload") or {}
            current = repository.get_current_use_binding(
                context.workspace_id,
                node.get("node_id"),
                receipt["profile_id"],
            )
            if current is not None:
                current = self._refresh_binding(context, current, _path=path)
            if (
                current is None
                or current.get("state") != CurrentUseState.CURRENT.value
                or current.get("qualification_receipt_id") != frozen.get("qualification_receipt_id")
            ):
                stale_reasons.append(f"dependency_binding_changed:{node.get('node_id')}")
        return sorted(set(stale_reasons))

    @staticmethod
    def _edge(
        source_type: str,
        source_id: str,
        target_type: str,
        target_id: str,
        edge_type: EvidenceEdgeType,
        source_hash: str | None,
        target_hash: str | None,
    ) -> dict[str, Any]:
        if source_type == target_type and source_id == target_id:
            raise InvalidEvidenceError("evidence_edge", "self edges are not allowed")
        return {
            "source_node_type": source_type,
            "source_node_id": source_id,
            "target_node_type": target_type,
            "target_node_id": target_id,
            "edge_type": edge_type.value,
            "source_hash": source_hash,
            "target_hash": target_hash,
            "status": "active",
        }

    @staticmethod
    def derive_verifier_lineage(
        repository: Repository,
        context: RequestContext,
        *,
        run_id: str | None,
        plan_id: str | None,
        modality: ValidationModality,
        artifact_ids: tuple[str, ...],
    ) -> VerifierLineage:
        run = repository.get_run(context.workspace_id, run_id) if run_id else None
        if run_id and run is None:
            raise ResourceNotFoundError("run", run_id)
        plan = (
            repository.get_research_verification_plan(context.workspace_id, plan_id)
            if plan_id
            else None
        )
        if plan_id and plan is None:
            raise ResourceNotFoundError("research_verification_plan", plan_id)
        route = run.get("selected_model") if run else None
        if route and route.startswith("kernel/"):
            route = None
        provider = None
        family = None
        if route:
            provider, _, family = route.partition("/")
            if not family:
                family = provider
                provider = None
        checker_hash = None
        data_hashes = []
        environment_hashes = []
        for artifact_id in artifact_ids:
            artifact = repository.get_artifact(context.workspace_id, artifact_id)
            if artifact is None:
                raise ResourceNotFoundError("artifact", artifact_id)
            metadata = artifact.get("metadata") or {}
            if modality is ValidationModality.KERNEL_CHECK:
                try:
                    certificate = json.loads(artifact.get("content_text") or "")
                except (TypeError, json.JSONDecodeError):
                    certificate = {}
                if isinstance(certificate, dict):
                    checker = certificate.get("checker") or {}
                    checker_hash = checker.get("toolchain_hash") or checker.get("executable_hash")
            if metadata.get("data_snapshot_hash"):
                data_hashes.append(metadata["data_snapshot_hash"])
            if metadata.get("environment_hash"):
                environment_hashes.append(metadata["environment_hash"])
        return VerifierLineage(
            principal_id=context.principal_id,
            organization_id=context.workspace_id,
            run_id=run_id,
            model_provider=provider,
            model_family=family,
            model_route=route,
            prompt_template_hash=(plan.get("content_digest") if plan else None),
            toolchain_hash=checker_hash,
            environment_hash=(
                canonical_hash(sorted(environment_hashes)) if environment_hashes else None
            ),
            data_snapshot_hash=(canonical_hash(sorted(data_hashes)) if data_hashes else None),
            verification_plan_hash=(plan.get("content_digest") if plan else None),
        )

    @staticmethod
    def _independence_summary(closure: EvidenceClosure) -> dict[str, Any]:
        attempts = [item for item in closure.nodes if item["node_type"] == "verification_attempt"]
        claim_node = next(item for item in closure.nodes if item["node_type"] == "claim_revision")
        origin = claim_node["payload"].get("origin_lineage") or {}
        independence_bases = set()
        verification_properties = set()
        limitations = []
        relationships = []
        for attempt_node in attempts:
            attempt = attempt_node["payload"]
            modality = attempt.get("validation_modality")
            lineage = attempt.get("verifier_lineage") or {}
            relationship = {
                "attempt_id": attempt_node["node_id"],
                "same_run": QualificationService._shared_value(lineage, origin, "run_id"),
                "same_agent": QualificationService._shared_value(lineage, origin, "principal_id"),
                "same_model": QualificationService._shared_value(lineage, origin, "model_route"),
                "same_model_family": QualificationService._shared_value(
                    lineage, origin, "model_family"
                ),
                "same_provider": QualificationService._shared_value(
                    lineage, origin, "model_provider"
                ),
                "same_organization": QualificationService._shared_value(
                    lineage, origin, "organization_id"
                ),
                "orthogonal_non_llm_checker": False,
                "physical_world": False,
            }
            if (
                modality == ValidationModality.KERNEL_CHECK.value
                and lineage.get("toolchain_hash")
                and str(lineage.get("principal_id") or "").startswith("system:verifier:")
                and not lineage.get("model_route")
            ):
                verification_properties.add("orthogonal_non_llm_checker")
                relationship["orthogonal_non_llm_checker"] = True
            if modality in {
                ValidationModality.WET_LAB.value,
                ValidationModality.PHYSICAL_EXPERIMENT.value,
                ValidationModality.FIELD_OBSERVATION.value,
            }:
                verification_properties.add("physical_world")
                relationship["physical_world"] = True
            if lineage.get("model_route"):
                limitations.append("model-mediated verification is not independent by itself")
            relationships.append(relationship)
        if not attempts:
            limitations.append("no verifier lineage is present")
        if "orthogonal_non_llm_checker" in verification_properties:
            limitations.append("orthogonal verification does not establish independent authority")
        limitations.append(
            "verifier organization and trust-domain identity are not yet established"
        )
        return {
            "derived": True,
            "qualified": False,
            "basis": sorted(independence_bases),
            "verification_properties": sorted(verification_properties),
            "verifier_count": len(attempts),
            "relationships": relationships,
            "limitations": sorted(set(limitations)),
        }

    @staticmethod
    def _shared_value(left: dict, right: dict, field: str) -> bool | None:
        left_value = left.get(field)
        right_value = right.get(field)
        if not left_value or not right_value:
            return None
        return left_value == right_value

    @staticmethod
    def _portable_receipt(value: dict) -> dict:
        return {
            **value,
            "qualification_receipt_id": value["id"],
            "profile": value["profile_id"],
        }

    @staticmethod
    def _portable_admission_receipt(value: dict) -> dict:
        return {
            **value,
            "knowledge_admission_receipt_id": value["id"],
        }

    @staticmethod
    def _text(field: str, value: str, maximum: int) -> str:
        cleaned = redact_record_text(value.strip(), max_chars=maximum)
        if not cleaned:
            raise InvalidEvidenceError(field, f"{field} is required")
        return cleaned
