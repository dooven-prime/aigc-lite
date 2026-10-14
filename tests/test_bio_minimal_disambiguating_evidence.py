"""Signed lineage can decide only the frozen biological-parent question."""

from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from aigc_lite_casepacks.mde_v0 import bio as bio_mde
from aigc_lite_casepacks.mde_v0.bio import (
    CHECKER_DEPENDENCY_SHA256,
    CHECKER_SOURCE_SHA256,
    CONTRACT_VERSION,
    LINEAGE_SEMANTICS,
    MAX_JSON_DEPTH,
    MAX_L1_RECORD_BYTES,
    MAX_LINEAGE_RECORD_BYTES,
    MAX_LINEAGE_RECORDS,
    MAX_TOTAL_INPUT_BYTES,
    MAX_TRUST_LABEL_CHARS,
    MAX_TRUSTED_KEYS_PER_ROLE,
    TrustedLineageKey,
    diagnose_bio_mde,
    signed_bytes,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_bio_in_vitro_case import witness
from test_bio_observation_provenance import _key, _signed_records

from app.profiles.bio_information_sufficiency import CASE_ID, TARGET, analyze_sufficiency
from app.profiles.bio_observation_provenance import signed_bytes as l1_signed_bytes


def _raw(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _signature(key: Ed25519PrivateKey, message: bytes) -> str:
    return base64.urlsafe_b64encode(key.sign(message)).decode("ascii").rstrip("=")


def _fixture() -> tuple[bytes, bytes, list[str], Ed25519PrivateKey, tuple, TrustedLineageKey]:
    submitted = witness()
    instrument_key = Ed25519PrivateKey.generate()
    custodian_key = Ed25519PrivateKey.generate()
    instrument, custodian, _ = _signed_records(
        submitted,
        instrument_key,
        custodian_key,
        [f"Sample-{index + 1}" for index in range(len(submitted["pairs"]))],
    )
    instrument_bytes = _raw(instrument)
    custodian["payload"]["instrument_sha256"] = hashlib.sha256(instrument_bytes).hexdigest()
    custodian["signature"] = _signature(custodian_key, l1_signed_bytes(custodian))
    sample_ids = [item["sample_id"] for item in custodian["payload"]["samples"]]
    lineage_key = Ed25519PrivateKey.generate()
    enrolled_lineage = TrustedLineageKey(
        "lineage-office-1",
        "lineage-key-1",
        "lab-parent-register-v1",
        lineage_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ),
    )
    l1_keys = (
        _key("instrument", "device-registry-1", instrument_key),
        _key("custodian", "sample-office-1", custodian_key),
    )
    return instrument_bytes, _raw(custodian), sample_ids, lineage_key, l1_keys, enrolled_lineage


def _lineage(
    instrument_bytes: bytes,
    custodian_bytes: bytes,
    key: Ed25519PrivateKey,
    sample_parents: dict[str, str],
    *,
    complete: bool,
) -> dict:
    envelope = {
        "contract_version": CONTRACT_VERSION,
        "issuer_id": "lineage-office-1",
        "key_id": "lineage-key-1",
        "payload": {
            "case_id": CASE_ID,
            "target": TARGET,
            "instrument_sha256": hashlib.sha256(instrument_bytes).hexdigest(),
            "custodian_sha256": hashlib.sha256(custodian_bytes).hexdigest(),
            "parent_registry": "lab-parent-register-v1",
            "lineage_semantics": LINEAGE_SEMANTICS,
            "complete_for_recorded_samples": complete,
            "sample_parents": [
                {"sample_id": sample_id, "canonical_parent_id": parent_id}
                for sample_id, parent_id in sample_parents.items()
            ],
        },
    }
    envelope["signature"] = _signature(key, signed_bytes(envelope))
    return envelope


def _diagnose(fixture: tuple, records: list[dict]) -> dict:
    instrument_bytes, custodian_bytes, _, _, l1_keys, lineage_key = fixture
    return diagnose_bio_mde(
        instrument_bytes,
        custodian_bytes,
        [_raw(record) for record in records],
        [lineage_key],
        l1_keys,
    )


def test_complete_attested_canonical_lineage_disambiguates_only_narrow_target() -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, _, _ = fixture
    assert analyze_sufficiency(json.loads(instrument), json.loads(custodian))[
        "parent_source_distinctness"
    ] == "UNRESOLVED"
    record = _lineage(
        instrument,
        custodian,
        signer,
        {sample: f"parent-{index}" for index, sample in enumerate(samples)},
        complete=True,
    )
    report = _diagnose(fixture, [record])
    assert report["status"] == "TARGET_TRUE_UNDER_ATTESTED_LINEAGE"
    assert report["l1_record_signatures_verified"] is True
    assert report["lineage_assertion_authenticated"] is True
    assert report["complete_coverage_attested"] is True
    assert report["missing_sample_ids"] == []
    assert report["qualification_granted"] is False
    assert "statistical independence" in " ".join(report["limitations"])


def test_report_identifies_checker_and_direct_frozen_dependencies() -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, _, _ = fixture
    record = _lineage(
        instrument,
        custodian,
        signer,
        {sample: f"parent-{index}" for index, sample in enumerate(samples)},
        complete=True,
    )
    report = _diagnose(fixture, [record])
    root = Path(__file__).resolve().parents[1]
    expected = {
        "bio_mde_contract": root / "casepacks/mde-v0/src/aigc_lite_casepacks/mde_v0/bio_lineage_contract_v0.json",
        "bio_v1_observation": root / "app/profiles/bio_information_sufficiency.py",
        "l1_record_signature_verifier": root / "app/profiles/bio_observation_provenance.py",
        "bio_v1_case_lock": root / "contracts/information-sufficiency/bio/case-lock.json",
    }
    assert report["checker_source_sha256"] == CHECKER_SOURCE_SHA256
    assert CHECKER_SOURCE_SHA256 == hashlib.sha256(
        (root / "casepacks/mde-v0/src/aigc_lite_casepacks/mde_v0/bio.py").read_bytes()
    ).hexdigest()
    assert report["checker_dependency_sha256"] == CHECKER_DEPENDENCY_SHA256
    assert report["checker_dependency_sha256"] == {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in expected.items()
    }
    assert report["lineage_record_sha256"] == [hashlib.sha256(_raw(record)).hexdigest()]
    contract = json.loads(expected["bio_mde_contract"].read_text(encoding="utf-8"))
    assert contract["contract_version"] == CONTRACT_VERSION
    assert contract["case_id"] == CASE_ID
    assert contract["target"] == TARGET
    assert contract["lineage_semantics"] == LINEAGE_SEMANTICS
    assert contract["resource_limits"] == {
        "l1_record_bytes_each": MAX_L1_RECORD_BYTES,
        "lineage_record_bytes_each": MAX_LINEAGE_RECORD_BYTES,
        "lineage_records": MAX_LINEAGE_RECORDS,
        "all_input_bytes": MAX_TOTAL_INPUT_BYTES,
        "json_nesting_levels": MAX_JSON_DEPTH,
        "trusted_keys_per_role_set": MAX_TRUSTED_KEYS_PER_ROLE,
        "host_key_metadata_chars_each": MAX_TRUST_LABEL_CHARS,
    }


def test_one_signed_common_parent_refutes_even_with_missing_samples() -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, _, _ = fixture
    record = _lineage(
        instrument,
        custodian,
        signer,
        {samples[0]: "parent-1", samples[1]: "parent-1"},
        complete=False,
    )
    report = _diagnose(fixture, [record])
    assert report["status"] == "TARGET_FALSE_UNDER_ATTESTED_LINEAGE"
    assert report["reason"] == "signed_assertion_common_canonical_parent"
    assert report["missing_sample_ids"] == sorted(samples[2:])
    assert report["complete_coverage_attested"] is False
    assert report["qualification_granted"] is False


def test_distinct_labels_without_complete_coverage_remain_unresolved() -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, _, _ = fixture
    partial = _lineage(
        instrument,
        custodian,
        signer,
        {sample: f"parent-{index}" for index, sample in enumerate(samples[:-1])},
        complete=False,
    )
    report = _diagnose(fixture, [partial])
    assert report["status"] == "UNRESOLVED"
    assert report["reason"] == "lineage_coverage_incomplete"
    assert report["missing_sample_ids"] == [samples[-1]]

    # Even fragments whose union covers every sample have no completeness attestation.
    tail = _lineage(instrument, custodian, signer, {samples[-1]: "parent-tail"}, complete=False)
    report = _diagnose(fixture, [partial, tail])
    assert report["covered_sample_ids"] == sorted(samples)
    assert report["status"] == "UNRESOLVED"

    false_complete = deepcopy(partial)
    false_complete["payload"]["complete_for_recorded_samples"] = True
    false_complete["signature"] = _signature(signer, signed_bytes(false_complete))
    report = _diagnose(fixture, [false_complete])
    assert report["status"] == "UNRESOLVED"
    assert report["lineage_assertion_authenticated"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "unsigned",
        "wrong_key",
        "wrong_case",
        "wrong_target",
        "wrong_instrument",
        "wrong_custodian",
        "wrong_registry",
        "wrong_semantics",
        "unknown_sample",
        "duplicate_sample",
    ],
)
def test_lineage_tampering_and_unenrolled_assertions_fail_closed(mutation: str) -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, _, _ = fixture
    record = _lineage(
        instrument,
        custodian,
        signer,
        {samples[0]: "parent-1", samples[1]: "parent-1"},
        complete=False,
    )
    if mutation == "unsigned":
        record.pop("signature")
    elif mutation == "wrong_key":
        record["signature"] = _signature(Ed25519PrivateKey.generate(), signed_bytes(record))
    elif mutation == "wrong_case":
        record["payload"]["case_id"] = "bio.other-case.v0"
    elif mutation == "wrong_target":
        record["payload"]["target"] = "Different scientific target"
    elif mutation == "wrong_instrument":
        record["payload"]["instrument_sha256"] = "0" * 64
    elif mutation == "wrong_custodian":
        record["payload"]["custodian_sha256"] = "0" * 64
    elif mutation == "wrong_registry":
        record["payload"]["parent_registry"] = "other-registry"
    elif mutation == "wrong_semantics":
        record["payload"]["lineage_semantics"] = "sample_label_only"
    elif mutation == "unknown_sample":
        record["payload"]["sample_parents"][0]["sample_id"] = "not-recorded"
    else:
        record["payload"]["sample_parents"].append(deepcopy(record["payload"]["sample_parents"][0]))
    if mutation not in {"unsigned", "wrong_key"}:
        record["signature"] = _signature(signer, signed_bytes(record))
    report = _diagnose(fixture, [record])
    assert report["status"] == "UNRESOLVED"
    assert report["lineage_assertion_authenticated"] is False
    assert report["qualification_granted"] is False


