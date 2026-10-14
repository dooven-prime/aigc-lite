"""Host-enrolled, synthetic Citation MDE evidence for the frozen v1 bundle."""

from __future__ import annotations

import base64
import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
from aigc_lite_casepacks.mde_v0.citation import (
    IDENTITY_VERSION,
    LINEAGE_VERSION,
    TARGET_FALSE,
    TARGET_TRUE,
    UNRESOLVED,
    citation_mde_signing_bytes,
    diagnose_citation_mde,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_citation_information_sufficiency import _candidate

_IDENTITY_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
_LINEAGE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))
_IDENTITY_ISSUER = "synthetic-bibliographic-registry"
_LINEAGE_ISSUER = "synthetic-data-provenance-registry"


def _public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _envelope(role: str, payload: dict, *, issuer: str | None = None, key=None) -> dict:
    if role == "identity":
        version, issuer, key = (
            IDENTITY_VERSION,
            issuer or _IDENTITY_ISSUER,
            key or _IDENTITY_KEY,
        )
    else:
        version, issuer, key = (
            LINEAGE_VERSION,
            issuer or _LINEAGE_ISSUER,
            key or _LINEAGE_KEY,
        )
    value = {
        "contract_version": version,
        "issuer_id": issuer,
        "key_id": "enrolled-key-1",
        "payload": payload,
        "signature": "",
    }
    value["signature"] = base64.urlsafe_b64encode(
        key.sign(citation_mde_signing_bytes(role, value))
    ).decode("ascii").rstrip("=")
    return value


def _evidence(bundle, match_ids, *, shared=False, complete=True, aliases=False):
    from app.core.qualification import canonical_hash

    digest = canonical_hash(bundle)
    matches = {item["id"]: item for item in bundle["proposal"]["bibliographic_matches"]}
    identities = []
    for index, match_id in enumerate(match_ids):
        identities.append(
            _envelope(
                "identity",
                {
                    "candidate_bundle_sha256": digest,
                    "match_id": match_id,
                    "candidate_identifier": matches[match_id]["target_identifier"],
                    "canonical_work_id": f"synthetic-work-{index + 1}",
                    "metadata_record_sha256": str(index + 1) * 64,
                },
            )
        )

    first_primary = "primary-root-a"
    second_primary = "primary-root-b" if aliases else first_primary if shared else "primary-root-b"
    nodes = [
        {"node_id": first_primary, "kind": "primary"},
        {"node_id": "dataset-label-a", "kind": "derivative"},
        {"node_id": "dataset-label-b", "kind": "derivative"},
    ]
    if second_primary != first_primary:
        nodes.append({"node_id": second_primary, "kind": "primary"})
    edges = [
        {"kind": "derived_from", "from": "dataset-label-a", "to": first_primary},
        {"kind": "derived_from", "from": "dataset-label-b", "to": second_primary},
    ]
    if aliases:
        edges.append({"kind": "alias_of", "from": first_primary, "to": second_primary})
    lineage = _envelope(
        "lineage",
        {
            "candidate_bundle_sha256": digest,
            "selected_match_ids": list(match_ids),
            "works": [
                {
                    "match_id": match_id,
                    "canonical_work_id": f"synthetic-work-{index + 1}",
                    "used_source_node_ids": [f"dataset-label-{'a' if index == 0 else 'b'}"],
                    "coverage_complete": complete,
                }
                for index, match_id in enumerate(match_ids)
            ],
            "nodes": nodes,
            "edges": edges,
            "alias_index_complete": complete,
            "source_record_sha256": "3" * 64,
        },
    )
    return identities, [lineage]


def _diagnose(bundle, match_ids, identities, lineages, **overrides):
    options = {
        "identity_trust_roots": {(_IDENTITY_ISSUER, "enrolled-key-1"): _public(_IDENTITY_KEY)},
        "lineage_trust_roots": {(_LINEAGE_ISSUER, "enrolled-key-1"): _public(_LINEAGE_KEY)},
    }
    options.update(overrides)
    return diagnose_citation_mde(bundle, match_ids, identities, lineages, **options)


