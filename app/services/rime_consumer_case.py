"""Candidate-only research Case for a frozen RIME finite consumer witness."""

from __future__ import annotations

import hashlib
import json

from ..adapters.research_import.rime_consumer import (
    PROFILE,
    RimeConsumerWitnessAdapter,
    canonical,
    digest,
)
from ..core.artifacts import ArtifactDraft, ArtifactKind
from ..core.contracts import RequestContext
from ..core.errors import InvalidEvidenceError
from ..core.evidence import ExecutionReceiptDraft, ProtocolDraft, ProtocolStatus
from ..core.qualification import ValidationModality
from ..core.research import (
    ClaimClosureStatus,
    ClaimRevisionDraft,
    ResearchCaseDraft,
    ResearchCaseStatus,
    VerificationAttemptDraft,
    VerificationKind,
    VerificationOutcome,
)
from ..database import get_repository
from ..redaction import redact_record_text
from .artifacts import ArtifactService
from .evidence import EvidenceService
from .research_registry import ResearchRegistryService

_MATHEMATICAL_WITNESS_FIELDS = (
    "environment", "prefix", "selected_block", "commands"
)
_LEGACY_UNMATERIALIZED_COMMIT = "c211517c8f170b682db9d45faeb13e9837b73b0b"


class RimeConsumerCaseService:
    def __init__(self, adapter: RimeConsumerWitnessAdapter | None = None) -> None:
        self.adapter = adapter or RimeConsumerWitnessAdapter()
        self.artifacts = ArtifactService()
        self.evidence = EvidenceService()
        self.research = ResearchRegistryService()

    @staticmethod
    def _mathematical_witness_hash(witness: dict) -> str:
        return digest({key: witness[key] for key in _MATHEMATICAL_WITNESS_FIELDS})

    def _prior_link(self, context: RequestContext, witness: dict, repository) -> dict | None:
        prior_case_id = witness.get("prior_case_id")
        if prior_case_id is None:
            return None
        prior_case = repository.get_research_case(context.workspace_id, prior_case_id)
        if prior_case is None or prior_case["profile"] != PROFILE:
            raise InvalidEvidenceError("prior_case_id", "Prior RIME Case is not in this workspace")
        claims = repository.list_research_claims(
            context.workspace_id, research_case_id=prior_case_id
        )
        claim = next((item for item in claims if item["claim_key"] == "finite-consumer-replay"), None)
        if claim is None:
            raise InvalidEvidenceError("prior_case_id", "Prior Case has no finite replay claim")
        source = next((item for item in claim["sources"] if item["source_key"] == "witness"), None)
        if source is None or not source["locator"].startswith("artifact:"):
            raise InvalidEvidenceError("prior_case_id", "Prior Case witness is unavailable")
        artifact = repository.get_artifact(
            context.workspace_id, source["locator"].removeprefix("artifact:")
        )
        if (artifact is None or artifact["content_hash"] != source["content_hash"] or
            hashlib.sha256(artifact["content_text"].encode("utf-8")).hexdigest() !=
            artifact["content_hash"]):
            raise InvalidEvidenceError("prior_case_id", "Prior witness Artifact identity is broken")
        try:
            prior_witness = json.loads(artifact["content_text"])
            prior_math_hash = self._mathematical_witness_hash(prior_witness)
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidEvidenceError("prior_case_id", "Prior witness cannot be decoded") from exc
        if prior_math_hash != self._mathematical_witness_hash(witness):
            raise InvalidEvidenceError("prior_case_id", "Prior Case is not the same mathematical witness")
        attempts = repository.list_research_verification_attempts(
            context.workspace_id, claim["id"]
        )
        if not attempts:
            raise InvalidEvidenceError("prior_case_id", "Prior Case has no recorded attempt")
        source_artifact = repository.get_artifact(
            context.workspace_id, prior_case["source_artifact_id"]
        )
        expected_contract_hash = (prior_case.get("metadata") or {}).get("contract_sha256")
        if (source_artifact is None or
            (expected_contract_hash and source_artifact["content_hash"] != expected_contract_hash) or
            hashlib.sha256(source_artifact["content_text"].encode("utf-8")).hexdigest() !=
            source_artifact["content_hash"]):
            raise InvalidEvidenceError("prior_case_id", "Prior source identity is broken")
        try:
            prior_contract = json.loads(source_artifact["content_text"])
        except (TypeError, ValueError) as exc:
            raise InvalidEvidenceError("prior_case_id", "Prior source identity is unavailable") from exc
        if not isinstance(prior_contract, dict):
            raise InvalidEvidenceError("prior_case_id", "Prior source identity is malformed")
        prior_commit = prior_contract.get("source_commit")
        return {
            "case_id": prior_case_id,
            "claim_revision_id": claim["id"],
            "attempt_ids": [item["id"] for item in attempts],
            "source_commit": prior_commit,
            "source_closure": (
                "unmaterialized_at_declared_commit"
                if prior_commit == _LEGACY_UNMATERIALIZED_COMMIT
                else "historical_not_reinterpreted"
            ),
        }

    def submit(self, context: RequestContext, witness: dict) -> dict:
        # Structural preflight and deterministic replay happen before any write.
        self.adapter.validate(witness)
        frozen_witness_text = redact_record_text(canonical(witness))
        frozen_witness = json.loads(frozen_witness_text)
        self.adapter.validate(frozen_witness)
        result = self.adapter.replay(frozen_witness)
        result_text = redact_record_text(canonical(result))
        witness_hash = hashlib.sha256(frozen_witness_text.encode("utf-8")).hexdigest()
        result_hash = hashlib.sha256(result_text.encode("utf-8")).hexdigest()
        contract = self.adapter.contract()
        contract_text = redact_record_text(canonical(contract))
        contract_hash = hashlib.sha256(contract_text.encode("utf-8")).hexdigest()
        repository = get_repository()
        mathematical_witness_hash = self._mathematical_witness_hash(frozen_witness)
        prior_link = self._prior_link(context, frozen_witness, repository)
        protocol = self.evidence.create_protocol(
            context,
            ProtocolDraft(
                name=f"RIME finite consumer witness {witness_hash[:12]}",
                profile=PROFILE,
                purpose="Replay one finite Event-Anchored Consumer witness with three deterministic routes.",
                scope="Only this exact witness and frozen checker contract; no general theorem claim.",
                completion_predicate="Log, forest, and placed forward-UFE reports agree at every observed prefix.",
                stop_conditions=("Invalid event registration", "Illegal Carry/Absorb command", "Route or candidate divergence"),
                budget={"network_calls": 0, "q_size_max": 16, "word_length_max": 128, "command_count_max": 128},
                status=ProtocolStatus.FROZEN,
            ),
            content_hash=digest({"contract": contract_hash, "witness": witness_hash}),
        )
        source = self.artifacts.create_artifact(
            context,
            ArtifactDraft(
                name="rime-consumer-source-identity.json",
                kind=ArtifactKind.JSON,
                media_type="application/json",
                content_text=contract_text,
                metadata={"role": "pinned_source_identity", "profile": PROFILE},
            ),
        )
        if source["content_hash"] != contract_hash:
            raise InvalidEvidenceError("contract", "Frozen contract bytes changed during persistence")
        session = repository.create_session(context.workspace_id, f"RIME consumer {witness_hash[:12]}")
        run = repository.create_run(
            context.workspace_id, session["id"], context.request_id, None,
            "checker/rime-consumer-replay-v2",
        )
        witness_artifact = self.artifacts.create_artifact(
            context,
            ArtifactDraft(
                name="rime-consumer-witness.json",
                kind=ArtifactKind.JSON,
                media_type="application/json",
                content_text=frozen_witness_text,
                metadata={"role": "untrusted_witness",
                          "contract_version": contract["contract_version"],
                          "source_manifest_sha256": contract["source_manifest_sha256"],
                          "checker_sha256": contract["checker_sha256"],
                          "mathematical_witness_sha256": mathematical_witness_hash},
            ),
            run_id=run["id"],
        )
        if witness_artifact["content_hash"] != witness_hash:
            raise InvalidEvidenceError("witness", "Frozen witness bytes changed during persistence")
        recorded_result = self.artifacts.create_artifact(
            context,
            ArtifactDraft(
                name="rime-consumer-replay-result.json",
                kind=ArtifactKind.JSON,
                media_type="application/json",
                content_text=result_text,
                metadata={"role": "deterministic_replay_result", "witness_sha256": witness_hash,
                          "checker_sha256": contract["checker_sha256"],
                          "contract_version": contract["contract_version"],
                          "source_manifest_sha256": contract["source_manifest_sha256"],
                          "data_snapshot_hash": witness_hash},
            ),
            run_id=run["id"],
        )
        if recorded_result["content_hash"] != result_hash:
            raise InvalidEvidenceError("result", "Checker result bytes changed during persistence")
        sequence = 0
        for item in result["prefix_checks"]:
            sequence += 1
            repository.append_run_step(
                context.workspace_id, run["id"], sequence, "tool",
                "rime.consumer.seed-prefix", "succeeded",
                canonical({"witness_sha256": witness_hash, "letter": item["letter"]}),
                canonical({"partition": item["partition"],
                           "placed_packets": item["placed_packets"],
                           "fusion_count": item["fusion_count"]}),
                {"prefix_index": item["prefix_index"],
                 "checker_sha256": contract["checker_sha256"]},
            )
        for item in result["trace"]:
            sequence += 1
            repository.append_run_step(
                context.workspace_id, run["id"], sequence, "tool",
                "rime.consumer.replay-prefix", "succeeded",
                canonical({"witness_sha256": witness_hash, "command": item["command"]}),
                canonical({"report": item["report"],
                           "placed_packets": item["placed_packets"]}),
                {"prefix_index": item["index"], "checker_sha256": contract["checker_sha256"]},
            )
        if result["failure"] is not None:
            failure = result["failure"]
            sequence += 1
            repository.append_run_step(
                context.workspace_id, run["id"], sequence, "tool",
                "rime.consumer.replay-failure", "failed",
                canonical({"witness_sha256": witness_hash, "prefix_index": failure["index"]}),
                canonical(failure), {"checker_sha256": contract["checker_sha256"]},
            )
        # A checker finding FAILED is still a successfully completed checker run.
        repository.finish_run(context.workspace_id, run["id"], "succeeded")
        import_receipt = self.evidence.create_receipt(
            context, protocol["id"],
            ExecutionReceiptDraft(
                status="candidate_imported", input_digest=witness_hash,
                output_digest=digest({"witness": witness_hash, "contract": contract_hash}),
                run_id=run["id"], artifact_ids=(source["id"], witness_artifact["id"]),
                runtime={"importer": self.adapter.importer_id, "version": self.adapter.version,
                         "network_calls": 0},
                metadata={"candidate_only": True,
                          "contract_version": contract["contract_version"],
                          "source_commit": contract["source_commit"],
                          "source_manifest_sha256": contract["source_manifest_sha256"],
                          "checker_sha256": contract["checker_sha256"],
                          "prior_link": prior_link},
            ),
        )
        case = ResearchCaseDraft(
            name=f"RIME Event-Anchored Consumer witness {witness_hash[:12]}",
            profile=PROFILE,
            registry_id=f"rime-consumer-{witness_hash}",
            registry_version="2", authority="candidate-only external witness",
            status=ResearchCaseStatus.FROZEN,
            protocol_id=protocol["id"], receipt_id=import_receipt["id"],
            source_artifact_id=source["id"],
            metadata={"witness_sha256": witness_hash,
                      "mathematical_witness_sha256": mathematical_witness_hash,
                      "contract_sha256": contract_hash,
                      "contract_version": contract["contract_version"],
                      "source_commit": contract["source_commit"],
                      "source_manifest_sha256": contract["source_manifest_sha256"],
                      "checker_sha256": contract["checker_sha256"],
                      "prior_link": prior_link,
                      "no_automatic_qualification": True},
        )
        claim = ClaimRevisionDraft(
            claim_key="finite-consumer-replay",
            statement=(f"For exact witness sha256:{witness_hash}, the finite Event-Anchored "
                       f"Consumer replay under {contract['contract_version']} agrees with "
                       "the pinned deterministic checker."),
            claim_type="finite_consumer_replay", scope="This witness only; not the All-N theorem.",
            method_revision=contract["checker_sha256"], lifecycle_status="candidate",
            closure_status=ClaimClosureStatus.BLOCKED,
            blockers=("Finite replay does not establish the general consumer theorem or independent authority.",),
            status_axes={"epistemic": "candidate", "knowledge_admission": "none"},
            source_ref_keys=("witness", "contract/manifest.json", *(
                f"contract/{item['path']}" for item in contract["source_files"]
            )),
        )
        claim_values = self.research._claim_values(claim)
        claim_values["negative_boundaries"] = [
            "No All-N consumer adequacy theorem is proved.",
            "No old B0 closure, independent authority, or current-use admission follows.",
        ]
        claim_values["semantic_hash"] = digest({
            "claim_key": claim.claim_key,
            "statement": claim.statement,
            "claim_type": claim.claim_type,
            "scope": claim.scope,
            "method_revision": claim.method_revision,
            "contract_version": contract["contract_version"],
            "source_commit": contract["source_commit"],
            "source_manifest_sha256": contract["source_manifest_sha256"],
            "negative_boundaries": claim_values["negative_boundaries"],
            "witness_sha256": witness_hash,
        })
        source_root_url = (
            "https://github.com/dooven-prime/rime-lite/blob/"
            f"{contract['source_commit']}/{contract['source_root']}"
        )
        source_refs = [
            {"ref_key": "witness", "source_key": "witness",
             "locator": f"artifact:{witness_artifact['id']}",
             "content_hash": witness_artifact["content_hash"], "status": "frozen"},
            {"ref_key": "contract/manifest.json", "source_key": "rime-contract-manifest",
             "locator": f"{source_root_url}/manifest.json",
             "content_hash": contract["source_manifest_sha256"], "status": "declared"},
        ]
        source_refs.extend(
            {"ref_key": f"contract/{item['path']}",
             "source_key": f"rime-contract-{item['path']}",
             "locator": f"{source_root_url}/{item['path']}",
             "content_hash": item["sha256"], "status": "declared",
             "metadata": {"role": item["role"], "size": item["size"]}}
            for item in contract["source_files"]
        )
        research_case = repository.create_research_registry(
            context.workspace_id, self.research._case_values(case), source_refs,
            [claim_values],
        )
        stored_claim = repository.list_research_claims(
            context.workspace_id, research_case_id=research_case["id"]
        )[0]
        attempt = self.research.record_verification_attempt(
            context, stored_claim["id"],
            VerificationAttemptDraft(
                kind=VerificationKind.REPRODUCTION,
                outcome=(VerificationOutcome.PASSED if result["outcome"] == "passed"
                         else VerificationOutcome.FAILED),
                method="Pinned three-route finite data replay (log / forest / placed-UFE).",
                scope="Exact witness only; no general theorem or current-use authority.",
                input_digest=witness_hash, output_digest=result_hash,
                validation_modality=ValidationModality.EXACT_REPLAY,
                run_id=run["id"],
                artifact_ids=(witness_artifact["id"], recorded_result["id"]),
                metadata={"checker_sha256": contract["checker_sha256"],
                          "claim_semantic_hash": claim_values["semantic_hash"],
                          "contract_version": contract["contract_version"],
                          "source_commit": contract["source_commit"],
                          "source_manifest_sha256": contract["source_manifest_sha256"],
                          "mathematical_witness_sha256": mathematical_witness_hash,
                          "prior_link": prior_link,
                          "limitations": result["limitations"],
                          "qualification_granted": False, "knowledge_admitted": False},
            ),
        )
        return {"case_id": research_case["id"], "claim_revision_id": stored_claim["id"],
                "run_id": run["id"], "verification_attempt_id": attempt["id"],
                "witness_artifact_id": witness_artifact["id"],
                "result_artifact_id": recorded_result["id"],
                "witness_sha256": witness_hash,
                "mathematical_witness_sha256": mathematical_witness_hash,
                "prior_link": prior_link, "result": result,
                "qualification_granted": False, "knowledge_admitted": False}