def test_conflicting_signed_assignments_and_multiple_authorities_fail_closed() -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, _, _ = fixture
    first = _lineage(instrument, custodian, signer, {samples[0]: "parent-1"}, complete=False)
    conflict = _lineage(instrument, custodian, signer, {samples[0]: "parent-2"}, complete=False)
    report = _diagnose(fixture, [first, conflict])
    assert report["status"] == "UNRESOLVED"
    assert report["lineage_assertion_authenticated"] is False

    # A bad second record may not be discarded after a signed collision.
    collision = _lineage(
        instrument,
        custodian,
        signer,
        {samples[0]: "parent-1", samples[1]: "parent-1"},
        complete=False,
    )
    report = _diagnose(fixture, [collision, conflict])
    assert report["status"] == "UNRESOLVED"


def test_l1_record_signatures_and_exact_instrument_binding_are_prerequisites() -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, l1_keys, lineage_key = fixture
    record = _lineage(
        instrument,
        custodian,
        signer,
        {samples[0]: "same", samples[1]: "same"},
        complete=False,
    )
    raw_record = _raw(record)
    no_l1_key = diagnose_bio_mde(instrument, custodian, [raw_record], [lineage_key], [])
    assert no_l1_key["status"] == "UNRESOLVED"
    assert no_l1_key["l1_record_signatures_verified"] is False

    edited_instrument = deepcopy(json.loads(instrument))
    edited_instrument["payload"]["pairs"][0]["control"] = "999"
    tampered = diagnose_bio_mde(
        _raw(edited_instrument), custodian, [raw_record], [lineage_key], l1_keys
    )
    assert tampered["status"] == "UNRESOLVED"

    # The envelope signature remains valid, but the custodian-to-instrument hash is stale.
    reformatted_instrument = json.dumps(json.loads(instrument), indent=2).encode("utf-8")
    rebound = diagnose_bio_mde(
        reformatted_instrument, custodian, [raw_record], [lineage_key], l1_keys
    )
    assert rebound["status"] == "UNRESOLVED"
    assert rebound["reason"] == "invalid_l1_record_bytes"