def test_complete_signed_distinct_sources_decide_only_the_narrow_target(tmp_path):
    path, bundle, match_ids, before = _candidate(tmp_path)
    original = deepcopy(bundle)
    identities, lineages = _evidence(bundle, match_ids)
    result = _diagnose(bundle, match_ids, identities, lineages)

    assert result["status"] == TARGET_TRUE
    assert result["coverage_complete"] is True
    assert result["identity_assertion_authenticated"] is True
    assert result["lineage_assertion_authenticated"] is True
    assert result["qualification_granted"] is False
    assert result["canonical_work_ids"] == ["synthetic-work-1", "synthetic-work-2"]
    from app.core.qualification import canonical_hash

    assert result["identity_attestation_sha256"] == [
        canonical_hash(item) for item in identities
    ]
    assert result["lineage_attestation_sha256"] == canonical_hash(lineages[0])
    root = Path(__file__).parents[1]
    assert result["checker_dependency_sha256"] == {
        "frozen_v1_checker": hashlib.sha256(
            (root / "app/profiles/citation_information_sufficiency.py").read_bytes()
        ).hexdigest(),
        "frozen_v1_case_lock": hashlib.sha256(
            (root / "contracts/information-sufficiency/citation/case-lock.json").read_bytes()
        ).hexdigest(),
    }
    assert bundle == original
    assert _diagnose(bundle, match_ids, identities, lineages) == result
    assert __import__("test_citation_extraction")._counts(path) == before


@pytest.mark.parametrize("aliases", [False, True])
def test_one_signed_common_primary_root_refutes_even_with_partial_coverage(tmp_path, aliases):
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(
        bundle, match_ids, shared=not aliases, complete=False, aliases=aliases
    )
    result = _diagnose(bundle, match_ids, identities, lineages)
    assert result["status"] == TARGET_FALSE
    assert result["coverage_complete"] is False
    assert len(set(result["primary_roots_by_match_id"][match_ids[0]]) & set(
        result["primary_roots_by_match_id"][match_ids[1]]
    )) == 1
    assert result["qualification_granted"] is False


def test_missing_identity_or_coverage_restores_ambiguity(tmp_path):
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids, complete=False)
    assert _diagnose(bundle, match_ids, identities, lineages)["status"] == UNRESOLVED
    assert _diagnose(bundle, match_ids, identities[:1], lineages)["status"] == UNRESOLVED
    assert _diagnose(bundle, match_ids, identities, [])["status"] == UNRESOLVED
    assert _diagnose(
        bundle, match_ids, identities, lineages, identity_trust_roots={}
    )["status"] == UNRESOLVED
    assert _diagnose(
        bundle, match_ids, identities, lineages, lineage_trust_roots={}
    )["status"] == UNRESOLVED


@pytest.mark.parametrize("incomplete_field", ["alias_index", "one_work", "derivation_parent"])
def test_each_completeness_assertion_is_required_for_positive_finding(tmp_path, incomplete_field):
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    payload = deepcopy(lineages[0]["payload"])
    if incomplete_field == "alias_index":
        payload["alias_index_complete"] = False
    elif incomplete_field == "one_work":
        payload["works"][1]["coverage_complete"] = False
    else:
        payload["edges"] = payload["edges"][1:]
    result = _diagnose(bundle, match_ids, identities, [_envelope("lineage", payload)])
    assert result["status"] == UNRESOLVED
    assert result["coverage_complete"] is False
    assert result["lineage_assertion_authenticated"] is True
    assert result["reason"] == "lineage_or_alias_coverage_incomplete"


def test_two_candidates_resolving_to_one_work_cannot_prove_disjointness(tmp_path):
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    identities[1]["payload"]["canonical_work_id"] = "synthetic-work-1"
    identities[1] = _envelope("identity", identities[1]["payload"])
    lineage_payload = deepcopy(lineages[0]["payload"])
    lineage_payload["works"][1]["canonical_work_id"] = "synthetic-work-1"
    result = _diagnose(
        bundle, match_ids, identities, [_envelope("lineage", lineage_payload)]
    )
    assert result["status"] == UNRESOLVED
    assert result["reason"] == "same_canonical_work_without_shared_root"


@pytest.mark.parametrize(
    "target",
    ["identity_bundle", "identity_target", "identity_work", "lineage_bundle", "lineage_match"],
)
def test_signed_but_wrong_binding_is_unresolved(tmp_path, target):
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    identities, lineages = deepcopy(identities), deepcopy(lineages)
    if target == "identity_bundle":
        identities[0]["payload"]["candidate_bundle_sha256"] = "0" * 64
        identities[0] = _envelope("identity", identities[0]["payload"])
    elif target == "identity_target":
        identities[0]["payload"]["candidate_identifier"] = "arxiv:0000.00000"
        identities[0] = _envelope("identity", identities[0]["payload"])
    elif target == "identity_work":
        identities[0]["payload"]["canonical_work_id"] = "other-work"
        identities[0] = _envelope("identity", identities[0]["payload"])
    elif target == "lineage_bundle":
        lineages[0]["payload"]["candidate_bundle_sha256"] = "0" * 64
        lineages[0] = _envelope("lineage", lineages[0]["payload"])
    else:
        lineages[0]["payload"]["selected_match_ids"] = list(reversed(match_ids))
        lineages[0] = _envelope("lineage", lineages[0]["payload"])
    assert _diagnose(bundle, match_ids, identities, lineages)["status"] == UNRESOLVED


