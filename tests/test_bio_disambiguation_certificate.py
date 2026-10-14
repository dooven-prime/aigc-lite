"""Bio certificate records information dependence, never source truth or authority."""

from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest
from aigc_lite_casepacks.mde_v0.bio import (
    MAX_LINEAGE_RECORD_BYTES,
    TrustedLineageKey,
    signed_bytes,
)
from aigc_lite_casepacks.mde_v0.bio_certificate import certify_bio_refutation
from aigc_lite_casepacks.mde_v0.certificate_common import verify_certificate_hash
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_bio_minimal_disambiguating_evidence import (
    _fixture,
    _lineage,
    _raw,
    _signature,
)


def _resign_without(record: dict, sample_id: str, signer: Ed25519PrivateKey) -> bytes:
    reduced = deepcopy(record)
    reduced["payload"]["sample_parents"] = [
        entry
        for entry in reduced["payload"]["sample_parents"]
        if entry["sample_id"] != sample_id
    ]
    if reduced["payload"]["complete_for_recorded_samples"]:
        reduced["payload"]["complete_for_recorded_samples"] = False
    reduced["signature"] = _signature(signer, signed_bytes(reduced))
    return _raw(reduced)


def _inputs(*, shared_third: bool = False) -> tuple:
    instrument, custodian, samples, signer, l1_keys, lineage_key = _fixture()
    parents = {samples[0]: "parent-X", samples[1]: "parent-X"}
    if shared_third:
        parents[samples[2]] = "parent-X"
    else:
        parents[samples[2]] = "parent-Y"
    record = _lineage(instrument, custodian, signer, parents, complete=True)
    return instrument, custodian, samples, signer, l1_keys, lineage_key, record


def _cert(inputs: tuple, *, reduced: dict[str, bytes] | None = None) -> dict:
    instrument, custodian, _, _, l1_keys, lineage_key, record = inputs
    return certify_bio_refutation(
        instrument,
        custodian,
        _raw(record),
        [lineage_key],
        l1_keys,
        reduced_lineage_by_sample=reduced,
        ablation_context="synthetic_test_resigning",
    )


def test_two_common_parent_mappings_are_necessary_and_third_is_redundant() -> None:
    inputs = _inputs()
    _, _, samples, signer, _, _, record = inputs
    reduced = {sample: _resign_without(record, sample, signer) for sample in samples}
    cert = _cert(inputs, reduced=reduced)
    assert cert == _cert(inputs, reduced=reduced)
    assert verify_certificate_hash(cert)
    assert cert["candidate_only"] is True
    assert cert["qualification_granted"] is False
    assert cert["current_use_bound"] is False
    assert cert["authorization_granted"] is False
    assert cert["decision"]["status"] == "TARGET_FALSE_UNDER_ATTESTED_LINEAGE"
    model = cert["decision"]["finite_model"]
    assert model["model_nonempty"] is True
    assert model["world_enumeration_claimed"] is False
    assert model["target_value_in_this_model"] is False
    assert model["target_value_for_all_compatible_finite_completions"] is False
    assert model["common_parent_counterexample"]["sample_ids"] == samples[:2]
    assert model["formal_placeholder_sample_ids"] == []
    by_sample = {row["evidence_unit"]["sample_id"]: row for row in cert["ablations"]}
    assert [by_sample[sample]["classification"] for sample in samples] == [
        "NECESSARY_IN_THIS_BUNDLE",
        "NECESSARY_IN_THIS_BUNDLE",
        "REDUNDANT_IN_THIS_BUNDLE",
    ]
    for sample in samples:
        row = by_sample[sample]
        assert row["verification"] == "SIGNED_EXACT_SINGLE_MAPPING_REDUCTION"
        assert row["ablation_context"] == "synthetic_test_resigning"
        assert row["ablation_context_authenticated"] is False
        assert row["reduced_lineage_sha256"] == hashlib.sha256(reduced[sample]).hexdigest()
        assert len(row["ablation_input_sha256"]) == 64
        assert row["diagnostic"]["lineage_assertion_authenticated"] is True
        assert row["completeness_claim_adjusted_to_false"] is True
    assert "physical" in " ".join(cert["unverified_assumptions"])


def test_uncovered_samples_are_formal_placeholders_not_new_issuer_assertions() -> None:
    instrument, custodian, samples, signer, l1_keys, lineage_key = _fixture()
    record = _lineage(
        instrument,
        custodian,
        signer,
        {samples[0]: "shared", samples[1]: "shared"},
        complete=False,
    )
    cert = certify_bio_refutation(
        instrument, custodian, _raw(record), [lineage_key], l1_keys
    )
    assert cert["decision"]["finite_model"]["formal_placeholder_sample_ids"] == [samples[2]]
    assert cert["decision"]["baseline_diagnostic"]["missing_sample_ids"] == [samples[2]]
    assert len(cert["ablations"]) == 2
    assert {item["classification"] for item in cert["ablations"]} == {"UNTESTED"}


def test_redundancy_is_relative_to_this_bundle_not_global_minimality() -> None:
    inputs = _inputs(shared_third=True)
    _, _, samples, signer, _, _, record = inputs
    cert = _cert(inputs, reduced={
        sample: _resign_without(record, sample, signer) for sample in samples
    })
    assert {item["classification"] for item in cert["ablations"]} == {
        "REDUNDANT_IN_THIS_BUNDLE"
    }
    assert "not a global minimal evidence proof" in " ".join(cert["limitations"])


