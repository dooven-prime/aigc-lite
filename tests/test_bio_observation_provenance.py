"""Same numbers, different source evidence: arithmetic cannot authenticate a lab."""

from __future__ import annotations

import base64
import json
import sqlite3

import pytest
from authority_support import provision_admin
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_bio_in_vitro_case import witness

from app.core.artifacts import ArtifactDraft, ArtifactKind
from app.core.contracts import RequestContext
from app.core.errors import InvalidEvidenceError
from app.core.evidence import ExecutionReceiptDraft, ProtocolDraft, ProtocolStatus
from app.core.qualification import (
    KnowledgeAdmissionDraft,
    ValidationModality,
    canonical_hash,
    claim_semantic_hash,
)
from app.core.research import VerificationAttemptDraft, VerificationKind, VerificationOutcome
from app.profiles.bio_observation_provenance import (
    BOUNDARIES,
    CLAIM_KIND,
    PROFILE_ID,
    SCOPE,
    VERIFIER_PRINCIPAL,
    BioObservationProvenanceVerifier,
    TrustedRecordKey,
    signed_bytes,
    statement,
)
from app.repository import SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.bio_in_vitro_case import BioInVitroCaseService
from app.services.evidence import EvidenceService
from app.services.qualification import DomainVerifierRegistry, QualificationService
from app.services.research_registry import ResearchRegistryService


def _envelope(role: str, issuer: str, private_key: Ed25519PrivateKey, payload: dict) -> dict:
    value = {
        "contract_version": f"bio.{role}-record.v0",
        "issuer_id": issuer,
        "key_id": "key-1",
        "payload": payload,
    }
    value["signature"] = (
        base64.urlsafe_b64encode(private_key.sign(signed_bytes(value))).decode().rstrip("=")
    )
    return value


def _key(role: str, issuer: str, private_key: Ed25519PrivateKey) -> TrustedRecordKey:
    return TrustedRecordKey(
        role,
        issuer,
        "key-1",
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ),
    )


def _artifact(service: ArtifactService, context: RequestContext, role: str, value: dict) -> dict:
    return service.create_artifact(
        context,
        ArtifactDraft(
            name=f"{role}.json",
            kind=ArtifactKind.JSON,
            media_type="application/json",
            content_text=json.dumps(value, ensure_ascii=False, sort_keys=True),
            metadata={"role": role},
        ),
    )


