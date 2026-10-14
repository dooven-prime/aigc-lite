"""Citation certificate records conditional information dependence only."""

from __future__ import annotations

from copy import deepcopy

import pytest
from aigc_lite_casepacks.mde_v0.certificate_common import verify_certificate_hash
from aigc_lite_casepacks.mde_v0.citation import TARGET_TRUE, UNRESOLVED
from aigc_lite_casepacks.mde_v0.citation_certificate import certify_citation_positive
from test_citation_information_sufficiency import _candidate
from test_citation_minimal_disambiguating_evidence import (
    _IDENTITY_ISSUER,
    _IDENTITY_KEY,
    _LINEAGE_ISSUER,
    _LINEAGE_KEY,
    _envelope,
    _evidence,
    _public,
)


def _roots() -> dict:
    return {
        "identity_trust_roots": {
            (_IDENTITY_ISSUER, "enrolled-key-1"): _public(_IDENTITY_KEY)
        },
        "lineage_trust_roots": {
            (_LINEAGE_ISSUER, "enrolled-key-1"): _public(_LINEAGE_KEY)
        },
    }


def _cert(bundle, match_ids, identities, lineage, reductions=None):
    return certify_citation_positive(
        bundle,
        match_ids,
        identities,
        lineage,
        reduced_lineage_by_unit=reductions,
        **_roots(),
    )


def _signed_reduction(payload: dict) -> dict:
    return {
        "attestation": _envelope("lineage", payload),
        "origin_label": "synthetic_test_resigning",
    }


def _by_unit(certificate: dict) -> dict[str, dict]:
    return {item["unit_id"]: item for item in certificate["ablations"]}


def test_positive_certificate_is_deterministic_conditional_and_candidate_only(tmp_path) -> None:
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    original = deepcopy((bundle, identities, lineages))

    certificate = _cert(bundle, match_ids, identities, lineages[0])
    assert _cert(bundle, match_ids, identities, lineages[0]) == certificate
    assert verify_certificate_hash(certificate)
    assert certificate["baseline"]["diagnostic"]["status"] == TARGET_TRUE
    finite = certificate["baseline"]["conditional_finite_model"]
    assert finite["model_nonempty"] is True
    assert finite["target_value_in_this_model"] is True
    assert finite["root_sets_disjoint"] is True
    assert finite["actual_publication_or_data_world_enumerated"] is False
    assert [item["match_id"] for item in finite["work_root_assignments"]] == list(match_ids)
    assert all(item["used_source_node_ids"] for item in finite["work_root_assignments"])
    assert all(item["primary_root_ids"] for item in finite["work_root_assignments"])
    assert "alias index" in finite["decision_rule"]
    assert certificate["baseline"]["claimed_source_record_hashes"]["pdf_sha256"] == (
        bundle["source_snapshot"]["pdf_sha256"]
    )
    assert certificate["baseline"]["input"]["candidate_bundle_sha256"] == (
        certificate["baseline"]["diagnostic"]["candidate_bundle_sha256"]
    )
    assert certificate["candidate_only"] is True
    assert certificate["qualification_granted"] is False
    assert certificate["current_use_bound"] is False
    assert certificate["authorization_granted"] is False
    assert any("not independently" in value for value in certificate["unverified_assumptions"])
    assert (bundle, identities, lineages) == original

    by_unit = _by_unit(certificate)
    for match_id in match_ids:
        item = by_unit[f"identity_record:{match_id}"]
        assert item["classification"] == "UNTESTED"
        assert item["diagnostic"]["status"] == UNRESOLVED
        assert item["verification"] == "SIMULATED_RECORD_OMISSION"
        assert item["new_issuer_statement_authenticated"] is False
        assert item["record_omission_only_shows_schema_dependency"] is True
        assert item["input"]["input_sha256"] != certificate["baseline"]["input"][
            "input_sha256"
        ]
    assert by_unit["lineage_record"]["classification"] == "UNTESTED"
    assert certificate["necessary_information_in_this_bundle"] == []
    assert by_unit["alias_index_complete"]["classification"] == "UNTESTED"
    assert by_unit[f"work_coverage:{match_ids[0]}"]["classification"] == "UNTESTED"
    assert by_unit["edge:0"]["classification"] == "UNTESTED"


