"""Finite counterworld diagnostic for biological-source information sufficiency.

This is not a QualificationProfile or an authenticity verifier. It shows that
the L1 record *fields* admit two latent parent-source assignments with opposite
answers to one frozen parent-source-distinctness question. Those assignments
are synthetic possibilities, not claims about the actual laboratory.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

from ..core.errors import InvalidEvidenceError
from ..core.qualification import canonical_hash

CASE_ID = "bio.biological-source-sufficiency.v1"
TARGET = "Each recorded measurement pair has a distinct biological parent source."
SAMPLE_FIELDS = {"pair_id", "sample_id", "collected_at", "assayed_at"}
ENVELOPE_FIELDS = {"contract_version", "issuer_id", "key_id", "payload", "signature"}
STUDY_FIELDS = {
    "study_id",
    "completed_at",
    "biological_model",
    "assay",
    "endpoint",
    "unit",
    "timepoint",
}
PAIR_FIELDS = {"pair_id", "batch_id", "control", "treated"}
CHECKER_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _visible_records(instrument: dict, custodian: dict) -> tuple[dict, list[dict]]:
    """Accept only the existing L1 observable shape; never infer latent lineage."""
    if not isinstance(instrument, dict) or not isinstance(custodian, dict):
        raise InvalidEvidenceError("records", "Expected the two L1 record envelopes")
    if set(instrument) != ENVELOPE_FIELDS or set(custodian) != ENVELOPE_FIELDS:
        raise InvalidEvidenceError("records", "Outside the frozen L1 envelope shape")
    if (
        instrument["contract_version"] != "bio.instrument-record.v0"
        or custodian["contract_version"] != "bio.custodian-record.v0"
    ):
        raise InvalidEvidenceError("records", "Outside the frozen L1 contract versions")
    if any(
        not isinstance(envelope[field], str)
        for envelope in (instrument, custodian)
        for field in ENVELOPE_FIELDS - {"payload"}
    ):
        raise InvalidEvidenceError("records", "Outside the frozen L1 envelope shape")
    instrument_payload = instrument.get("payload")
    custodian_payload = custodian.get("payload")
    if not isinstance(instrument_payload, dict) or not isinstance(custodian_payload, dict):
        raise InvalidEvidenceError("records", "Record payloads are required")
    if set(instrument_payload) != {"study", "pairs"}:
        raise InvalidEvidenceError("instrument", "Outside the frozen L1 payload shape")
    if set(custodian_payload) != {"study_id", "instrument_sha256", "samples"}:
        raise InvalidEvidenceError("custodian", "Outside the frozen L1 payload shape")
    if not all(
        isinstance(custodian_payload[field], str) for field in ("study_id", "instrument_sha256")
    ):
        raise InvalidEvidenceError("custodian", "Outside the frozen L1 payload shape")
    pairs = instrument_payload.get("pairs")
    samples = custodian_payload.get("samples")
    study = instrument_payload.get("study")
    if (
        not isinstance(study, dict)
        or set(study) != STUDY_FIELDS
        or not all(isinstance(value, str) for value in study.values())
    ):
        raise InvalidEvidenceError("study", "Outside the frozen L1 study shape")
    if (
        not isinstance(pairs, list)
        or not isinstance(samples, list)
        or not 3 <= len(pairs) == len(samples) <= 64
    ):
        raise InvalidEvidenceError("samples", "Expected 3 to 64 paired L1 sample records")
    if any(
        not isinstance(item, dict)
        or set(item) != PAIR_FIELDS
        or not all(isinstance(value, str) for value in item.values())
        for item in pairs
    ):
        raise InvalidEvidenceError("pairs", "Outside the frozen L1 pair shape")
    if any(
        not isinstance(item, dict)
        or set(item) != SAMPLE_FIELDS
        or not all(isinstance(item[key], str) and item[key] for key in SAMPLE_FIELDS)
        for item in samples
    ):
        raise InvalidEvidenceError("samples", "Sample records must use the exact L1 fields")
    pair_ids = [item.get("pair_id") for item in pairs if isinstance(item, dict)]
    sample_pair_ids = [item["pair_id"] for item in samples]
    sample_ids = [item["sample_id"] for item in samples]
    if (
        len(pair_ids) != len(pairs)
        or any(not isinstance(pair_id, str) for pair_id in pair_ids)
        or len(set(sample_pair_ids)) != len(samples)
        or sorted(pair_ids) != sorted(sample_pair_ids)
        or len(set(sample_ids)) != len(samples)
    ):
        raise InvalidEvidenceError(
            "samples", "This counterworld Case requires matching pairs and distinct sample IDs"
        )
    return {"instrument": instrument, "custodian": custodian}, samples


def _world(visible: dict, *, shared_parent: bool) -> dict:
    samples = visible["custodian"]["payload"]["samples"]
    return {
        "observation": deepcopy(visible),
        "latent_parent_sources": {
            item["sample_id"]: "culture-1" if shared_parent else f"culture-{index + 1}"
            for index, item in enumerate(samples)
        },
    }


def _projection(world: dict) -> dict:
    return world["observation"]


def _target(world: dict) -> bool:
    samples = world["observation"]["custodian"]["payload"]["samples"]
    sources = [world["latent_parent_sources"][item["sample_id"]] for item in samples]
    return len(set(sources)) == len(sources)


def observation_equivalence_invariant(visible: dict, first: dict, second: dict) -> bool:
    """Check this Case's full L1 projection and both possible-world constraints."""
    if not isinstance(visible, dict) or set(visible) != {"instrument", "custodian"}:
        return False
    try:
        baseline, _ = _visible_records(visible["instrument"], visible["custodian"])
    except InvalidEvidenceError:
        return False

    def valid_world(world: dict) -> bool:
        if (
            not isinstance(world, dict)
            or set(world) != {"observation", "latent_parent_sources"}
            or not isinstance(world["observation"], dict)
            or set(world["observation"]) != {"instrument", "custodian"}
        ):
            return False
        try:
            observation, samples = _visible_records(
                world["observation"]["instrument"], world["observation"]["custodian"]
            )
        except InvalidEvidenceError:
            return False
        if observation != baseline:
            return False
        # L1's existing study, pair, and sample mapping must hold in each world.
        if (
            observation["custodian"]["payload"]["study_id"]
            != observation["instrument"]["payload"]["study"]["study_id"]
        ):
            return False
        roots = world["latent_parent_sources"]
        return bool(
            isinstance(roots, dict)
            and set(roots) == {item["sample_id"] for item in samples}
            and all(isinstance(root, str) and root for root in roots.values())
        )

    return valid_world(first) and valid_world(second) and _target(first) != _target(second)