@pytest.mark.parametrize("mutation", ["signature", "other_mapping", "wrong_target", "wrong_observation"])
def test_invalid_ablations_never_count_as_necessary_information(mutation: str) -> None:
    inputs = _inputs()
    instrument, custodian, samples, signer, _, _, record = inputs
    reduced = deepcopy(record)
    reduced["payload"]["sample_parents"] = [
        entry for entry in reduced["payload"]["sample_parents"]
        if entry["sample_id"] != samples[0]
    ]
    reduced["payload"]["complete_for_recorded_samples"] = False
    reduced["signature"] = _signature(signer, signed_bytes(reduced))
    if mutation == "signature":
        reduced["signature"] = "A" * 86
    elif mutation == "other_mapping":
        reduced["payload"]["sample_parents"][0]["canonical_parent_id"] = "changed"
        reduced["signature"] = _signature(signer, signed_bytes(reduced))
    elif mutation == "wrong_target":
        reduced["payload"]["target"] = "Another claim"
        reduced["signature"] = _signature(signer, signed_bytes(reduced))
    else:
        reduced["payload"]["instrument_sha256"] = hashlib.sha256(custodian).hexdigest()
        reduced["signature"] = _signature(signer, signed_bytes(reduced))
    cert = _cert(inputs, reduced={samples[0]: _raw(reduced)})
    item = cert["ablations"][0]
    assert item["classification"] == "INVALID_ABLATION"
    assert item["verification"] == "INVALID_OR_UNAUTHENTICATED_REDUCTION"
    assert item["diagnostic"]["status"] == "UNRESOLVED"
    assert item["diagnostic"]["lineage_assertion_authenticated"] is (mutation == "other_mapping")
    assert verify_certificate_hash(cert)


def test_baseline_tamper_wrong_binding_or_unenrolled_key_fail_closed() -> None:
    inputs = _inputs()
    instrument, custodian, _, _, l1_keys, lineage_key, record = inputs
    tampered = deepcopy(record)
    tampered["signature"] = "A" * 86
    with pytest.raises(ValueError, match="not authenticated"):
        certify_bio_refutation(instrument, custodian, _raw(tampered), [lineage_key], l1_keys)
    with pytest.raises(ValueError, match="not authenticated"):
        certify_bio_refutation(instrument + b" ", custodian, _raw(record), [lineage_key], l1_keys)
    other_key = TrustedLineageKey(
        lineage_key.issuer_id,
        lineage_key.key_id,
        lineage_key.parent_registry,
        Ed25519PrivateKey.generate().public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ),
    )
    with pytest.raises(ValueError, match="not authenticated"):
        certify_bio_refutation(instrument, custodian, _raw(record), [other_key], l1_keys)


def test_conflicting_or_empty_baselines_cannot_create_vacuous_certificate() -> None:
    inputs = _inputs()
    instrument, custodian, samples, signer, l1_keys, lineage_key, _ = inputs
    contradictory = _lineage(
        instrument, custodian, signer,
        {samples[0]: "same", samples[1]: "different"}, complete=False,
    )
    with pytest.raises(ValueError, match="not authenticated"):
        certify_bio_refutation(
            instrument, custodian, _raw(contradictory), [lineage_key], l1_keys
        )
    conflicting_mapping = deepcopy(contradictory)
    conflicting_mapping["payload"]["sample_parents"].append(
        {"sample_id": samples[0], "canonical_parent_id": "other-parent"}
    )
    conflicting_mapping["signature"] = _signature(signer, signed_bytes(conflicting_mapping))
    with pytest.raises(ValueError, match="not authenticated"):
        certify_bio_refutation(
            instrument, custodian, _raw(conflicting_mapping), [lineage_key], l1_keys
        )
    with pytest.raises(ValueError, match="not authenticated"):
        certify_bio_refutation(instrument, custodian, b"{}", [lineage_key], l1_keys)


def test_unknown_ablation_key_does_not_silently_change_world_scope() -> None:
    inputs = _inputs()
    with pytest.raises(ValueError, match="baseline signed mappings"):
        _cert(inputs, reduced={"not-observed": b"{}"})


def test_present_but_nonbyte_reduction_is_invalid_not_untested() -> None:
    inputs = _inputs()
    sample_id = inputs[2][0]
    cert = _cert(inputs, reduced={sample_id: None})  # type: ignore[dict-item]
    item = next(row for row in cert["ablations"] if row["evidence_unit"]["sample_id"] == sample_id)
    assert item["classification"] == "INVALID_ABLATION"
    assert item["verification"] == "INVALID_INPUT_TYPE"


def test_oversized_reduction_is_rejected_before_hashing() -> None:
    inputs = _inputs()
    sample_id = inputs[2][0]
    cert = _cert(inputs, reduced={sample_id: b"x" * (MAX_LINEAGE_RECORD_BYTES + 1)})
    item = next(
        row for row in cert["ablations"] if row["evidence_unit"]["sample_id"] == sample_id
    )
    assert item["classification"] == "INVALID_ABLATION"
    assert item["verification"] == "RESOURCE_LIMIT_EXCEEDED_BEFORE_HASHING"
    assert item["reduced_lineage_sha256"] is None
    assert item["ablation_input_sha256"] is None
    assert item["diagnostic"] is None


def test_caller_cannot_inject_authoritative_ablation_label_or_nonbyte_baseline() -> None:
    inputs = _inputs()
    instrument, custodian, _, _, l1_keys, lineage_key, record = inputs
    with pytest.raises(ValueError, match="fixed non-authoritative label"):
        certify_bio_refutation(
            instrument,
            custodian,
            _raw(record),
            [lineage_key],
            l1_keys,
            ablation_context="verified_independent_issuer_action",
        )
    with pytest.raises(ValueError, match="exact raw source bytes"):
        certify_bio_refutation(
            instrument,
            custodian,
            record,  # type: ignore[arg-type]
            [lineage_key],
            l1_keys,
        )