def test_resigned_coverage_alias_and_edge_reductions_are_authenticated_and_needed(
    tmp_path,
) -> None:
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    original = lineages[0]["payload"]
    reductions = {}
    alias = deepcopy(original)
    alias["alias_index_complete"] = False
    reductions["alias_index_complete"] = _signed_reduction(alias)
    work = deepcopy(original)
    work["works"][0]["coverage_complete"] = False
    reductions[f"work_coverage:{match_ids[0]}"] = _signed_reduction(work)
    edge = deepcopy(original)
    edge["edges"].pop(0)
    reductions["edge:0"] = _signed_reduction(edge)

    certificate = _cert(bundle, match_ids, identities, lineages[0], reductions)
    assert verify_certificate_hash(certificate)
    by_unit = _by_unit(certificate)
    for unit_id in reductions:
        item = by_unit[unit_id]
        assert item["classification"] == "NECESSARY_IN_THIS_BUNDLE"
        assert item["verification"] == "SIGNED_EXACT_ONE_UNIT_REDUCTION"
        assert item["diagnostic"]["status"] == UNRESOLVED
        assert item["diagnostic"]["identity_assertion_authenticated"] is True
        assert item["diagnostic"]["lineage_assertion_authenticated"] is True
        assert item["origin_label"] == "synthetic_test_resigning"
        assert item["origin_label_authenticated"] is False
        assert item["input"]["lineage_attestation_sha256"] != certificate["baseline"][
            "input"
        ]["lineage_attestation_sha256"]


def test_extra_unreferenced_node_is_redundant_only_in_this_bundle(tmp_path) -> None:
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    augmented = deepcopy(lineages[0]["payload"])
    augmented["nodes"].append({"node_id": "unreferenced-root", "kind": "primary"})
    augmented_envelope = _envelope("lineage", augmented)
    reduced = deepcopy(augmented)
    reduced["nodes"].pop()

    certificate = _cert(
        bundle,
        match_ids,
        identities,
        augmented_envelope,
        {"unused_node:unreferenced-root": _signed_reduction(reduced)},
    )
    item = _by_unit(certificate)["unused_node:unreferenced-root"]
    assert item["classification"] == "REDUNDANT_IN_THIS_BUNDLE"
    assert item["diagnostic"]["status"] == TARGET_TRUE
    assert item["verification"] == "SIGNED_EXACT_ONE_UNIT_REDUCTION"
    assert "unused_node:unreferenced-root" in certificate[
        "redundant_information_in_this_bundle"
    ]


@pytest.mark.parametrize("fault", ["tampered_signature", "wrong_delta", "wrong_issuer"])
def test_invalid_reductions_cannot_count_as_necessary(tmp_path, fault) -> None:
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    payload = deepcopy(lineages[0]["payload"])
    payload["alias_index_complete"] = False
    reduced = _signed_reduction(payload)
    if fault == "tampered_signature":
        reduced["attestation"]["signature"] = "a" * 86
    elif fault == "wrong_delta":
        payload["source_record_sha256"] = "f" * 64
        reduced = _signed_reduction(payload)
    else:
        reduced["attestation"] = _envelope(
            "lineage", payload, issuer="other-lineage-issuer"
        )
    certificate = _cert(
        bundle,
        match_ids,
        identities,
        lineages[0],
        {"alias_index_complete": reduced},
    )
    item = _by_unit(certificate)["alias_index_complete"]
    assert item["classification"] == "INVALID_ABLATION"
    assert item["verification"] == "INVALID_OR_UNAUTHENTICATED_REDUCTION"
    assert "alias_index_complete" not in certificate[
        "necessary_information_in_this_bundle"
    ]


def test_deep_reduction_refuses_certificate_instead_of_leaking_resource_error(tmp_path) -> None:
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    deep = []
    cursor = deep
    for _ in range(40):
        child = []
        cursor.append(child)
        cursor = child
    malformed = deepcopy(lineages[0])
    malformed["payload"]["extra"] = deep
    with pytest.raises(ValueError, match="resource limit"):
        _cert(
            bundle,
            match_ids,
            identities,
            lineages[0],
            {
                "alias_index_complete": {
                    "attestation": malformed,
                    "origin_label": "synthetic_test_resigning",
                }
            },
        )


def test_wrong_bound_or_conflicting_baseline_cannot_be_certified(tmp_path) -> None:
    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    wrong = deepcopy(identities)
    wrong[0]["payload"]["candidate_bundle_sha256"] = "0" * 64
    wrong[0] = _envelope("identity", wrong[0]["payload"])
    with pytest.raises(ValueError, match="positive MDE replay"):
        _cert(bundle, match_ids, wrong, lineages[0])
    with pytest.raises(ValueError, match="positive MDE replay"):
        certify_citation_positive(
            bundle,
            match_ids,
            identities,
            lineages[0],
            identity_trust_roots={},
            lineage_trust_roots=_roots()["lineage_trust_roots"],
        )


def test_empty_finite_root_assignment_fails_closed_even_if_status_says_true(
    tmp_path, monkeypatch
) -> None:
    import aigc_lite_casepacks.mde_v0.citation_certificate as module

    _, bundle, match_ids, _ = _candidate(tmp_path)
    identities, lineages = _evidence(bundle, match_ids)
    original_diagnose = module.diagnose_citation_mde

    def false_positive(*args, **kwargs):
        report = original_diagnose(*args, **kwargs)
        report["primary_roots_by_match_id"][match_ids[0]] = []
        return report

    monkeypatch.setattr(module, "diagnose_citation_mde", false_positive)
    with pytest.raises(ValueError, match="finite model is empty"):
        _cert(bundle, match_ids, identities, lineages[0])