def _world_summary(kind: str, world: dict) -> dict:
    observation = _projection(world)
    samples = observation["custodian"]["payload"]["samples"]
    return {
        "kind": kind,
        "latent_parent_sources": [
            world["latent_parent_sources"][item["sample_id"]] for item in samples
        ],
        "projected_samples_sha256": canonical_hash(samples),
        "projected_observation_sha256": canonical_hash(observation),
        "target_holds": _target(world),
    }


def analyze_sufficiency(instrument: dict, custodian: dict) -> dict:
    """Return a constructive non-identifiability witness, never a verdict grant."""
    visible, samples = _visible_records(instrument, custodian)
    distinct_source_world = _world(visible, shared_parent=False)
    shared_source_world = _world(visible, shared_parent=True)
    if not observation_equivalence_invariant(
        visible, distinct_source_world, shared_source_world
    ):
        raise AssertionError("Counterworlds violate observation equivalence or target separation")
    projection_hash = canonical_hash(samples)
    return {
        "case_id": CASE_ID,
        "checker_source_sha256": CHECKER_SOURCE_SHA256,
        "target": TARGET,
        "observation_sha256": canonical_hash(visible),
        "observation_equivalence_invariant": True,
        "observed_fields": sorted(SAMPLE_FIELDS),
        "record_binding": "CHECK_SEPARATELY_WITH_L1_GATE",
        "parent_source_distinctness": "UNRESOLVED",
        "reason": "parent_source_lineage_not_observed",
        "counterworlds": [
            _world_summary("distinct_cultures", distinct_source_world),
            _world_summary("one_culture_multiple_aliquots", shared_source_world),
        ],
        "projected_samples_sha256": projection_hash,
        "limitations": [
            "The worlds are constructed possibilities, not authenticated laboratory histories.",
            "L1 signatures and arithmetic do not identify biological parent sources.",
            "Distinct parent labels alone would not establish statistical independence or QC.",
            "No qualification, current-use binding, or execution authority is issued.",
        ],
    }


def disclosed_lineage_control(instrument: dict, custodian: dict, sources: dict[str, str]) -> dict:
    """Inspect an explicit *synthetic* lineage control, without authenticating it."""
    _, samples = _visible_records(instrument, custodian)
    sample_ids = {item["sample_id"] for item in samples}
    if (
        not isinstance(sources, dict)
        or set(sources) != sample_ids
        or any(not isinstance(value, str) or not value for value in sources.values())
    ):
        raise InvalidEvidenceError("sources", "Expected one declared parent per sample ID")
    world = {
        "observation": {"instrument": instrument, "custodian": custodian},
        "latent_parent_sources": sources,
    }
    return {
        "case_id": CASE_ID,
        "status": "REFUTED_IN_DECLARED_CONTROL" if not _target(world) else "NOT_REFUTED_BY_CONTROL",
        "lineage_authenticated": False,
        "qualification_granted": False,
    }
