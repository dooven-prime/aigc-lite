"""Finite citation counterworld Case over one frozen v1 candidate bundle.

This module neither verifies a PDF's external origin nor qualifies a research
claim. It asks only whether the visible citation record fixes the primary
evidence roots of two bibliographic target candidates. The worlds are synthetic
possible assignments, not assertions about real works' histories.
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import Path

from ..core.errors import InvalidEvidenceError
from ..core.qualification import canonical_hash

CASE_ID = "citation.primary-evidence-root-sufficiency.v1"
TARGET = (
    "The works hypothetically denoted by two selected bibliographic target candidates "
    "have disjoint primary evidence roots."
)
CONTRACT_VERSION = "CitationExtractionProposal.v1"
# This Case analyzes one lineage-silent synthetic PDF, not arbitrary papers.
FIXTURE_PDF_SHA256 = "b372b2ed0fa6a1d3b823da76480139c294cc32fb4de7729246fa4df508931d10"
FIXTURE_PAGE_TEXT_SHA256 = "901d420f16387cc4f2b0df88c3d475f03672536a936fb2df51faf27f99424456"
CHECKER_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
EXTRACTOR_SOURCE_SHA256 = hashlib.sha256(
    (Path(__file__).parents[1] / "services" / "citation_extraction.py").read_bytes()
).hexdigest()
_NUMERIC_MARKER = re.compile(r"\[\s*(\d{1,3})\s*\]")
_REFERENCE_MARKER = re.compile(r"(?m)^\[(\d{1,3})\]\s")
_ARXIV_IDENTIFIER = re.compile(r"(?i)(?<!\w)arxiv:\d{4}\.\d{4,5}(?:v\d+)?(?!\w)")

_BUNDLE_FIELDS = {
    "contract_version",
    "admission_state",
    "source_snapshot",
    "checker_source_sha256",
    "proposal",
    "proposal_mode",
    "occurrence_limit",
    "truncated",
    "submitted_by_principal_id",
    "limitations",
}
_SNAPSHOT_FIELDS = {
    "pdf_sha256",
    "pdf_size_bytes",
    "source_uri",
    "extractor",
    "pages",
}
_PAGE_FIELDS = {"page", "text", "text_sha256"}
_PROPOSAL_FIELDS = {
    "contract_version",
    "occurrences",
    "bibliographic_matches",
    "claim_relation_proposals",
    "proposer_model_route",
    "proposer_prompt_hash",
}
_OCCURRENCE_FIELDS = {"id", "anchor", "style", "status", "reason"}
_MATCH_FIELDS = {
    "id",
    "occurrence_id",
    "reference_anchor",
    "target_identifier",
    "status",
    "reason",
}
_ANCHOR_FIELDS = {"page", "start", "end", "quote"}


def _anchor_is_exact(anchor: object, page_texts: dict[int, str]) -> bool:
    if not isinstance(anchor, dict) or set(anchor) != _ANCHOR_FIELDS:
        return False
    page, start, end, quote = (
        anchor["page"],
        anchor["start"],
        anchor["end"],
        anchor["quote"],
    )
    return (
        type(page) is int
        and type(start) is int
        and type(end) is int
        and page in page_texts
        and 0 <= start < end <= len(page_texts[page])
        and isinstance(quote, str)
        and page_texts[page][start:end] == quote
    )


def _visible_bundle(bundle: dict, match_ids: tuple[str, str]) -> tuple[str, str]:
    """Check the exact two-match fixture shape; never infer source lineage."""
    if not isinstance(bundle, dict) or set(bundle) != _BUNDLE_FIELDS:
        raise InvalidEvidenceError("bundle", "Expected the exact frozen v1 bundle fields")
    if (
        bundle["contract_version"] != CONTRACT_VERSION
        or bundle["admission_state"] != "candidate_only"
        or bundle["checker_source_sha256"] != EXTRACTOR_SOURCE_SHA256
        or bundle["proposal_mode"] != "deterministic_baseline"
        or bundle["truncated"] is not False
        or bundle["occurrence_limit"] != 200
        or not isinstance(bundle["submitted_by_principal_id"], str)
        or not isinstance(bundle["limitations"], list)
        or any(not isinstance(item, str) for item in bundle["limitations"])
    ):
        raise InvalidEvidenceError("bundle", "Expected the fixed candidate-only v1 baseline")
    if (
        not isinstance(match_ids, tuple)
        or len(match_ids) != 2
        or any(not isinstance(value, str) or not value for value in match_ids)
        or match_ids[0] == match_ids[1]
    ):
        raise InvalidEvidenceError("match_ids", "Select two distinct bibliography match IDs")

    snapshot = bundle["source_snapshot"]
    if not isinstance(snapshot, dict) or set(snapshot) != _SNAPSHOT_FIELDS:
        raise InvalidEvidenceError("source_snapshot", "Expected the exact v1 source shape")
    if snapshot["pdf_sha256"] != FIXTURE_PDF_SHA256:
        raise InvalidEvidenceError("source_snapshot", "Outside the frozen Citation Case PDF")
    if (
        type(snapshot["pdf_size_bytes"]) is not int
        or snapshot["pdf_size_bytes"] <= 0
        or not isinstance(snapshot["source_uri"], str)
        or not isinstance(snapshot["extractor"], str)
    ):
        raise InvalidEvidenceError("source_snapshot", "Invalid frozen source metadata")
    pages = snapshot["pages"]
    if not isinstance(pages, list) or len(pages) != 1:
        raise InvalidEvidenceError("source_snapshot.pages", "Frozen one-page text is required")
    page_texts: dict[int, str] = {}
    for page in pages:
        if (
            not isinstance(page, dict)
            or set(page) != _PAGE_FIELDS
            or type(page["page"]) is not int
            or page["page"] != 1
            or page["page"] in page_texts
            or not isinstance(page["text"], str)
            or page["text_sha256"] != hashlib.sha256(page["text"].encode()).hexdigest()
        ):
            raise InvalidEvidenceError("source_snapshot.pages", "Invalid frozen page text")
        page_texts[page["page"]] = page["text"]
    if pages[0]["text_sha256"] != FIXTURE_PAGE_TEXT_SHA256:
        raise InvalidEvidenceError("source_snapshot.pages", "Outside the frozen Citation Case text")
    page_text = pages[0]["text"]
    header = re.search(r"(?m)^References\s*$", page_text)
    if header is None:
        raise InvalidEvidenceError("source_snapshot.pages", "Frozen References region is missing")
    reference_hits = list(_REFERENCE_MARKER.finditer(page_text, header.end()))
    reference_entries = {
        hit.group(1): (
            hit.start(),
            reference_hits[index + 1].start()
            if index + 1 < len(reference_hits)
            else len(page_text),
        )
        for index, hit in enumerate(reference_hits)
    }
    if len(reference_entries) != 2 or set(reference_entries) != {"1", "2"}:
        raise InvalidEvidenceError("source_snapshot.pages", "Expected two frozen references")

    proposal = bundle["proposal"]
    if (
        not isinstance(proposal, dict)
        or set(proposal) != _PROPOSAL_FIELDS
        or proposal["contract_version"] != CONTRACT_VERSION
        or proposal["claim_relation_proposals"] != []
        or proposal["proposer_model_route"] is not None
        or proposal["proposer_prompt_hash"] is not None
    ):
        raise InvalidEvidenceError(
            "proposal", "Expected a v1 proposal without unverified claim relations"
        )
    occurrences = proposal["occurrences"]
    matches = proposal["bibliographic_matches"]
    if (
        not isinstance(occurrences, list)
        or not isinstance(matches, list)
        or len(occurrences) != 2
        or len(matches) != 2
    ):
        raise InvalidEvidenceError(
            "proposal", "This finite Case requires exactly two occurrences and matches"
        )
    occurrence_ids: set[str] = set()
    occurrences_by_id = {}
    marker_numbers: set[str] = set()
    for occurrence in occurrences:
        anchor = occurrence.get("anchor") if isinstance(occurrence, dict) else None
        marker = (
            _NUMERIC_MARKER.fullmatch(anchor.get("quote", ""))
            if isinstance(anchor, dict) and isinstance(anchor.get("quote"), str)
            else None
        )
        if (
            not isinstance(occurrence, dict)
            or set(occurrence) != _OCCURRENCE_FIELDS
            or not isinstance(occurrence["id"], str)
            or not occurrence["id"]
            or occurrence["id"] in occurrence_ids
            or occurrence["status"] != "body_anchored"
            or occurrence["reason"] is not None
            or not _anchor_is_exact(anchor, page_texts)
            or occurrence["style"] != "numeric"
            or anchor["start"] >= header.start()
            or marker is None
            or marker.group(1) not in reference_entries
            or marker.group(1) in marker_numbers
        ):
            raise InvalidEvidenceError(
                "proposal.occurrences", "Expected exact anchored occurrence fields"
            )
        occurrence_ids.add(occurrence["id"])
        occurrences_by_id[occurrence["id"]] = occurrence
        marker_numbers.add(marker.group(1))

    matches_by_id = {}
    matched_occurrences: set[str] = set()
    for match in matches:
        occurrence_id = match.get("occurrence_id") if isinstance(match, dict) else None
        occurrence = (
            occurrences_by_id.get(occurrence_id)
            if isinstance(occurrence_id, str)
            else None
        )
        marker = (
            _NUMERIC_MARKER.fullmatch(occurrence["anchor"]["quote"])
            if occurrence is not None
            else None
        )
        reference_span = reference_entries.get(marker.group(1)) if marker is not None else None
        reference_anchor = match.get("reference_anchor") if isinstance(match, dict) else None
        identifier = match.get("target_identifier") if isinstance(match, dict) else None
        anchored_identifiers = (
            {
                item.group().casefold()
                for item in _ARXIV_IDENTIFIER.finditer(reference_anchor["quote"])
            }
            if isinstance(reference_anchor, dict)
            and isinstance(reference_anchor.get("quote"), str)
            else set()
        )
        if (
            not isinstance(match, dict)
            or set(match) != _MATCH_FIELDS
            or not isinstance(match["id"], str)
            or not match["id"]
            or match["id"] in matches_by_id
            or not isinstance(match["occurrence_id"], str)
            or match["occurrence_id"] not in occurrence_ids
            or match["occurrence_id"] in matched_occurrences
            or match["status"] != "reference_anchored"
            or match["reason"] is not None
            or not isinstance(identifier, str)
            or not identifier
            or not _anchor_is_exact(reference_anchor, page_texts)
            or reference_span is None
            or (reference_anchor["start"], reference_anchor["end"]) != reference_span
            or anchored_identifiers != {identifier.casefold()}
        ):
            raise InvalidEvidenceError(
                "proposal.bibliographic_matches", "Expected exact anchored match fields"
            )
        matches_by_id[match["id"]] = match
        matched_occurrences.add(match["occurrence_id"])
    if set(matches_by_id) != set(match_ids):
        raise InvalidEvidenceError("match_ids", "Match IDs must select the frozen two matches")
    targets = tuple(matches_by_id[match_id]["target_identifier"] for match_id in match_ids)
    if targets[0].casefold() == targets[1].casefold():
        raise InvalidEvidenceError("match_ids", "Candidate target identifiers must be distinct")
    return targets


def _world(bundle: dict, roots_by_target: dict[str, list[str]]) -> dict:
    return {
        "visible_bundle": deepcopy(bundle),
        "latent_primary_evidence_roots": deepcopy(roots_by_target),
    }


def _projection(world: dict) -> dict:
    return deepcopy(world["visible_bundle"])


def _target_holds(world: dict, targets: tuple[str, str]) -> bool:
    roots = world["latent_primary_evidence_roots"]
    return set(roots[targets[0]]).isdisjoint(roots[targets[1]])


def _world_summary(kind: str, world: dict, targets: tuple[str, str]) -> dict:
    return {
        "kind": kind,
        "latent_primary_evidence_roots": world["latent_primary_evidence_roots"],
        "projected_bundle_sha256": canonical_hash(_projection(world)),
        "target_holds": _target_holds(world, targets),
    }


def observation_equivalence_invariant(
    bundle: dict, match_ids: tuple[str, str], first_world: dict, second_world: dict
) -> bool:
    """Check complete visible-bundle equality and the latent-root domain."""
    try:
        targets = _visible_bundle(bundle, match_ids)
    except InvalidEvidenceError:
        return False
    for world in (first_world, second_world):
        if not isinstance(world, dict) or set(world) != {
            "visible_bundle",
            "latent_primary_evidence_roots",
        }:
            return False
        if _projection(world) != bundle:
            return False
        roots_by_target = world["latent_primary_evidence_roots"]
        if not isinstance(roots_by_target, dict) or set(roots_by_target) != set(targets):
            return False
        if any(
            not isinstance(roots, list)
            or not roots
            or any(not isinstance(root, str) or not root for root in roots)
            or len(set(roots)) != len(roots)
            for roots in roots_by_target.values()
        ):
            return False
    return _target_holds(first_world, targets) != _target_holds(second_world, targets)


def analyze_sufficiency(bundle: dict, match_ids: tuple[str, str]) -> dict:
    """Construct opposite target answers with identical frozen citation data."""
    targets = _visible_bundle(bundle, match_ids)
    disjoint = _world(bundle, {targets[0]: ["primary-root-1"], targets[1]: ["primary-root-2"]})
    shared = _world(bundle, {targets[0]: ["primary-root-1"], targets[1]: ["primary-root-1"]})
    if not observation_equivalence_invariant(bundle, match_ids, disjoint, shared):
        raise AssertionError("Citation counterworlds no longer separate the target")
    observation_hash = canonical_hash(bundle)
    return {
        "case_id": CASE_ID,
        "checker_source_sha256": CHECKER_SOURCE_SHA256,
        "target": TARGET,
        "observation_sha256": observation_hash,
        "selected_match_ids": list(match_ids),
        "selected_target_identifiers": list(targets),
        "primary_evidence_root_disjointness": "UNRESOLVED",
        "reason": "primary_evidence_root_lineage_not_observed",
        "counterworlds": [
            _world_summary("disjoint_primary_roots", disjoint, targets),
            _world_summary("shared_primary_root", shared, targets),
        ],
        "projected_bundle_sha256": observation_hash,
        "limitations": [
            "Worlds are synthetic possibilities, not authenticated research histories.",
            "This Case is relative to its frozen two-citation fixture; PDF text may disclose provenance, so it does not diagnose arbitrary PDFs.",
            "The canonical observation digest does not authenticate the stored Artifact or original PDF bytes.",
            "Bibliographic target candidates are not authenticated paper identities; matches do not verify claim support or statistical independence.",
            "No qualification, current-use binding, or execution authority is issued.",
        ],
    }


def disclosed_roots_control(
    bundle: dict, match_ids: tuple[str, str], roots_by_target: dict[str, list[str]]
) -> dict:
    """Check declared synthetic roots without authenticating their provenance."""
    targets = _visible_bundle(bundle, match_ids)
    if (
        not isinstance(roots_by_target, dict)
        or set(roots_by_target) != set(targets)
        or any(
            not isinstance(roots, list)
            or not roots
            or any(not isinstance(root, str) or not root for root in roots)
            or len(set(roots)) != len(roots)
            for roots in roots_by_target.values()
        )
    ):
        raise InvalidEvidenceError(
            "roots_by_target", "Declare nonempty primary-root sets for both candidates"
        )
    world = _world(bundle, roots_by_target)
    return {
        "case_id": CASE_ID,
        "status": (
            "NOT_REFUTED_BY_CONTROL"
            if _target_holds(world, targets)
            else "REFUTED_IN_DECLARED_CONTROL"
        ),
        "lineage_authenticated": False,
        "qualification_granted": False,
    }