def test_tampering_unsigned_data_and_conflicting_records_fail_closed(tmp_path):
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    tampered = deepcopy(lineages)
    tampered[0]["payload"]["works"][0]["coverage_complete"] = False
    tampered_result = _diagnose(bundle, match_ids, identities, tampered)
    assert tampered_result["status"] == UNRESOLVED
    assert tampered_result["lineage_assertion_authenticated"] is False
    assert tampered_result["reason"] == "lineage_signature_or_schema_invalid"

    unsigned = deepcopy(identities)
    unsigned[0]["signature"] = ""
    assert _diagnose(bundle, match_ids, unsigned, lineages)["status"] == UNRESOLVED

    self_enrolled = deepcopy(identities)
    self_enrolled[0]["public_key"] = _public(_IDENTITY_KEY).hex()
    assert _diagnose(bundle, match_ids, self_enrolled, lineages)["status"] == UNRESOLVED

    assert _diagnose(bundle, match_ids, identities + identities[:1], lineages)[
        "status"
    ] == UNRESOLVED
    assert _diagnose(bundle, match_ids, identities, lineages + lineages)[
        "status"
    ] == UNRESOLVED


@pytest.mark.parametrize("fault", ["cycle", "missing_node", "alias_kind_conflict", "open_path"])
def test_bad_or_incomplete_derivative_graph_cannot_prove_disjointness(tmp_path, fault):
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    payload = deepcopy(lineages[0]["payload"])
    if fault == "cycle":
        payload["edges"].append(
            {"kind": "derived_from", "from": "dataset-label-a", "to": "dataset-label-b"}
        )
        payload["edges"].append(
            {"kind": "derived_from", "from": "dataset-label-b", "to": "dataset-label-a"}
        )
    elif fault == "missing_node":
        payload["works"][0]["used_source_node_ids"] = ["unknown-node"]
    elif fault == "alias_kind_conflict":
        payload["edges"].append(
            {"kind": "alias_of", "from": "dataset-label-a", "to": "primary-root-a"}
        )
    else:
        payload["edges"] = payload["edges"][1:]
    lineages = [_envelope("lineage", payload)]
    result = _diagnose(bundle, match_ids, identities, lineages)
    assert result["status"] == UNRESOLVED
    assert result["qualification_granted"] is False


def test_independent_enrolled_source_role_is_required(tmp_path):
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    forged = _envelope(
        "lineage", lineages[0]["payload"], issuer=_IDENTITY_ISSUER, key=_IDENTITY_KEY
    )
    result = _diagnose(
        bundle,
        match_ids,
        identities,
        [forged],
        lineage_trust_roots={(_IDENTITY_ISSUER, "enrolled-key-1"): _public(_IDENTITY_KEY)},
    )
    assert result["status"] == UNRESOLVED
    assert result["reason"] == "lineage_signer_not_separate"


def test_dummy_source_digests_do_not_become_verified_source_bytes(tmp_path):
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    # These are syntactically valid but fictitious digests. The diagnostic
    # authenticates the signed assertion, not the unseen metadata/data bytes.
    for identity in identities:
        identity["payload"]["metadata_record_sha256"] = "f" * 64
    identities = [_envelope("identity", item["payload"]) for item in identities]
    lineages[0]["payload"]["source_record_sha256"] = "e" * 64
    lineages = [_envelope("lineage", lineages[0]["payload"])]

    result = _diagnose(bundle, match_ids, identities, lineages)
    assert result["status"] == TARGET_TRUE
    assert result["identity_assertion_authenticated"] is True
    assert result["lineage_assertion_authenticated"] is True
    assert any("underlying bytes are not fetched" in item for item in result["limitations"])


def test_resource_preflight_rejects_deep_large_cyclic_and_non_json_inputs(tmp_path):
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)

    deep = []
    cursor = deep
    for _ in range(40):
        child = []
        cursor.append(child)
        cursor = child
    cyclic = {}
    cyclic["self"] = cyclic
    repeated = []
    abnormal_values = [
        deep,
        cyclic,
        {"first": repeated, "second": repeated},
        {"first": "a" * 600_000, "second": "b" * 600_000},
        {"unpaired_surrogate": "\ud800"},
        object(),
    ]
    for abnormal in abnormal_values:
        submitted = deepcopy(lineages)
        submitted[0]["payload"]["abnormal"] = abnormal
        report = _diagnose(bundle, match_ids, identities, submitted)
        assert report["status"] == UNRESOLVED
        assert report["reason"] == "resource_limits_or_non_json_input"
        assert report["qualification_granted"] is False

    deep_bundle = deepcopy(bundle)
    deep_bundle["limitations"].append(deep)
    assert _diagnose(
        bundle=deep_bundle,
        match_ids=match_ids,
        identities=identities,
        lineages=lineages,
    )["reason"] == "resource_limits_or_non_json_input"

    to_sign = deepcopy(lineages[0])
    to_sign["payload"]["abnormal"] = deep
    with pytest.raises(ValueError, match="resource limit"):
        citation_mde_signing_bytes("lineage", to_sign)