def test_duplicate_json_keys_are_not_accepted_as_a_binding_ambiguity() -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, l1_keys, lineage_key = fixture
    record = _lineage(
        instrument,
        custodian,
        signer,
        {samples[0]: "shared", samples[1]: "shared"},
        complete=False,
    )
    raw_record = _raw(record)
    duplicate = raw_record.replace(b'"issuer_id":"lineage-office-1",', b'"issuer_id":"lineage-office-1","issuer_id":"attacker",')
    report = diagnose_bio_mde(instrument, custodian, [duplicate], [lineage_key], l1_keys)
    assert report["status"] == "UNRESOLVED"
    assert report["lineage_assertion_authenticated"] is False

    duplicate_l1 = instrument.replace(
        b'"issuer_id":"device-registry-1",',
        b'"issuer_id":"device-registry-1","issuer_id":"attacker",',
    )
    report = diagnose_bio_mde(duplicate_l1, custodian, [raw_record], [lineage_key], l1_keys)
    assert report["status"] == "UNRESOLVED"


def test_resigned_information_ablations_restore_ambiguity_without_signature_failure() -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, _, _ = fixture
    complete = _lineage(
        instrument,
        custodian,
        signer,
        {sample: f"parent-{index}" for index, sample in enumerate(samples)},
        complete=True,
    )
    assert _diagnose(fixture, [complete])["status"] == "TARGET_TRUE_UNDER_ATTESTED_LINEAGE"

    missing_sample = deepcopy(complete)
    missing_sample["payload"]["sample_parents"].pop()
    missing_sample["payload"]["complete_for_recorded_samples"] = False
    missing_sample["signature"] = _signature(signer, signed_bytes(missing_sample))
    incomplete = _diagnose(fixture, [missing_sample])
    assert incomplete["status"] == "UNRESOLVED"
    assert incomplete["lineage_assertion_authenticated"] is True
    assert incomplete["reason"] == "lineage_coverage_incomplete"
    assert incomplete["missing_sample_ids"] == [samples[-1]]

    no_completeness_claim = deepcopy(complete)
    no_completeness_claim["payload"]["complete_for_recorded_samples"] = False
    no_completeness_claim["signature"] = _signature(signer, signed_bytes(no_completeness_claim))
    full_but_unattested = _diagnose(fixture, [no_completeness_claim])
    assert full_but_unattested["status"] == "UNRESOLVED"
    assert full_but_unattested["lineage_assertion_authenticated"] is True
    assert full_but_unattested["covered_sample_ids"] == sorted(samples)
    assert full_but_unattested["complete_coverage_attested"] is False

    tampered = deepcopy(complete)
    tampered["payload"]["complete_for_recorded_samples"] = False
    signature_failure = _diagnose(fixture, [tampered])
    assert signature_failure["status"] == "UNRESOLVED"
    assert signature_failure["lineage_assertion_authenticated"] is False


