"""A data-only, retrospective in-vitro observation Case and deterministic replay."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from ..core.artifacts import ArtifactDraft, ArtifactKind
from ..core.contracts import RequestContext
from ..core.errors import InvalidEvidenceError
from ..core.evidence import ExecutionReceiptDraft, ProtocolDraft, ProtocolStatus
from ..core.qualification import ValidationModality, canonical_hash, claim_semantic_hash
from ..core.research import VerificationAttemptDraft, VerificationKind, VerificationOutcome
from ..database import get_repository
from ..profiles.bio_in_vitro import (
    CLAIM_KIND,
    CLAIM_SCOPE,
    CONTRACT_VERSION,
    NEGATIVE_BOUNDARIES,
    PROFILE,
    VERIFIER_PRINCIPAL,
    canonical,
    certificate,
    checker_source_sha256,
    replay,
    statement,
    validate_witness,
    witness_sha256,
)
from ..redaction import redact, redact_record_text
from ..repository import Repository
from .artifacts import ArtifactService
from .evidence import EvidenceService
from .qualification import QualificationService
from .research_registry import ResearchRegistryService


class BioInVitroCaseService:
    def __init__(self, repository_provider=get_repository) -> None:
        self._repository_provider = repository_provider
        self._artifacts = ArtifactService(repository_provider=repository_provider)
        self._evidence = EvidenceService(repository_provider)
        self._research = ResearchRegistryService(
            repository_provider,
            artifact_service=self._artifacts,
            evidence_service=self._evidence,
        )
        self._qualification = QualificationService(
            repository_provider,
            artifact_service=self._artifacts,
            evidence_service=self._evidence,
        )

    @staticmethod
    def contract() -> dict:
        return {
            "contract_version": CONTRACT_VERSION,
            "profile": PROFILE.as_dict(),
            "profile_hash": PROFILE.content_hash,
            "checker_source_sha256": checker_source_sha256(),
            "input_shape": {
                "study": [
                    "study_id", "completed_at", "biological_model", "assay",
                    "endpoint", "unit", "timepoint",
                ],
                "analysis": ["direction", "minimum_mean_delta"],
                "pairs": ["pair_id", "batch_id", "control", "treated"],
                "pair_count": "3..64",
                "measurement_format": "bounded decimal strings",
            },
            "qualification_meaning": (
                "The exact observed mean difference in the submitted dataset meets "
                "the frozen threshold. This is not an efficacy or safety claim."
            ),
            "limitations": NEGATIVE_BOUNDARIES,
            "no_automatic_knowledge_admission": True,
            "no_experiment_execution": True,
        }

    def submit(self, context: RequestContext, witness: dict) -> dict:
        # Invalid shapes do not enter the ledger. A well-formed but unsupported
        # observation does, with a FAILED attempt and BLOCKED evaluation.
        validate_witness(witness)
        if date.fromisoformat(witness["study"]["completed_at"]) > datetime.now(UTC).date():
            raise InvalidEvidenceError(
                "study.completed_at", "Future-dated studies are outside this retrospective Case"
            )
        witness_text = canonical(witness)
        if redact(witness) != witness:
            raise InvalidEvidenceError("witness", "Witness contains credential-like content")
        witness_text = redact_record_text(witness_text)
        frozen_witness = json.loads(witness_text)
        result = replay(frozen_witness)
        source_hash = witness_sha256(frozen_witness)
        source_contract = self.contract()
        repository: Repository = self._repository_provider()

        protocol = self._evidence.create_protocol(
            context,
            ProtocolDraft(
                name=f"In-vitro paired observation {source_hash[:12]}",
                profile=PROFILE.profile_id,
                purpose="Evaluate one submitted retrospective in-vitro dataset statement.",
                scope=CLAIM_SCOPE,
                completion_predicate="Exact paired-mean replay and frozen threshold decision.",
                stop_conditions=("Malformed measurement data", "Broken source identity"),
                budget={"network_calls": 0, "measurement_pairs_max": 64, "experiment_calls": 0},
                status=ProtocolStatus.FROZEN,
            ),
            content_hash=canonical_hash({"witness": source_hash, "profile": PROFILE.content_hash}),
        )
        session = repository.create_session(
            context.workspace_id, f"In-vitro observation {source_hash[:12]}"
        )
        run = repository.create_run(
            context.workspace_id, session["id"], context.request_id, None,
            "checker/bio-in-vitro-observation-v0",
        )
        source = self._artifacts.create_artifact(
            context,
            ArtifactDraft(
                name="bio-in-vitro-measurements.json",
                kind=ArtifactKind.JSON,
                media_type="application/json",
                content_text=witness_text,
                metadata={
                    "role": "bio_in_vitro_witness",
                    "contract_version": CONTRACT_VERSION,
                    "checker_source_sha256": source_contract["checker_source_sha256"],
                    "submitted_data_only": True,
                },
            ),
            run_id=run["id"],
        )
        if source["content_hash"] != source_hash:
            raise InvalidEvidenceError("witness", "Frozen witness bytes changed")
        repository.append_run_step(
            context.workspace_id, run["id"], 1, "tool",
            "bio.in_vitro.exact_replay", "succeeded",
            canonical({"witness_sha256": source_hash, "profile_hash": PROFILE.content_hash}),
            canonical(result),
            {"checker_source_sha256": source_contract["checker_source_sha256"]},
        )
        repository.finish_run(context.workspace_id, run["id"], "succeeded")
        registration = self._evidence.create_receipt(
            context,
            protocol["id"],
            ExecutionReceiptDraft(
                status="candidate_registered",
                input_digest=source_hash,
                output_digest=canonical_hash(result),
                run_id=run["id"],
                artifact_ids=(source["id"],),
                runtime={"component": "bio.in-vitro.exact-replay", "network_calls": 0},
                metadata={
                    "submitted_data_only": True,
                    "completion_unverified": True,
                    "knowledge_admission": False,
                    "execution_authority": False,
                },
            ),
        )
        semantic_input = {
            "claim_key": f"BIO-{source_hash[:20]}",
            "revision_number": 1,
            "claim_type": CLAIM_KIND,
            "statement": statement(frozen_witness, source_hash),
            "scope": CLAIM_SCOPE,
            "definitions": [],
            "negative_boundaries": NEGATIVE_BOUNDARIES,
            "dependency_claim_ids": [],
            "parent_revision_id": None,
        }
        case = repository.create_research_registry(
            context.workspace_id,
            {
                "protocol_id": protocol["id"],
                "receipt_id": registration["id"],
                "profile": PROFILE.profile_id,
                "name": f"In-vitro observation {source_hash[:12]}",
                "registry_id": f"bio-in-vitro-{source_hash}",
                "registry_version": "0",
                "authority": "submitted_data_only",
                "status": "frozen",
                "source_artifact_id": source["id"],
                "metadata": {
                    "witness_sha256": source_hash,
                    "profile_hash": PROFILE.content_hash,
                    "checker_source_sha256": source_contract["checker_source_sha256"],
                    "completion_unverified": True,
                    "no_automatic_knowledge_admission": True,
                },
            },
            [{
                "ref_key": "measurements",
                "source_key": "measurements",
                "locator": f"artifact:{source['id']}",
                "content_hash": source_hash,
                "status": "frozen",
            }],
            [{
                **semantic_input,
                "source_ref_keys": ["measurements"],
                "method_revision": CONTRACT_VERSION,
                "lifecycle_status": "candidate",
                "closure_status": "closed",
                "status_axes": {"epistemic": "candidate", "knowledge_admission": "none"},
                "blockers": ["qualification_not_evaluated"],
                "semantic_hash": claim_semantic_hash(semantic_input),
            }],
        )
        claim = repository.list_research_claims(
            context.workspace_id, research_case_id=case["id"], limit=1
        )[0]
        checked = certificate(frozen_witness, claim["id"], claim["semantic_hash"])
        certificate_artifact = self._artifacts.create_artifact(
            context,
            ArtifactDraft(
                name="bio-in-vitro-replay-certificate.json",
                kind=ArtifactKind.JSON,
                media_type="application/json",
                content_text=canonical(checked),
                metadata={
                    "role": "bio_in_vitro_certificate",
                    "witness_sha256": source_hash,
                    "checker_source_sha256": source_contract["checker_source_sha256"],
                    "claim_semantic_hash": claim["semantic_hash"],
                },
            ),
            run_id=run["id"],
        )
        verifier_context = RequestContext(
            request_id=context.request_id,
            workspace_id=context.workspace_id,
            principal_id=VERIFIER_PRINCIPAL,
        )
        attempt = self._research.record_verification_attempt(
            verifier_context,
            claim["id"],
            VerificationAttemptDraft(
                kind=VerificationKind.CALCULATION,
                outcome=(
                    VerificationOutcome.PASSED if result["threshold_met"]
                    else VerificationOutcome.FAILED
                ),
                method="Server-owned exact paired-difference replay.",
                scope=CLAIM_SCOPE,
                input_digest=canonical_hash({
                    "witness_sha256": source_hash,
                    "claim_semantic_hash": claim["semantic_hash"],
                }),
                output_digest=certificate_artifact["content_hash"],
                validation_modality=ValidationModality.EXACT_REPLAY,
                run_id=run["id"],
                artifact_ids=(source["id"], certificate_artifact["id"]),
                metadata={"completion_unverified": True, "limitations": NEGATIVE_BOUNDARIES},
            ),
        )
        evaluation = self._qualification.evaluate(context, claim["id"], PROFILE.profile_id)
        return {
            "case_id": case["id"],
            "claim_revision_id": claim["id"],
            "run_id": run["id"],
            "witness_artifact_id": source["id"],
            "certificate_artifact_id": certificate_artifact["id"],
            "attempt_id": attempt["id"],
            "observed_result": result,
            **evaluation,
        }
