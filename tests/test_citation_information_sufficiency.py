"""Citation identity does not identify the cited works' primary evidence roots."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from test_citation_extraction import CONTEXT, URI, _counts, _pdf, _setup

from app.core.errors import InvalidEvidenceError
from app.profiles.citation_information_sufficiency import (
    analyze_sufficiency,
    disclosed_roots_control,
    observation_equivalence_invariant,
)


def _candidate(tmp_path):
    path, repository, service = _setup(tmp_path)
    pdf = _pdf(
        [
            "Two cited reports address question Q [1] [2].",
            "References",
            "[1] Doe. Report A. arXiv:1234.56789.",
            "[2] Roe. Report B. arXiv:9876.54321.",
        ]
    )
    before = _counts(path)
    preview = service.preview(CONTEXT, pdf, URI)
    assert preview["admission_state"] == "candidate_only"
    assert len(preview["occurrences"]) == 2
    assert all(item["status"] == "body_anchored" for item in preview["occurrences"])
    assert len(preview["bibliographic_matches"]) == 2
    assert all(item["status"] == "reference_anchored" for item in preview["bibliographic_matches"])
    committed = service.commit(CONTEXT, pdf, URI, preview["preview_hash"])
    stored = repository.get_artifact(CONTEXT.workspace_id, committed["artifact_id"])
    assert stored["content_hash"] == preview["preview_hash"]
    bundle = json.loads(stored["content_text"])
    match_ids = tuple(item["id"] for item in bundle["proposal"]["bibliographic_matches"])
    return path, bundle, match_ids, before


def test_two_root_worlds_share_frozen_citation_bundle_without_authority(tmp_path) -> None:
    path, bundle, match_ids, before = _candidate(tmp_path)
    report = analyze_sufficiency(bundle, match_ids)

    assert report["primary_evidence_root_disjointness"] == "UNRESOLVED"
    assert {world["target_holds"] for world in report["counterworlds"]} == {
        True,
        False,
    }
    assert len({world["projected_bundle_sha256"] for world in report["counterworlds"]}) == 1
    assert analyze_sufficiency(deepcopy(bundle), match_ids) == report
    assert _counts(path) == before


def test_observation_equivalence_invariant_preserves_all_citation_fields(tmp_path) -> None:
    _, bundle, match_ids, _ = _candidate(tmp_path)
    targets = [item["target_identifier"] for item in bundle["proposal"]["bibliographic_matches"]]
    disjoint = {
        "visible_bundle": deepcopy(bundle),
        "latent_primary_evidence_roots": {targets[0]: ["root-a"], targets[1]: ["root-b"]},
    }
    shared = {
        "visible_bundle": deepcopy(bundle),
        "latent_primary_evidence_roots": {targets[0]: ["root-a"], targets[1]: ["root-a"]},
    }
    assert observation_equivalence_invariant(bundle, match_ids, disjoint, shared)
    for world in (disjoint, shared):
        visible = world["visible_bundle"]
        assert visible == bundle
        assert visible["source_snapshot"] == bundle["source_snapshot"]
        assert visible["proposal"]["occurrences"] == bundle["proposal"]["occurrences"]
        assert visible["proposal"]["bibliographic_matches"] == bundle["proposal"][
            "bibliographic_matches"
        ]
    same_truth = deepcopy(shared)
    same_truth["latent_primary_evidence_roots"][targets[1]] = ["root-b"]
    assert not observation_equivalence_invariant(bundle, match_ids, disjoint, same_truth)
    wrong_source = deepcopy(shared)
    wrong_source["visible_bundle"]["source_snapshot"]["pdf_sha256"] = "0" * 64
    assert not observation_equivalence_invariant(bundle, match_ids, disjoint, wrong_source)
    wrong_match = deepcopy(shared)
    wrong_match["visible_bundle"]["proposal"]["bibliographic_matches"][1][
        "target_identifier"
    ] = "arxiv:0000.00000"
    assert not observation_equivalence_invariant(bundle, match_ids, disjoint, wrong_match)
    missing_root = deepcopy(shared)
    del missing_root["latent_primary_evidence_roots"][targets[1]]
    assert not observation_equivalence_invariant(bundle, match_ids, disjoint, missing_root)


def test_declared_shared_root_is_refuted_not_missing_information(tmp_path) -> None:
    path, bundle, match_ids, before = _candidate(tmp_path)
    targets = [item["target_identifier"] for item in bundle["proposal"]["bibliographic_matches"]]
    unresolved = analyze_sufficiency(bundle, match_ids)
    shared = disclosed_roots_control(
        bundle,
        match_ids,
        {
            targets[0]: ["dataset-common", "dataset-a"],
            targets[1]: ["dataset-common", "dataset-b"],
        },
    )
    separate = disclosed_roots_control(
        bundle,
        match_ids,
        {target: [f"dataset-{index + 1}"] for index, target in enumerate(targets)},
    )

    assert unresolved["primary_evidence_root_disjointness"] == "UNRESOLVED"
    assert shared["status"] == "REFUTED_IN_DECLARED_CONTROL"
    assert separate["status"] == "NOT_REFUTED_BY_CONTROL"
    assert shared["lineage_authenticated"] is False
    assert separate["qualification_granted"] is False
    assert _counts(path) == before


def test_citation_case_rejects_lineage_smuggling_and_unanchored_match(tmp_path) -> None:
    _, bundle, match_ids, _ = _candidate(tmp_path)
    smuggled = deepcopy(bundle)
    smuggled["proposal"]["bibliographic_matches"][0]["primary_evidence_roots"] = ["dataset-1"]
    with pytest.raises(InvalidEvidenceError):
        analyze_sufficiency(smuggled, match_ids)

    unanchored = deepcopy(bundle)
    unanchored["proposal"]["bibliographic_matches"][0]["status"] = "abstained"
    with pytest.raises(InvalidEvidenceError):
        analyze_sufficiency(unanchored, match_ids)

    with pytest.raises(InvalidEvidenceError):
        analyze_sufficiency(bundle, (match_ids[0], match_ids[0]))


def test_citation_case_rejects_false_anchored_target_or_reference(tmp_path) -> None:
    _, bundle, match_ids, _ = _candidate(tmp_path)
    invented = deepcopy(bundle)
    invented["proposal"]["bibliographic_matches"][0]["target_identifier"] = "arxiv:0000.00000"
    with pytest.raises(InvalidEvidenceError, match="anchored match fields"):
        analyze_sufficiency(invented, match_ids)

    wrong_reference = deepcopy(bundle)
    first, second = wrong_reference["proposal"]["bibliographic_matches"]
    first["reference_anchor"], second["reference_anchor"] = (
        second["reference_anchor"],
        first["reference_anchor"],
    )
    with pytest.raises(InvalidEvidenceError, match="anchored match fields"):
        analyze_sufficiency(wrong_reference, match_ids)

    malformed = deepcopy(bundle)
    malformed["proposal"]["bibliographic_matches"][0]["occurrence_id"] = []
    with pytest.raises(InvalidEvidenceError, match="anchored match fields"):
        analyze_sufficiency(malformed, match_ids)


def test_citation_case_does_not_claim_missing_lineage_for_another_pdf(tmp_path) -> None:
    _, _, service = _setup(tmp_path)
    pdf = _pdf(
        [
            "Two cited reports address question Q [1] [2].",
            "Both reports reuse dataset D1.",
            "References",
            "[1] Doe. Report A. arXiv:1234.56789.",
            "[2] Roe. Report B. arXiv:9876.54321.",
        ]
    )
    bundle, _ = service._prepare(CONTEXT, pdf, URI, None)
    match_ids = tuple(item["id"] for item in bundle["proposal"]["bibliographic_matches"])
    with pytest.raises(InvalidEvidenceError, match="Outside the frozen Citation Case PDF"):
        analyze_sufficiency(bundle, match_ids)