def test_shallow_invalid_frozen_bundle_and_match_ids_are_unresolved(tmp_path):
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    wrong_bundle = deepcopy(bundle)
    wrong_bundle["source_snapshot"]["pdf_sha256"] = "0" * 64
    report = _diagnose(wrong_bundle, match_ids, identities, lineages)
    assert report["status"] == UNRESOLVED
    assert report["reason"] == "frozen_visible_bundle_invalid"

    report = _diagnose(bundle, (match_ids[0], []), identities, lineages)
    assert report["status"] == UNRESOLVED
    assert report["reason"] == "frozen_visible_bundle_invalid"

    report = _diagnose(bundle, (match_ids[0], "x" * 257), identities, lineages)
    assert report["status"] == UNRESOLVED
    assert report["reason"] == "resource_limits_or_non_json_input"


def test_report_fingerprints_host_keys_and_full_trust_configuration(tmp_path):
    from app.core.qualification import canonical_hash

    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    result = _diagnose(bundle, match_ids, identities, lineages)
    assert result["status"] == TARGET_TRUE
    expected_keys = [
        {
            "role": "identity",
            "issuer_id": _IDENTITY_ISSUER,
            "key_id": "enrolled-key-1",
            "public_key_sha256": hashlib.sha256(_public(_IDENTITY_KEY)).hexdigest(),
        },
        {
            "role": "lineage",
            "issuer_id": _LINEAGE_ISSUER,
            "key_id": "enrolled-key-1",
            "public_key_sha256": hashlib.sha256(_public(_LINEAGE_KEY)).hexdigest(),
        },
    ]
    assert result["host_trust_key_fingerprints"] == expected_keys
    assert result["host_trust_config_sha256"] == canonical_hash(expected_keys)
    assert result["used_enrolled_key_fingerprints"] == [
        {**expected_keys[0], "match_id": match_id} for match_id in match_ids
    ] + [expected_keys[1]]

    extra_key = Ed25519PrivateKey.from_private_bytes(bytes(range(65, 97)))
    extended = _diagnose(
        bundle,
        match_ids,
        identities,
        lineages,
        identity_trust_roots={
            (_IDENTITY_ISSUER, "enrolled-key-1"): _public(_IDENTITY_KEY),
            ("unused-issuer", "unused-key"): _public(extra_key),
        },
    )
    assert extended["status"] == TARGET_TRUE
    assert extended["host_trust_config_sha256"] != result["host_trust_config_sha256"]
    assert extended["used_enrolled_key_fingerprints"] == result[
        "used_enrolled_key_fingerprints"
    ]

    rotated_identities = [
        _envelope("identity", item["payload"], key=extra_key) for item in identities
    ]
    rotated = _diagnose(
        bundle,
        match_ids,
        rotated_identities,
        lineages,
        identity_trust_roots={(_IDENTITY_ISSUER, "enrolled-key-1"): _public(extra_key)},
    )
    assert rotated["status"] == TARGET_TRUE
    assert rotated["host_trust_config_sha256"] != result["host_trust_config_sha256"]
    assert rotated["used_enrolled_key_fingerprints"][0]["public_key_sha256"] != result[
        "used_enrolled_key_fingerprints"
    ][0]["public_key_sha256"]

    old_signatures_under_rotated_trust = _diagnose(
        bundle,
        match_ids,
        identities,
        lineages,
        identity_trust_roots={(_IDENTITY_ISSUER, "enrolled-key-1"): _public(extra_key)},
    )
    assert old_signatures_under_rotated_trust["status"] == UNRESOLVED
    assert old_signatures_under_rotated_trust["reason"] == "identity_signature_or_schema_invalid"
    assert old_signatures_under_rotated_trust["host_trust_config_sha256"] == rotated[
        "host_trust_config_sha256"
    ]

    invalid_trust = _diagnose(
        bundle,
        match_ids,
        identities,
        lineages,
        identity_trust_roots={("\ud800", "key"): _public(_IDENTITY_KEY)},
    )
    assert invalid_trust["status"] == UNRESOLVED
    assert invalid_trust["reason"] == "host_trust_config_invalid"