def test_report_binds_host_public_keys_and_trust_configuration() -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, l1_keys, lineage_key = fixture
    record = _lineage(
        instrument,
        custodian,
        signer,
        {sample: f"parent-{index}" for index, sample in enumerate(samples)},
        complete=True,
    )
    report = _diagnose(fixture, [record])
    descriptors = report["trusted_public_keys"]
    assert {item["role"] for item in descriptors} == {"instrument", "custodian", "lineage"}
    assert report["verified_public_keys"] == descriptors
    assert {item["key_id"] for item in descriptors} == {"key-1", "lineage-key-1"}
    assert [item for item in descriptors if item["role"] == "lineage"][0][
        "parent_registry"
    ] == lineage_key.parent_registry
    expected = {
        (key.role, key.issuer_id, key.key_id): hashlib.sha256(key.public_key).hexdigest()
        for key in l1_keys
    }
    expected[("lineage", lineage_key.issuer_id, lineage_key.key_id)] = hashlib.sha256(
        lineage_key.public_key
    ).hexdigest()
    assert {
        (item["role"], item["issuer_id"], item["key_id"]): item["public_key_sha256"]
        for item in descriptors
    } == expected
    assert report["trust_configuration_sha256"] == hashlib.sha256(
        json.dumps(descriptors, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert "public_key" not in descriptors[0]
    reordered = diagnose_bio_mde(
        instrument, custodian, [_raw(record)], [lineage_key], tuple(reversed(l1_keys))
    )
    assert reordered["trust_configuration_sha256"] == report["trust_configuration_sha256"]

    # A same-named but different host key changes the configuration identity.
    replacement_key = Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    replacement = TrustedLineageKey(
        lineage_key.issuer_id, lineage_key.key_id, lineage_key.parent_registry, replacement_key
    )
    wrong_root = diagnose_bio_mde(
        instrument, custodian, [_raw(record)], [replacement], l1_keys
    )
    assert wrong_root["status"] == "UNRESOLVED"
    assert wrong_root["lineage_assertion_authenticated"] is False
    assert wrong_root["trust_configuration_sha256"] != report["trust_configuration_sha256"]


def test_oversize_total_size_and_deep_json_fail_closed_before_hashing() -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, l1_keys, lineage_key = fixture
    record = _lineage(
        instrument,
        custodian,
        signer,
        {samples[0]: "shared", samples[1]: "shared"},
        complete=False,
    )
    oversized_l1 = diagnose_bio_mde(
        b"{" + b" " * MAX_L1_RECORD_BYTES,
        custodian,
        [_raw(record)],
        [lineage_key],
        l1_keys,
    )
    assert oversized_l1["status"] == "UNRESOLVED"
    assert oversized_l1["reason"] == "resource_limit_exceeded"
    assert oversized_l1["instrument_sha256"] is None
    assert oversized_l1["lineage_record_sha256"] == []

    oversized_lineage = diagnose_bio_mde(
        instrument,
        custodian,
        [b" " * (MAX_LINEAGE_RECORD_BYTES + 1)],
        [lineage_key],
        l1_keys,
    )
    assert oversized_lineage["reason"] == "resource_limit_exceeded"
    assert oversized_lineage["lineage_record_sha256"] == []

    repeated = [b" " * 50_000] * 64
    assert sum(map(len, repeated)) > MAX_TOTAL_INPUT_BYTES
    excessive_total = diagnose_bio_mde(
        instrument, custodian, repeated, [lineage_key], l1_keys
    )
    assert excessive_total["reason"] == "resource_limit_exceeded"
    assert excessive_total["lineage_record_sha256"] == []

    nested_depth = max(10_000, MAX_JSON_DEPTH + 1)
    deeply_nested = b"[" * nested_depth + b"0" + b"]" * nested_depth
    deep_lineage = diagnose_bio_mde(
        instrument, custodian, [deeply_nested], [lineage_key], l1_keys
    )
    assert deep_lineage["status"] == "UNRESOLVED"
    assert deep_lineage["reason"] == "invalid_or_conflicting_lineage"
    assert deep_lineage["l1_record_signatures_verified"] is True
    assert deep_lineage["lineage_assertion_authenticated"] is False

    deep_l1 = diagnose_bio_mde(
        deeply_nested, custodian, [_raw(record)], [lineage_key], l1_keys
    )
    assert deep_l1["status"] == "UNRESOLVED"
    assert deep_l1["reason"] == "invalid_l1_record_bytes"


def test_l1_verifier_resource_exception_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, l1_keys, lineage_key = fixture
    record = _lineage(
        instrument,
        custodian,
        signer,
        {samples[0]: "shared", samples[1]: "shared"},
        complete=False,
    )

    def exhausted(*_args: object, **_kwargs: object) -> bool:
        raise MemoryError("simulated verifier exhaustion")

    monkeypatch.setattr(bio_mde, "_verified", exhausted)
    report = diagnose_bio_mde(
        instrument, custodian, [_raw(record)], [lineage_key], l1_keys
    )
    assert report["status"] == "UNRESOLVED"
    assert report["reason"] == "resource_limit_exceeded"
    assert report["l1_record_signatures_verified"] is False
    assert report["qualification_granted"] is False


@pytest.mark.parametrize(
    ("role", "field"),
    [
        ("lineage", "issuer_id"),
        ("lineage", "key_id"),
        ("lineage", "parent_registry"),
        ("l1", "issuer_id"),
        ("l1", "key_id"),
    ],
)
def test_oversized_host_key_metadata_fails_closed(role: str, field: str) -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, l1_keys, lineage_key = fixture
    record = _lineage(
        instrument,
        custodian,
        signer,
        {samples[0]: "shared", samples[1]: "shared"},
        complete=False,
    )
    oversized = "x" * (MAX_TRUST_LABEL_CHARS + 1)
    if role == "lineage":
        lineage_roots = [replace(lineage_key, **{field: oversized})]
        l1_roots = l1_keys
    else:
        lineage_roots = [lineage_key]
        l1_roots = (replace(l1_keys[0], **{field: oversized}), l1_keys[1])
    report = diagnose_bio_mde(
        instrument, custodian, [_raw(record)], lineage_roots, l1_roots
    )
    assert report["status"] == "UNRESOLVED"
    assert report["reason"] == "invalid_trust_configuration"
    assert report["trust_configuration_sha256"] is None
    assert report["lineage_assertion_authenticated"] is False