def _provenance_case(
    repository, context, arithmetic, records, *, pass_attempt=False, case_label=""
):
    provider = lambda: repository  # noqa: E731
    evidence = EvidenceService(provider)
    artifacts = ArtifactService(repository_provider=provider)
    research = ResearchRegistryService(provider)
    protocol = evidence.create_protocol(
        context,
        ProtocolDraft(
            name="Record-origin comparison",
            profile=PROFILE_ID,
            purpose="Check exact record binding, not experiment quality.",
            scope=SCOPE,
            completion_predicate="Enrolled signatures and cross-record identity check.",
            stop_conditions=("Missing record",),
            budget={"network_calls": 0},
            status=ProtocolStatus.FROZEN,
        ),
        content_hash=canonical_hash(
            {
                "arithmetic_case": arithmetic["case_id"],
                "records": bool(records),
                "case_label": case_label,
            }
        ),
    )
    registration = evidence.create_receipt(
        context,
        protocol["id"],
        ExecutionReceiptDraft(
            status="candidate_registered",
            input_digest=arithmetic["observed_result"]["witness_sha256"],
            output_digest=canonical_hash({"records": bool(records)}),
            artifact_ids=(arithmetic["witness_artifact_id"],),
            metadata={"no_automatic_knowledge_admission": True},
        ),
    )
    source_nodes = {
        "witness": repository.get_artifact(context.workspace_id, arithmetic["witness_artifact_id"]),
    }
    if records:
        source_nodes["instrument"] = _artifact(
            artifacts, context, "bio_provenance_instrument", records[0]
        )
        records[1]["payload"]["instrument_sha256"] = source_nodes["instrument"]["content_hash"]
        # The signature covers the final content-addressed instrument reference.
        records[1]["signature"] = (
            base64.urlsafe_b64encode(records[2].sign(signed_bytes(records[1]))).decode().rstrip("=")
        )
        source_nodes["custodian"] = _artifact(
            artifacts, context, "bio_provenance_custodian", records[1]
        )
    frozen_hash = source_nodes["witness"]["content_hash"]
    semantic_input = {
        "claim_key": f"BIO-PROVENANCE-{arithmetic['case_id']}-{case_label}",
        "revision_number": 1,
        "claim_type": CLAIM_KIND,
        "statement": statement(frozen_hash),
        "scope": SCOPE,
        "definitions": [],
        "negative_boundaries": BOUNDARIES,
        "dependency_claim_ids": [],
        "parent_revision_id": None,
    }
    case = repository.create_research_registry(
        context.workspace_id,
        {
            "protocol_id": protocol["id"],
            "receipt_id": registration["id"],
            "profile": PROFILE_ID,
            "name": "Record provenance candidate",
            "registry_id": f"bio-provenance-{arithmetic['case_id']}-{case_label}",
            "registry_version": "0",
            "authority": "candidate_only",
            "status": "frozen",
            "source_artifact_id": arithmetic["witness_artifact_id"],
        },
        [
            {
                "ref_key": role,
                "source_key": role,
                "locator": f"artifact:{node['id']}",
                "content_hash": node["content_hash"],
                "status": "frozen",
            }
            for role, node in source_nodes.items()
        ],
        [
            {
                **semantic_input,
                "source_ref_keys": list(source_nodes),
                "method_revision": PROFILE_ID,
                "lifecycle_status": "candidate",
                "closure_status": "closed" if records else "blocked",
                "status_axes": {"epistemic": "candidate"},
                "blockers": [] if records else ["instrument_and_sample_chain_missing"],
                "semantic_hash": claim_semantic_hash(semantic_input),
            }
        ],
    )
    claim = repository.list_research_claims(context.workspace_id, research_case_id=case["id"])[0]
    if pass_attempt:
        research.record_verification_attempt(
            RequestContext(context.request_id, context.workspace_id, VERIFIER_PRINCIPAL),
            claim["id"],
            VerificationAttemptDraft(
                kind=VerificationKind.SOURCE_AUDIT,
                outcome=VerificationOutcome.PASSED,
                method="Enrolled Ed25519 record signatures and exact cross-record replay.",
                scope=SCOPE,
                input_digest=canonical_hash(
                    {
                        "claim_semantic_hash": claim["semantic_hash"],
                        "sources": {
                            role: node["content_hash"] for role, node in source_nodes.items()
                        },
                    }
                ),
                output_digest=canonical_hash({"record_binding": "passed"}),
                validation_modality=ValidationModality.EXACT_REPLAY,
                artifact_ids=tuple(node["id"] for node in source_nodes.values()),
            ),
        )
    return claim


def _signed_records(submitted, instrument_key, custodian_key, sample_ids):
    instrument = _envelope(
        "instrument",
        "device-registry-1",
        instrument_key,
        {"study": submitted["study"], "pairs": submitted["pairs"]},
    )
    custodian = _envelope(
        "custodian",
        "sample-office-1",
        custodian_key,
        {
            "study_id": submitted["study"]["study_id"],
            "instrument_sha256": "pending",
            "samples": [
                {
                    "pair_id": pair["pair_id"],
                    "sample_id": sample_id,
                    "collected_at": "2026-09-01T08:00:00Z",
                    "assayed_at": "2026-09-01T09:00:00Z",
                }
                for pair, sample_id in zip(submitted["pairs"], sample_ids, strict=True)
            ],
        },
    )
    return instrument, custodian, custodian_key


