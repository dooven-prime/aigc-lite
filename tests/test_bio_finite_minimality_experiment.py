"""Finite, synthetic counterworld experiment for Bio MDE information units.

The test signer can issue valid reduced records. This does not assert that a
real laboratory made those statements or that its parent registry is complete.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from itertools import product

import pytest
from test_bio_minimal_disambiguating_evidence import _diagnose, _fixture, _lineage, _raw

from app.profiles.bio_information_sufficiency import observation_equivalence_invariant

PARENT_LABELS = ("culture-X", "culture-Y", "culture-Z")


def _target(world: dict, samples: list[str]) -> bool:
    parents = [world["latent_parent_sources"][sample] for sample in samples]
    return len(set(parents)) == len(parents)


def _admissible(world: dict, visible: dict, samples: list[str], retained: dict) -> bool:
    """Check the declared finite world domain and the reduced evidence fibre."""
    if set(world) != {"observation", "latent_parent_sources"}:
        return False
    if world["observation"] != visible:
        return False
    parents = world["latent_parent_sources"]
    return (
        isinstance(parents, dict)
        and set(parents) == set(samples)
        and all(value in PARENT_LABELS for value in parents.values())
        and all(parents[sample] == parent for sample, parent in retained.items())
    )


def _compatible_worlds(visible: dict, samples: list[str], retained: dict) -> list[dict]:
    worlds = [
        {
            "observation": deepcopy(visible),
            "latent_parent_sources": dict(zip(samples, labels, strict=True)),
        }
        for labels in product(PARENT_LABELS, repeat=len(samples))
    ]
    assert len(worlds) == 27
    return [world for world in worlds if _admissible(world, visible, samples, retained)]


def _signed_retained_mapping(record: dict) -> dict[str, str]:
    return {
        item["sample_id"]: item["canonical_parent_id"]
        for item in record["payload"]["sample_parents"]
    }


@pytest.mark.parametrize("removed_index", [0, 1])
def test_removing_either_shared_parent_mapping_has_an_opposite_target_pair(
    removed_index: int,
) -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, _, _ = fixture
    assert len(samples) == 3
    visible = {"instrument": json.loads(instrument), "custodian": json.loads(custodian)}
    full = dict(zip(samples, ("culture-X", "culture-X", "culture-Y"), strict=True))
    full_record = _lineage(instrument, custodian, signer, full, complete=True)
    full_report = _diagnose(fixture, [full_record])
    assert full_report["status"] == "TARGET_FALSE_UNDER_ATTESTED_LINEAGE"
    assert full_report["lineage_assertion_authenticated"] is True

    # A valid reduced record must withdraw its former completeness assertion.
    # Only the removed sample-parent mapping is discarded as an information unit.
    retained = {
        sample: parent for sample, parent in full.items() if sample != samples[removed_index]
    }
    reduced_record = _lineage(instrument, custodian, signer, retained, complete=False)
    reduced_report = _diagnose(fixture, [reduced_record])
    assert reduced_report["status"] == "UNRESOLVED"
    assert reduced_report["reason"] == "lineage_coverage_incomplete"
    assert reduced_report["l1_record_signatures_verified"] is True
    assert reduced_report["lineage_assertion_authenticated"] is True
    assert reduced_report["missing_sample_ids"] == [samples[removed_index]]
    assert reduced_report["qualification_granted"] is False
    assert reduced_report["lineage_record_sha256"] == [
        hashlib.sha256(_raw(reduced_record)).hexdigest()
    ]
    retained = _signed_retained_mapping(reduced_record)

    # W has 3^3 assignments. The same old observation and the same signed
    # reduced record are held fixed while latent parents vary.
    worlds = _compatible_worlds(visible, samples, retained)
    assert len(worlds) == 3
    assert {_target(world, samples) for world in worlds} == {False, True}
    false_world = next(world for world in worlds if world["latent_parent_sources"] == full)
    true_world = next(world for world in worlds if _target(world, samples))
    assert _admissible(false_world, visible, samples, retained)
    assert _admissible(true_world, visible, samples, retained)
    assert observation_equivalence_invariant(visible, false_world, true_world)
    assert false_world["observation"] == true_world["observation"] == visible
    assert (
        {sample: false_world["latent_parent_sources"][sample] for sample in retained}
        == {sample: true_world["latent_parent_sources"][sample] for sample in retained}
        == retained
    )

    # These checks would fail if a putative counterworld changed the old
    # sample observation or one of the retained signed assignments.
    changed_observation = deepcopy(true_world)
    changed_observation["observation"]["custodian"]["payload"]["samples"][0]["collected_at"] = (
        "different-observation"
    )
    assert not observation_equivalence_invariant(visible, false_world, changed_observation)
    changed_retained_parent = deepcopy(true_world)
    retained_sample = next(iter(retained))
    changed_retained_parent["latent_parent_sources"][retained_sample] = "culture-Z"
    assert not _admissible(changed_retained_parent, visible, samples, retained)


def test_third_parent_mapping_is_redundant_for_this_narrow_refutation() -> None:
    fixture = _fixture()
    instrument, custodian, samples, signer, _, _ = fixture
    assert len(samples) == 3
    visible = {"instrument": json.loads(instrument), "custodian": json.loads(custodian)}
    retained = {samples[0]: "culture-X", samples[1]: "culture-X"}
    reduced_record = _lineage(instrument, custodian, signer, retained, complete=False)
    reduced_report = _diagnose(fixture, [reduced_record])
    assert reduced_report["status"] == "TARGET_FALSE_UNDER_ATTESTED_LINEAGE"
    assert reduced_report["lineage_assertion_authenticated"] is True
    assert reduced_report["complete_coverage_attested"] is False
    assert reduced_report["missing_sample_ids"] == [samples[2]]
    assert reduced_report["qualification_granted"] is False
    assert reduced_report["lineage_record_sha256"] == [
        hashlib.sha256(_raw(reduced_record)).hexdigest()
    ]
    retained = _signed_retained_mapping(reduced_record)

    worlds = _compatible_worlds(visible, samples, retained)
    assert len(worlds) == 3
    assert {_target(world, samples) for world in worlds} == {False}
    assert all(_admissible(world, visible, samples, retained) for world in worlds)


def test_two_shared_parent_mappings_form_a_minimal_refutation_subset_in_this_finite_domain() -> (
    None
):
    fixture = _fixture()
    instrument, custodian, samples, signer, _, _ = fixture
    assert len(samples) == 3
    visible = {"instrument": json.loads(instrument), "custodian": json.loads(custodian)}
    pair = {samples[0]: "culture-X", samples[1]: "culture-X"}
    pair_record = _lineage(instrument, custodian, signer, pair, complete=False)
    assert _diagnose(fixture, [pair_record])["status"] == "TARGET_FALSE_UNDER_ATTESTED_LINEAGE"
    pair = _signed_retained_mapping(pair_record)
    assert {_target(world, samples) for world in _compatible_worlds(visible, samples, pair)} == {
        False
    }
    assert {
        _target(world, samples) for world in _compatible_worlds(visible, samples, {})
    } == {False, True}

    for sample in pair:
        singleton = {sample: pair[sample]}
        singleton_record = _lineage(instrument, custodian, signer, singleton, complete=False)
        report = _diagnose(fixture, [singleton_record])
        assert report["status"] == "UNRESOLVED"
        assert report["l1_record_signatures_verified"] is True
        assert report["lineage_assertion_authenticated"] is True
        assert {
            _target(world, samples)
            for world in _compatible_worlds(
                visible, samples, _signed_retained_mapping(singleton_record)
            )
        } == {False, True}
