"""Candidate-only certificate for one signed Bio parent-lineage refutation.

This module recomputes the frozen MDE v0 diagnostic. It records a conditional
finite-model witness and one-step information ablations; it does not sign or
qualify a biological claim. Reduced signed records are supplied by the caller.
Their signatures authenticate bytes, not that an issuer historically performed
the counterfactual deletion or that the reported physical lineage is true.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path

from app.profiles.bio_observation_provenance import TrustedRecordKey

from . import bio
from .certificate_common import CERTIFICATE_VERSION, canonical_json_bytes, finalize_certificate

CERTIFICATE_KIND = "bio.parent-source-refutation.v0"
CERTIFICATE_GENERATOR_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_BASELINE_STATUS = "TARGET_FALSE_UNDER_ATTESTED_LINEAGE"
_UNRESOLVED = "UNRESOLVED"
_ABLATION_CONTEXTS = {"caller_supplied_counterfactual", "synthetic_test_resigning"}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _finite_refutation_model(report: dict, mapping: dict[str, str]) -> dict:
    """Construct one satisfying finite assignment, not a laboratory record."""
    sample_ids = sorted(set(report["covered_sample_ids"]) | set(report["missing_sample_ids"]))
    if len(sample_ids) < 2 or set(mapping) != set(report["covered_sample_ids"]):
        raise ValueError("Baseline does not define a consistent finite sample model")
    by_parent: dict[str, list[str]] = defaultdict(list)
    for sample_id, parent_id in mapping.items():
        by_parent[parent_id].append(sample_id)
    shared = sorted(
        (parent_id, sorted(ids)) for parent_id, ids in by_parent.items() if len(ids) >= 2
    )
    if not shared:
        raise ValueError("No signed common-parent counterexample exists")
    parent_id, pair = shared[0]
    modeled = dict(mapping)
    constructed: list[str] = []
    counter = 0
    for sample_id in sample_ids:
        if sample_id in modeled:
            continue
        # Fresh formal symbols make the finite constraint set nonempty. They
        # are never asserted to be measured or signed biological parent IDs.
        while True:
            formal_parent = f"<unobserved-parent-{counter}>"
            counter += 1
            if formal_parent not in modeled.values():
                break
        modeled[sample_id] = formal_parent
        constructed.append(sample_id)
    if set(modeled) != set(sample_ids) or len(set(modeled.values())) == len(modeled):
        raise ValueError("Finite witness does not refute the frozen target")
    return {
        "model_nonempty": True,
        "model_scope": "one finite completion under authenticated issuer assertions",
        "sample_parent_assignment": [
            {
                "sample_id": sample_id,
                "canonical_parent_id": modeled[sample_id],
                "basis": "signed_lineage_assertion" if sample_id in mapping else "formal_placeholder",
            }
            for sample_id in sample_ids
        ],
        "formal_placeholder_sample_ids": constructed,
        "common_parent_counterexample": {
            "sample_ids": pair[:2],
            "canonical_parent_id": parent_id,
            "basis": "authenticated_lineage_assertion_not_physical_authentication",
        },
        "decision_rule": (
            "Two distinct recorded samples sharing one asserted canonical parent "
            "refute parent-source distinctness in every compatible finite completion."
        ),
        "target_value_in_this_model": False,
        "target_value_for_all_compatible_finite_completions": False,
        "world_enumeration_claimed": False,
    }


def _single_mapping_removed(original: dict, reduced: dict, sample_id: str) -> tuple[bool, bool]:
    """Allow only deletion of this entry and required loss of completeness."""
    try:
        if set(original) != set(reduced) or any(
            original[key] != reduced[key]
            for key in original
            if key not in {"payload", "signature"}
        ):
            return False, False
        original_payload = original["payload"]
        reduced_payload = reduced["payload"]
        entries = original_payload["sample_parents"]
        matching_indices = [
            index for index, entry in enumerate(entries) if entry["sample_id"] == sample_id
        ]
        if len(matching_indices) != 1:
            return False, False
        expected = deepcopy(original_payload)
        expected["sample_parents"].pop(matching_indices[0])
        changed_completeness = expected["complete_for_recorded_samples"] is True
        if changed_completeness:
            expected["complete_for_recorded_samples"] = False
        return expected == reduced_payload, changed_completeness
    except (KeyError, IndexError, TypeError, ValueError, RecursionError):
        return False, False


def certify_bio_refutation(
    instrument_bytes: bytes,
    custodian_bytes: bytes,
    lineage_record: bytes,
    trusted_keys: Sequence[bio.TrustedLineageKey],
    trusted_record_keys: Sequence[TrustedRecordKey],
    *,
    reduced_lineage_by_sample: Mapping[str, bytes] | None = None,
    ablation_context: str = "caller_supplied_counterfactual",
) -> dict:
    """Recompute and certify a conditional Bio refutation, without authority.

    ``ablation_context`` is explicitly unauthenticated descriptive metadata.
    Synthetic tests set it to ``synthetic_test_resigning``. No signing key is
    accepted or used here; a reduced record must arrive already signed.
    """
    if not all(
        isinstance(value, bytes)
        for value in (instrument_bytes, custodian_bytes, lineage_record)
    ):
        raise ValueError("Bio certificate requires exact raw source bytes")
    if not isinstance(trusted_keys, (list, tuple)) or not isinstance(
        trusted_record_keys, (list, tuple)
    ):
        raise ValueError("Trust roots must be bounded host-supplied sequences")
    if reduced_lineage_by_sample is not None and not isinstance(reduced_lineage_by_sample, Mapping):
        raise ValueError("Ablations must be keyed by baseline sample ID")
    if not isinstance(ablation_context, str) or ablation_context not in _ABLATION_CONTEXTS:
        raise ValueError("Ablation context must be a fixed non-authoritative label")
    baseline = bio.diagnose_bio_mde(
        instrument_bytes,
        custodian_bytes,
        [lineage_record],
        trusted_keys,
        trusted_record_keys,
    )
    if (
        baseline["status"] != _BASELINE_STATUS
        or baseline["reason"] != "signed_assertion_common_canonical_parent"
        or baseline["lineage_assertion_authenticated"] is not True
        or baseline["l1_record_signatures_verified"] is not True
        or baseline["qualification_granted"] is not False
        or not baseline["trust_configuration_sha256"]
        or baseline["lineage_record_sha256"] != [_sha256(lineage_record)]
    ):
        raise ValueError(f"Bio refutation baseline is not authenticated: {baseline['reason']}")
    envelope = bio._read_json_object(lineage_record, max_bytes=bio.MAX_LINEAGE_RECORD_BYTES)
    entries = envelope["payload"]["sample_parents"]
    mapping = {entry["sample_id"]: entry["canonical_parent_id"] for entry in entries}
    finite_model = _finite_refutation_model(baseline, mapping)
    proposed = reduced_lineage_by_sample or {}
    if len(proposed) > len(mapping) or set(proposed) - set(mapping):
        raise ValueError("Ablation keys must identify baseline signed mappings")
    ablations: list[dict] = []
    for sample_id in sorted(mapping):
        provided = sample_id in proposed
        raw = proposed[sample_id] if provided else None
        reduced_sha256 = (
            _sha256(raw)
            if isinstance(raw, bytes) and len(raw) <= bio.MAX_LINEAGE_RECORD_BYTES
            else None
        )
        item = {
            "evidence_unit": {"kind": "signed_sample_parent_mapping", "sample_id": sample_id},
            "baseline_lineage_sha256": _sha256(lineage_record),
            "reduced_lineage_sha256": reduced_sha256,
            "ablation_input_sha256": (
                _sha256(canonical_json_bytes({
                    "sample_id": sample_id,
                    "instrument_sha256": baseline["instrument_sha256"],
                    "custodian_sha256": baseline["custodian_sha256"],
                    "reduced_lineage_sha256": reduced_sha256,
                    "trust_configuration_sha256": baseline["trust_configuration_sha256"],
                }))
                if reduced_sha256 is not None
                else None
            ),
            "ablation_context": ablation_context if provided else None,
            "ablation_context_authenticated": False,
            "completeness_claim_adjusted_to_false": False,
            "verification": None,
            "diagnostic": None,
            "classification": "UNTESTED",
        }
        if not provided:
            ablations.append(item)
            continue
        if not isinstance(raw, bytes):
            item["verification"] = "INVALID_INPUT_TYPE"
            item["classification"] = "INVALID_ABLATION"
            ablations.append(item)
            continue
        if len(raw) > bio.MAX_LINEAGE_RECORD_BYTES:
            item["verification"] = "RESOURCE_LIMIT_EXCEEDED_BEFORE_HASHING"
            item["classification"] = "INVALID_ABLATION"
            ablations.append(item)
            continue
        reduced_report = bio.diagnose_bio_mde(
            instrument_bytes, custodian_bytes, [raw], trusted_keys, trusted_record_keys
        )
        item["diagnostic"] = reduced_report
        try:
            reduced = bio._read_json_object(raw, max_bytes=bio.MAX_LINEAGE_RECORD_BYTES)
            exact_delta, completeness_changed = _single_mapping_removed(envelope, reduced, sample_id)
        except (ValueError, UnicodeError, RecursionError, MemoryError):
            exact_delta, completeness_changed = False, False
        item["completeness_claim_adjusted_to_false"] = completeness_changed
        authenticated = (
            reduced_report["lineage_assertion_authenticated"] is True
            and reduced_report["l1_record_signatures_verified"] is True
            and reduced_report["trust_configuration_sha256"]
            == baseline["trust_configuration_sha256"]
            and reduced_report["instrument_sha256"] == baseline["instrument_sha256"]
            and reduced_report["custodian_sha256"] == baseline["custodian_sha256"]
            and reduced_report["target"] == baseline["target"]
            and reduced_report["case_id"] == baseline["case_id"]
        )
        if not exact_delta or not authenticated:
            item["verification"] = "INVALID_OR_UNAUTHENTICATED_REDUCTION"
            item["classification"] = "INVALID_ABLATION"
        else:
            item["verification"] = "SIGNED_EXACT_SINGLE_MAPPING_REDUCTION"
            if (
                reduced_report["status"] == _UNRESOLVED
                and reduced_report["reason"] == "lineage_coverage_incomplete"
            ):
                item["classification"] = "NECESSARY_IN_THIS_BUNDLE"
            elif (
                reduced_report["status"] == _BASELINE_STATUS
                and reduced_report["reason"] == "signed_assertion_common_canonical_parent"
            ):
                item["classification"] = "REDUNDANT_IN_THIS_BUNDLE"
            else:
                item["verification"] = "REDUCTION_CHANGED_TARGET_DECISION"
                item["classification"] = "INVALID_ABLATION"
        ablations.append(item)

    return finalize_certificate(
        {
            "certificate_version": CERTIFICATE_VERSION,
            "certificate_kind": CERTIFICATE_KIND,
            "generator_source_sha256": CERTIFICATE_GENERATOR_SHA256,
            "case_id": baseline["case_id"],
            "target": baseline["target"],
            "contract_version": baseline["contract_version"],
            "checker_source_sha256": baseline["checker_source_sha256"],
            "checker_dependency_sha256": baseline["checker_dependency_sha256"],
            "inputs": {
                "instrument_sha256": baseline["instrument_sha256"],
                "custodian_sha256": baseline["custodian_sha256"],
                "lineage_record_sha256": _sha256(lineage_record),
                "trust_configuration_sha256": baseline["trust_configuration_sha256"],
                "trusted_public_keys": baseline["trusted_public_keys"],
            },
            "decision": {
                "status": baseline["status"],
                "reason": baseline["reason"],
                "conditional_on": "authenticated_issuer_lineage_assertion_and_frozen_L1_observation",
                "finite_model": finite_model,
                "baseline_diagnostic": baseline,
            },
            "ablations": ablations,
            "unverified_assumptions": [
                "The physical samples actually have the canonical parents asserted by the issuer.",
                "The host enrollment of instrument, custodian, and lineage public keys reflects rightful authorities.",
                "The lineage registry's canonicalization and parent identities are physically accurate.",
                "No independent inspection of sample collection, custody, key revocation, or experiment QC was performed.",
                "Caller-provided reduced records may be synthetic; their signatures do not authenticate a historical ablation event.",
            ],
            "limitations": [
                "Only the frozen finite parent-distinctness target is decided under these assertions.",
                "One-step deletion results are relative to these mapped evidence units, not a global minimal evidence proof.",
                "Unobserved-parent symbols establish logical consistency only, not real-world sample identity.",
            ],
            "candidate_only": True,
            "qualification_granted": False,
            "current_use_bound": False,
            "authorization_granted": False,
        }
    )