def _independence_candidate(repository, context, provenance_claims, assertion):
    """Untrusted broader claim referring to L1 evidence; no L2 gate exists yet."""
    original_case = repository.get_research_case(
        context.workspace_id, provenance_claims[0]["research_case_id"]
    )
    evidence = EvidenceService(lambda: repository)
    protocol = evidence.create_protocol(
        context,
        ProtocolDraft(
            name="Experiment-independence proposal",
            profile="bio.experimental-independence.candidate.v0",
            purpose="Record a claim that L1 cannot qualify.",
            scope="Candidate biological or experiment-level independence assertion.",
            completion_predicate="No L2 verifier is registered in this slice.",
            stop_conditions=("Unverified independence",),
            budget={"network_calls": 0},
            status=ProtocolStatus.FROZEN,
        ),
        content_hash=canonical_hash(
            {
                "assertion": assertion,
                "source_claim_ids": [item["id"] for item in provenance_claims],
            }
        ),
    )
    registration = evidence.create_receipt(
        context,
        protocol["id"],
        ExecutionReceiptDraft(
            status="candidate_registered",
            input_digest=canonical_hash([item["id"] for item in provenance_claims]),
            output_digest=canonical_hash({"assertion": assertion}),
            artifact_ids=(original_case["source_artifact_id"],),
            metadata={"qualification_not_established": True},
        ),
    )
    sources = [
        {
            "ref_key": f"case{index}_{source['source_key']}",
            "source_key": f"case{index}_{source['source_key']}",
            "locator": source["locator"],
            "content_hash": source["content_hash"],
            "status": source["status"],
        }
        for index, claim in enumerate(provenance_claims)
        for source in claim["sources"]
    ]
    semantic_input = {
        "claim_key": f"BIO-INDEPENDENCE-{provenance_claims[0]['id']}",
        "revision_number": 1,
        "claim_type": "in_vitro_experiment_independence",
        "statement": assertion,
        "scope": "Biological or experiment-level independence is only a candidate assertion.",
        "definitions": [],
        "negative_boundaries": ["L1 record signatures do not establish independence."],
        "dependency_claim_ids": [item["id"] for item in provenance_claims],
        "parent_revision_id": None,
    }
    case = repository.create_research_registry(
        context.workspace_id,
        {
            "protocol_id": protocol["id"],
            "receipt_id": registration["id"],
            "profile": "bio.experimental-independence.candidate.v0",
            "name": "Unverified experiment-independence assertion",
            "registry_id": f"bio-independence-{provenance_claims[0]['id']}",
            "registry_version": "0",
            "authority": "candidate_only",
            "status": "frozen",
            "source_artifact_id": original_case["source_artifact_id"],
        },
        sources,
        [
            {
                **semantic_input,
                "source_ref_keys": [item["ref_key"] for item in sources],
                "method_revision": "bio.experimental-independence.candidate.v0",
                "lifecycle_status": "candidate",
                "closure_status": "blocked",
                "status_axes": {"epistemic": "candidate"},
                "blockers": ["no_biological_independence_verifier"],
                "semantic_hash": claim_semantic_hash(semantic_input),
            }
        ],
    )
    return repository.list_research_claims(context.workspace_id, research_case_id=case["id"])[0]


