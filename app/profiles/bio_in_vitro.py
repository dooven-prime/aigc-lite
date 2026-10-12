"""Finite, data-only qualification for one completed in-vitro observation.

The checker proves arithmetic about submitted measurement pairs. It does not
authenticate laboratory provenance, independence, causality, safety, or efficacy.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..core.errors import InvalidEvidenceError
from ..core.qualification import (
    CriterionReceipt,
    EvidenceAxisState,
    EvidenceClosure,
    QualificationCriterion,
    QualificationDecision,
    QualificationProfile,
    QualificationVerdict,
    ValidationModality,
    canonical_hash,
)

PROFILE_ID = "bio.in_vitro.observed_pair_delta.v0"
CLAIM_KIND = "in_vitro_observed_pair_delta"
CONTRACT_VERSION = "bio.in-vitro.paired-observation.v0"
CERTIFICATE_VERSION = "bio.in-vitro.replay-certificate.v0"
VERIFIER_PRINCIPAL = "system:verifier:bio-in-vitro"
CLAIM_SCOPE = "Exact submitted in-vitro measurement pairs only; no population inference."
NEGATIVE_BOUNDARIES = [
    "Submitted laboratory data and completion date are not externally authenticated.",
    "Distinct pair or batch labels do not establish biological independence.",
    "No causal, clinical efficacy, safety, regulatory, or future-experiment claim follows.",
    "The analysis threshold may have been selected after observing the data.",
]
_NUMBER = re.compile(r"^-?(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,6})?$")
_LABEL = re.compile(r"^[\w .:/+()-]{1,100}$", re.UNICODE)
CHECKER_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

PROFILE = QualificationProfile(
    profile_id=PROFILE_ID,
    version=1,
    claim_kind=CLAIM_KIND,
    policy_version="bio.in_vitro.observed_pair_delta.policy.v0",
    description=(
        "Exact replay of a bounded set of submitted in-vitro paired measurements. "
        "ADMITTED means only that the frozen, dataset-specific mean-difference "
        "statement follows from these bytes; laboratory provenance and biological "
        "or clinical inference remain undetermined. "
        f"Checker source sha256:{CHECKER_SOURCE_SHA256}."
    ),
    criteria=(
        QualificationCriterion("statement_identity", "The ClaimRevision is the server-built dataset statement."),
        QualificationCriterion("source_integrity", "One frozen, intact measurement Artifact is bound as source."),
        QualificationCriterion("exact_replay", "A server-derived certificate matches fresh exact arithmetic."),
        QualificationCriterion("observed_threshold", "The exact observed mean meets the frozen direction and threshold."),
    ),
    accepted_modalities=(ValidationModality.EXACT_REPLAY,),
)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def checker_source_sha256() -> str:
    return CHECKER_SOURCE_SHA256


def _keys(value: Any, expected: set[str], field: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise InvalidEvidenceError(field, f"{field} must contain exactly {sorted(expected)}")
    return value


def _label(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _LABEL.fullmatch(value):
        raise InvalidEvidenceError(field, f"{field} must be a short printable label")
    return value


def _number(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str) or not _NUMBER.fullmatch(value):
        raise InvalidEvidenceError(field, f"{field} must be a bounded decimal string")
    parsed = Decimal(value)
    if positive and parsed <= 0:
        raise InvalidEvidenceError(field, f"{field} must be positive")
    return parsed


def validate_witness(witness: Any) -> dict:
    _keys(witness, {"contract_version", "study", "analysis", "pairs"}, "witness")
    if len(canonical(witness).encode("utf-8")) > 64_000:
        raise InvalidEvidenceError("witness", "Witness exceeds 64 KiB")
    if witness["contract_version"] != CONTRACT_VERSION:
        raise InvalidEvidenceError("contract_version", "Unknown in-vitro witness contract")
    study = _keys(
        witness["study"],
        {"study_id", "completed_at", "biological_model", "assay", "endpoint", "unit", "timepoint"},
        "study",
    )
    for key in ("study_id", "biological_model", "assay", "endpoint", "unit", "timepoint"):
        _label(study[key], f"study.{key}")
    if not isinstance(study["completed_at"], str):
        raise InvalidEvidenceError("study.completed_at", "Expected an ISO calendar date")
    try:
        completed = date.fromisoformat(study["completed_at"])
    except ValueError as exc:
        raise InvalidEvidenceError("study.completed_at", "Expected an ISO calendar date") from exc
    if completed.isoformat() != study["completed_at"]:
        raise InvalidEvidenceError("study.completed_at", "Expected an ISO calendar date")
    analysis = _keys(witness["analysis"], {"direction", "minimum_mean_delta"}, "analysis")
    if analysis["direction"] not in ("increase", "decrease"):
        raise InvalidEvidenceError("analysis.direction", "Expected increase or decrease")
    _number(analysis["minimum_mean_delta"], "analysis.minimum_mean_delta", positive=True)
    pairs = witness["pairs"]
    if not isinstance(pairs, list) or not 3 <= len(pairs) <= 64:
        raise InvalidEvidenceError("pairs", "Expected 3 to 64 matched measurement pairs")
    ids: set[str] = set()
    for index, item in enumerate(pairs):
        _keys(item, {"pair_id", "batch_id", "control", "treated"}, f"pairs[{index}]")
        pair_id = _label(item["pair_id"], f"pairs[{index}].pair_id")
        _label(item["batch_id"], f"pairs[{index}].batch_id")
        _number(item["control"], f"pairs[{index}].control")
        _number(item["treated"], f"pairs[{index}].treated")
        if pair_id in ids:
            raise InvalidEvidenceError("pairs", "pair_id must be unique")
        ids.add(pair_id)
    return witness


def witness_sha256(witness: dict) -> str:
    # ArtifactService persists JSON with standard separators after redaction.
    # The service rejects inputs that would be altered by redaction.
    persisted_text = json.dumps(witness, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(persisted_text.encode("utf-8")).hexdigest()


def statement(witness: dict, source_hash: str) -> str:
    study = witness["study"]
    analysis = witness["analysis"]
    comparison = (
        f">= +{analysis['minimum_mean_delta']}"
        if analysis["direction"] == "increase"
        else f"<= -{analysis['minimum_mean_delta']}"
    )
    labels = ", ".join(
        f"{key}={canonical(study[key])}"
        for key in ("study_id", "biological_model", "assay", "endpoint", "unit", "timepoint")
    )
    return (
        f"For the {len(witness['pairs'])} submitted paired in-vitro measurements "
        f"({labels}; witness sha256:{source_hash}), the observed mean treated-minus-control "
        f"{study['endpoint']} difference is {comparison} {study['unit']}."
    )


def replay(witness: dict) -> dict[str, Any]:
    validate_witness(witness)
    deltas = [
        _number(item["treated"], "treated") - _number(item["control"], "control")
        for item in witness["pairs"]
    ]
    mean = sum((Fraction(delta) for delta in deltas), Fraction()) / len(deltas)
    sample_variance = sum(
        ((Fraction(delta) - mean) ** 2 for delta in deltas), Fraction()
    ) / (len(deltas) - 1)
    threshold = Fraction(_number(witness["analysis"]["minimum_mean_delta"], "threshold"))
    direction = witness["analysis"]["direction"]
    supported = mean >= threshold if direction == "increase" else mean <= -threshold
    return {
        "contract_version": CONTRACT_VERSION,
        "witness_sha256": witness_sha256(witness),
        "pair_count": len(deltas),
        "declared_batch_count": len({item["batch_id"] for item in witness["pairs"]}),
        "pair_deltas": [
            {"pair_id": item["pair_id"], "delta": str(delta)}
            for item, delta in zip(witness["pairs"], deltas, strict=True)
        ],
        "mean_delta_exact": {"numerator": mean.numerator, "denominator": mean.denominator},
        "sample_variance_exact": {
            "numerator": sample_variance.numerator,
            "denominator": sample_variance.denominator,
        },
        "observed_range": {"min": str(min(deltas)), "max": str(max(deltas))},
        "direction_counts": {
            "increase": sum(delta > 0 for delta in deltas),
            "unchanged": sum(delta == 0 for delta in deltas),
            "decrease": sum(delta < 0 for delta in deltas),
        },
        "threshold_met": supported,
        "limitations": NEGATIVE_BOUNDARIES,
    }


def certificate(witness: dict, claim_id: str, semantic_hash: str) -> dict:
    return {
        "contract_version": CERTIFICATE_VERSION,
        "claim_revision_id": claim_id,
        "claim_semantic_hash": semantic_hash,
        "checker_source_sha256": checker_source_sha256(),
        "replay": replay(witness),
    }


def _intact(artifact: dict) -> bool:
    content = artifact["payload"].get("content_text")
    return (
        isinstance(content, str)
        and hashlib.sha256(content.encode("utf-8")).hexdigest() == artifact["content_hash"]
        and artifact["content_hash"] == artifact["payload"].get("content_hash")
    )


def _criterion(code: str, ok: bool, reason: str, ids: tuple[str, ...] = ()) -> CriterionReceipt:
    return CriterionReceipt(
        code,
        EvidenceAxisState.SATISFIED if ok else EvidenceAxisState.UNDETERMINED,
        reason,
        ids,
    )


class BioInVitroObservationVerifier:
    profile = PROFILE

    def evaluate(self, closure: EvidenceClosure) -> QualificationDecision:
        claim = next(node for node in closure.nodes if node["node_type"] == "claim_revision")
        payload = claim["payload"]
        if payload.get("claim_type") != CLAIM_KIND:
            return QualificationDecision(QualificationVerdict.NOT_APPLICABLE, (), ("claim_kind_mismatch",))
        artifacts = {node["node_id"]: node for node in closure.nodes if node["node_type"] == "artifact"}
        witnesses = [
            node for node in artifacts.values()
            if (node["payload"].get("metadata") or {}).get("role") == "bio_in_vitro_witness"
        ]
        sources = [node for node in closure.nodes if node["node_type"] == "source"]
        witness = None
        source = witnesses[0] if len(witnesses) == 1 else None
        if source is not None and _intact(source):
            try:
                witness = validate_witness(json.loads(source["payload"]["content_text"]))
            except (InvalidEvidenceError, TypeError, ValueError):
                pass
        source_ok = witness is not None and len(sources) == 1 and any(
            node["payload"].get("source_key") == "measurements"
            and node["payload"].get("locator") == f"artifact:{source['node_id']}"
            and node["payload"].get("status") == "frozen"
            and node["content_hash"] == source["content_hash"]
            for node in sources
        )
        identity_ok = bool(
            source_ok
            and payload.get("semantic_hash") == closure.claim_semantic_hash
            and "statement_drift" not in closure.limitations
            and payload.get("statement") == statement(witness, source["content_hash"])
            and payload.get("scope") == CLAIM_SCOPE
            and payload.get("negative_boundaries") == NEGATIVE_BOUNDARIES
            and not payload.get("dependency_claim_ids")
        )
        expected = certificate(witness, closure.claim_revision_id, closure.claim_semantic_hash) if identity_ok else None
        certificate_ok = False
        attempt_id = None
        if expected is not None:
            for attempt in (node for node in closure.nodes if node["node_type"] == "verification_attempt"):
                value = attempt["payload"]
                lineage = value.get("verifier_lineage") or {}
                if (
                    value.get("invalidated_by")
                    or value.get("validation_modality") != ValidationModality.EXACT_REPLAY.value
                    or lineage.get("runtime_derived") is not True
                    or lineage.get("principal_id") != VERIFIER_PRINCIPAL
                    or lineage.get("model_route")
                    or value.get("input_digest") != canonical_hash({
                        "witness_sha256": source["content_hash"],
                        "claim_semantic_hash": closure.claim_semantic_hash,
                    })
                    or len(value.get("artifact_ids") or []) != 2
                    or source["node_id"] not in value["artifact_ids"]
                ):
                    continue
                result_id = next(item for item in value["artifact_ids"] if item != source["node_id"])
                result = artifacts.get(result_id)
                if (
                    result is None
                    or (result["payload"].get("metadata") or {}).get("role") != "bio_in_vitro_certificate"
                    or not _intact(result)
                    or value.get("output_digest") != result["content_hash"]
                    or value.get("outcome") != ("passed" if expected["replay"]["threshold_met"] else "failed")
                ):
                    continue
                try:
                    actual = json.loads(result["payload"]["content_text"])
                except (TypeError, ValueError):
                    continue
                if canonical_hash(actual) == canonical_hash(expected):
                    certificate_ok = True
                    attempt_id = attempt["node_id"]
                    break
        supported = certificate_ok and expected["replay"]["threshold_met"]
        criteria = (
            _criterion("statement_identity", identity_ok, "Exact dataset-only statement and semantic hash.", (closure.claim_revision_id,)),
            _criterion("source_integrity", source_ok, "Frozen submitted measurements match their source Artifact.", (source["node_id"],) if source else ()),
            _criterion("exact_replay", certificate_ok, "The server-derived certificate matches fresh exact arithmetic.", (attempt_id,) if attempt_id else ()),
            CriterionReceipt(
                "observed_threshold",
                EvidenceAxisState.SATISFIED if supported else EvidenceAxisState.FAILED if certificate_ok else EvidenceAxisState.UNDETERMINED,
                "The observed mean meets the frozen threshold." if supported else "The observed mean does not meet the frozen threshold." if certificate_ok else "No validated replay is available.",
                (attempt_id,) if attempt_id else (),
            ),
        )
        verdict = (
            QualificationVerdict.ADMITTED if supported else
            QualificationVerdict.BLOCKED if certificate_ok else QualificationVerdict.UNRESOLVED
        )
        blockers = (() if supported else ("observed_threshold_not_met",) if certificate_ok else ("insufficient_replay_evidence",))
        return QualificationDecision(
            verdict, criteria, blockers,
            {
                "statement_identity": EvidenceAxisState.SATISFIED if identity_ok else EvidenceAxisState.UNDETERMINED,
                "artifact_integrity": EvidenceAxisState.SATISFIED if source_ok else EvidenceAxisState.UNDETERMINED,
                "local_correctness": EvidenceAxisState.SATISFIED if certificate_ok else EvidenceAxisState.UNDETERMINED,
                "replayability": EvidenceAxisState.SATISFIED if certificate_ok else EvidenceAxisState.UNDETERMINED,
                "independent_validation": EvidenceAxisState.UNDETERMINED,
                "external_reality": EvidenceAxisState.UNDETERMINED,
            },
        )