def test_same_measurements_do_not_inherit_laboratory_provenance(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "provenance.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    context = RequestContext("bio-provenance-test", "workspace", "research-admin")
    bio = BioInVitroCaseService(provider)
    submitted = witness()
    signed_arithmetic = bio.submit(context, submitted)
    manual_data = witness()
    manual_data["study"]["study_id"] = "retrospective-assay-manual"
    manual_arithmetic = bio.submit(context, manual_data)
    assert signed_arithmetic["evaluation"]["verdict"] == "ADMITTED"
    assert manual_arithmetic["evaluation"]["verdict"] == "ADMITTED"
    assert (
        signed_arithmetic["observed_result"]["pair_deltas"]
        == manual_arithmetic["observed_result"]["pair_deltas"]
    )
    assert (
        signed_arithmetic["observed_result"]["mean_delta_exact"]
        == manual_arithmetic["observed_result"]["mean_delta_exact"]
    )

    instrument_key = Ed25519PrivateKey.generate()
    custodian_key = Ed25519PrivateKey.generate()
    verifier = BioObservationProvenanceVerifier(
        (
            _key("instrument", "device-registry-1", instrument_key),
            _key("custodian", "sample-office-1", custodian_key),
        )
    )
    assert verifier.profile.content_hash != BioObservationProvenanceVerifier().profile.content_hash
    registry = DomainVerifierRegistry()
    registry.register(verifier)
    gate = QualificationService(provider, registry=registry)
    instrument = _envelope(
        "instrument",
        "device-registry-1",
        instrument_key,
        {
            "study": submitted["study"],
            "pairs": submitted["pairs"],
        },
    )
    custodian = _envelope(
        "custodian",
        "sample-office-1",
        custodian_key,
        {
            "study_id": submitted["study"]["study_id"],
            "instrument_sha256": "pending",
            "samples": [
                {
                    "pair_id": item["pair_id"],
                    "sample_id": f"sample-{index}",
                    "collected_at": "2026-09-01T08:00:00Z",
                    "assayed_at": "2026-09-01T09:00:00Z",
                }
                for index, item in enumerate(submitted["pairs"])
            ],
        },
    )
    signed_claim = _provenance_case(
        repository,
        context,
        signed_arithmetic,
        (instrument, custodian, custodian_key),
        pass_attempt=True,
    )
    manual_claim = _provenance_case(repository, context, manual_arithmetic, None)
    signed = gate.evaluate(context, signed_claim["id"], PROFILE_ID)
    manual = gate.evaluate(context, manual_claim["id"], PROFILE_ID)
    assert signed["evaluation"]["verdict"] == "ADMITTED"
    assert signed["qualification_receipt"] is not None
    assert signed["qualification_receipt"]["evidence_vector"]["external_reality"] == "undetermined"
    assert (
        gate.evaluate(context, signed_arithmetic["claim_revision_id"], PROFILE_ID)["evaluation"][
            "verdict"
        ]
        == "NOT_APPLICABLE"
    )
    assert manual["evaluation"]["verdict"] == "UNRESOLVED"
    assert manual["qualification_receipt"] is None
    assert "instrument_record" in manual["evaluation"]["blockers"]
    assert gate.qualified_search(context, "retrospective-assay-1", PROFILE_ID) == []
    assert repository.list_current_use_bindings(context.workspace_id, PROFILE_ID) == []
    assert repository.list_authorization_grants(context.workspace_id) == []

    instrument_source = next(
        source for source in signed_claim["sources"] if source["source_key"] == "instrument"
    )
    instrument_id = instrument_source["locator"].removeprefix("artifact:")
    with sqlite3.connect(tmp_path / "provenance.db") as db:
        db.execute(
            "UPDATE artifacts SET content_text = ? WHERE id = ?",
            ('{"tampered": true}', instrument_id),
        )
    rechecked = gate.evaluate(context, signed_claim["id"], PROFILE_ID)
    assert rechecked["evaluation"]["verdict"] == "UNRESOLVED"
    assert rechecked["qualification_receipt"] is None
    assert (
        len(repository.list_qualification_receipts(context.workspace_id, signed_claim["id"])) == 1
    )
    with pytest.raises(InvalidEvidenceError, match="stale qualification evidence"):
        admin_context, authority = provision_admin(repository, context)
        gate.admit_knowledge(
            admin_context,
            KnowledgeAdmissionDraft(
                qualification_receipt_id=signed["qualification_receipt"][
                    "qualification_receipt_id"
                ],
                admission_policy_id="knowledge.default.v1",
                rationale="Trying to reuse a record-origin receipt after source tampering",
            ),
            authority=authority,
        )


def test_unenrolled_record_key_cannot_qualify(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "untrusted.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    context = RequestContext("untrusted-key", "workspace", "research-admin")
    submitted = witness()
    arithmetic = BioInVitroCaseService(provider).submit(context, submitted)
    attacker_instrument = Ed25519PrivateKey.generate()
    attacker_custodian = Ed25519PrivateKey.generate()
    instrument = _envelope(
        "instrument",
        "attacker",
        attacker_instrument,
        {
            "study": submitted["study"],
            "pairs": submitted["pairs"],
        },
    )
    custodian = _envelope(
        "custodian",
        "attacker-2",
        attacker_custodian,
        {
            "study_id": submitted["study"]["study_id"],
            "instrument_sha256": "pending",
            "samples": [
                {
                    "pair_id": item["pair_id"],
                    "sample_id": "one-sample",
                    "collected_at": "2026-09-01",
                    "assayed_at": "2026-09-01",
                }
                for item in submitted["pairs"]
            ],
        },
    )
    claim = _provenance_case(
        repository,
        context,
        arithmetic,
        (instrument, custodian, attacker_custodian),
        pass_attempt=True,
    )
    registry = DomainVerifierRegistry()
    registry.register(BioObservationProvenanceVerifier())  # Production default has no trust roots.
    result = QualificationService(provider, registry=registry).evaluate(
        context, claim["id"], PROFILE_ID
    )
    assert result["evaluation"]["verdict"] == "UNRESOLVED"
    assert result["qualification_receipt"] is None
    assert "instrument_record" in result["evaluation"]["blockers"]


def test_signed_technical_repeats_do_not_qualify_as_biological_replicates(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "technical-repeats.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    context = RequestContext("technical-repeats", "workspace", "research-admin")
    submitted = witness()
    arithmetic = BioInVitroCaseService(provider).submit(context, submitted)
    instrument_key = Ed25519PrivateKey.generate()
    custodian_key = Ed25519PrivateKey.generate()
    registry = DomainVerifierRegistry()
    registry.register(
        BioObservationProvenanceVerifier(
            (
                _key("instrument", "device-registry-1", instrument_key),
                _key("custodian", "sample-office-1", custodian_key),
            )
        )
    )
    gate = QualificationService(provider, registry=registry)
    records = _signed_records(
        submitted, instrument_key, custodian_key, ["Sample-001"] * len(submitted["pairs"])
    )
    assert {item["sample_id"] for item in records[1]["payload"]["samples"]} == {"Sample-001"}
    provenance_claim = _provenance_case(repository, context, arithmetic, records, pass_attempt=True)
    l1 = gate.evaluate(context, provenance_claim["id"], PROFILE_ID)
    assert l1["evaluation"]["verdict"] == "ADMITTED"
    assert l1["qualification_receipt"]["profile"] == PROFILE_ID
    assert (
        l1["qualification_receipt"]["evidence_vector"]["independent_validation"] == "undetermined"
    )
    broader = _independence_candidate(
        repository,
        context,
        [provenance_claim],
        "This in-vitro experiment contains three independent biological replicates.",
    )
    attempted_promotion = gate.evaluate(context, broader["id"], PROFILE_ID)
    assert attempted_promotion["evaluation"]["verdict"] == "NOT_APPLICABLE"
    assert attempted_promotion["qualification_receipt"] is None
    assert repository.list_qualification_receipts(context.workspace_id, broader["id"]) == []
    assert repository.list_current_use_bindings(context.workspace_id, PROFILE_ID) == []


def test_reusing_one_signed_record_across_cases_is_not_independent_experiments(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "record-reuse.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    context = RequestContext("record-reuse", "workspace", "research-admin")
    submitted = witness()
    arithmetic = BioInVitroCaseService(provider).submit(context, submitted)
    instrument_key = Ed25519PrivateKey.generate()
    custodian_key = Ed25519PrivateKey.generate()
    registry = DomainVerifierRegistry()
    registry.register(
        BioObservationProvenanceVerifier(
            (
                _key("instrument", "device-registry-1", instrument_key),
                _key("custodian", "sample-office-1", custodian_key),
            )
        )
    )
    gate = QualificationService(provider, registry=registry)
    records = _signed_records(
        submitted,
        instrument_key,
        custodian_key,
        [f"Sample-{index}" for index in range(len(submitted["pairs"]))],
    )
    first = _provenance_case(
        repository, context, arithmetic, records, pass_attempt=True, case_label="first"
    )
    copied = _provenance_case(
        repository, context, arithmetic, records, pass_attempt=True, case_label="copy"
    )
    assert first["research_case_id"] != copied["research_case_id"]
    assert first["id"] != copied["id"]
    assert {item["source_key"]: item["content_hash"] for item in first["sources"]} == {
        item["source_key"]: item["content_hash"] for item in copied["sources"]
    }
    first_l1 = gate.evaluate(context, first["id"], PROFILE_ID)
    copied_l1 = gate.evaluate(context, copied["id"], PROFILE_ID)
    assert first_l1["evaluation"]["verdict"] == "ADMITTED"
    assert copied_l1["evaluation"]["verdict"] == "ADMITTED"
    assert (
        first_l1["qualification_receipt"]["qualification_receipt_id"]
        != copied_l1["qualification_receipt"]["qualification_receipt_id"]
    )
    for result in (first_l1, copied_l1):
        assert (
            result["qualification_receipt"]["evidence_vector"]["independent_validation"]
            == "undetermined"
        )
    broader = _independence_candidate(
        repository,
        context,
        [first, copied],
        "Two Research Cases establish two independent in-vitro experiments.",
    )
    attempted_promotion = gate.evaluate(context, broader["id"], PROFILE_ID)
    assert attempted_promotion["evaluation"]["verdict"] == "NOT_APPLICABLE"
    assert attempted_promotion["qualification_receipt"] is None
    assert repository.list_current_use_bindings(context.workspace_id, PROFILE_ID) == []
