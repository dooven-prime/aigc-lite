"""Conditional, candidate-only certificate for a positive Citation MDE finding.

The certificate records a finite checker result and reproducible information
ablations. It neither authenticates publication/data history nor grants any
qualification or current use. In particular, an omitted signed record is a
local counterfactual, not a new statement by its issuer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path

from .certificate_common import CERTIFICATE_VERSION, finalize_certificate
from .citation import (
    CASE_ID,
    TARGET_TRUE,
    UNRESOLVED,
    _bounded_hash,
    diagnose_citation_mde,
)

CERTIFICATE_KIND = "citation.positive-conditional-disambiguation.v0"
CERTIFICATE_GENERATOR_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _input_identity(
    bundle: dict,
    match_ids: tuple[str, str],
    identity_attestations: list[dict],
    lineage_attestations: list[dict],
    trust_config_sha256: str,
) -> dict:
    """Hash actual submitted evidence independently of a signature's claims."""
    parts = {
        "candidate_bundle_sha256": _bounded_hash(bundle),
        "selected_match_ids": list(match_ids),
        "identity_attestation_sha256": [_bounded_hash(item) for item in identity_attestations],
        "lineage_attestation_sha256": [_bounded_hash(item) for item in lineage_attestations],
        "host_trust_config_sha256": trust_config_sha256,
    }
    return {**parts, "input_sha256": _bounded_hash(parts)}


def _decision_view(report: dict) -> dict:
    return {
        "status": report["status"],
        "reason": report["reason"],
        "identity_assertion_authenticated": report["identity_assertion_authenticated"],
        "lineage_assertion_authenticated": report["lineage_assertion_authenticated"],
        "coverage_complete": report["coverage_complete"],
        "primary_roots_by_match_id": report["primary_roots_by_match_id"],
        "qualification_granted": report["qualification_granted"],
    }


def _finite_model(report: dict, match_ids: tuple[str, str], lineage_payload: dict) -> dict:
    """Expose one finite rooted assignment, not a real publication history."""
    works = {work["match_id"]: work for work in lineage_payload["works"]}
    assignments = [
        {
            "match_id": match_id,
            "canonical_work_id": report["canonical_work_ids"][index],
            "used_source_node_ids": list(works[match_id]["used_source_node_ids"]),
            "primary_root_ids": list(report["primary_roots_by_match_id"][match_id]),
            "root_basis": "authenticated_issuer_assertion_not_observed_publication_history",
        }
        for index, match_id in enumerate(match_ids)
    ]
    if (
        len(assignments) != 2
        or lineage_payload["alias_index_complete"] is not True
        or not all(works[match_id]["coverage_complete"] is True for match_id in match_ids)
        or report["coverage_complete"] is not True
        or any(not item["used_source_node_ids"] or not item["primary_root_ids"] for item in assignments)
        or not set(assignments[0]["primary_root_ids"]).isdisjoint(
            assignments[1]["primary_root_ids"]
        )
    ):
        raise ValueError("Citation finite rooted assignment is empty or inconsistent")
    return {
        "model_nonempty": True,
        "model_scope": "one finite rooted graph assignment under authenticated issuer assertions",
        "work_root_assignments": assignments,
        "alias_index_complete_asserted": lineage_payload["alias_index_complete"],
        "per_work_coverage_complete_asserted": [
            works[match_id]["coverage_complete"] for match_id in match_ids
        ],
        "source_paths_closed_by_frozen_checker": True,
        "root_sets_disjoint": True,
        "decision_rule": (
            "Under signed complete used-source coverage, closed root paths, and a complete "
            "cross-work alias index, disjoint primary-root sets decide this frozen "
            "two-work target in every finite completion allowed by the declared graph."
        ),
        "target_value_in_this_model": True,
        "actual_publication_or_data_world_enumerated": False,
    }


def _signed_unit_ids(lineage_payload: dict, match_ids: tuple[str, str]) -> list[str]:
    return [
        "alias_index_complete",
        *(f"work_coverage:{match_id}" for match_id in match_ids),
        *(f"edge:{index}" for index, _ in enumerate(lineage_payload["edges"])),
        *(
            f"unused_node:{node['node_id']}"
            for node in lineage_payload["nodes"]
            if node["node_id"] not in {
                source
                for work in lineage_payload["works"]
                for source in work["used_source_node_ids"]
            }
            and node["node_id"] not in {
                endpoint
                for edge in lineage_payload["edges"]
                for endpoint in (edge["from"], edge["to"])
            }
        ),
    ]


def _reduced_payload(payload: dict, unit_id: str) -> dict:
    """Compute the only permitted one-unit deletion; never sign it here."""
    reduced = deepcopy(payload)
    if unit_id == "alias_index_complete":
        if payload["alias_index_complete"] is not True:
            raise ValueError("Alias completeness was not asserted")
        reduced["alias_index_complete"] = False
    elif unit_id.startswith("work_coverage:"):
        match_id = unit_id.partition(":")[2]
        selected = [work for work in reduced["works"] if work["match_id"] == match_id]
        if len(selected) != 1 or selected[0]["coverage_complete"] is not True:
            raise ValueError("Work coverage unit was not asserted")
        selected[0]["coverage_complete"] = False
    elif unit_id.startswith("edge:"):
        index_text = unit_id.partition(":")[2]
        if not index_text.isdecimal() or str(int(index_text)) != index_text:
            raise ValueError("Invalid edge unit")
        index = int(index_text)
        if index >= len(reduced["edges"]):
            raise ValueError("Edge unit is absent")
        reduced["edges"].pop(index)
    elif unit_id.startswith("unused_node:"):
        node_id = unit_id.partition(":")[2]
        if node_id not in {node["node_id"] for node in reduced["nodes"]}:
            raise ValueError("Node unit is absent")
        if any(
            node_id in work["used_source_node_ids"] for work in reduced["works"]
        ) or any(node_id in (edge["from"], edge["to"]) for edge in reduced["edges"]):
            raise ValueError("Node participates in the visible lineage graph")
        reduced["nodes"] = [node for node in reduced["nodes"] if node["node_id"] != node_id]
    else:
        raise ValueError("Unknown Citation ablation unit")
    return reduced


def _one_ablation(
    unit_id: str,
    mode: str,
    bundle: dict,
    match_ids: tuple[str, str],
    identity_attestations: list[dict],
    lineage_attestations: list[dict],
    *,
    identity_trust_roots: Mapping[tuple[str, str], bytes],
    lineage_trust_roots: Mapping[tuple[str, str], bytes],
    baseline: dict,
    origin_label: str,
    signed_reduction: bool,
) -> dict:
    report = diagnose_citation_mde(
        bundle,
        match_ids,
        identity_attestations,
        lineage_attestations,
        identity_trust_roots=identity_trust_roots,
        lineage_trust_roots=lineage_trust_roots,
    )
    input_identity = _input_identity(
        bundle,
        match_ids,
        identity_attestations,
        lineage_attestations,
        baseline["host_trust_config_sha256"],
    )
    frozen_scope_preserved = (
        report["candidate_bundle_sha256"] == baseline["candidate_bundle_sha256"]
        and report["selected_match_ids"] == baseline["selected_match_ids"]
        and report["selected_candidate_identifiers"]
        == baseline["selected_candidate_identifiers"]
        and report["host_trust_config_sha256"] == baseline["host_trust_config_sha256"]
    )
    # Absence of a whole signed record predictably removes authentication. It
    # is useful to show the schema dependency, but is not evidence that the
    # missing information unit was necessary under a lawful signed reduction.
    authenticated = (
        report["identity_assertion_authenticated"]
        and report["lineage_assertion_authenticated"]
    )
    if not frozen_scope_preserved or (signed_reduction and not authenticated):
        classification = "INVALID_ABLATION"
    elif not signed_reduction:
        classification = "UNTESTED"
    elif report["status"] == TARGET_TRUE:
        classification = "REDUNDANT_IN_THIS_BUNDLE"
    elif report["status"] == UNRESOLVED and report["reason"] == "lineage_or_alias_coverage_incomplete":
        classification = "NECESSARY_IN_THIS_BUNDLE"
    else:
        classification = "INVALID_ABLATION"
    return {
        "unit_id": unit_id,
        "classification": classification,
        "ablation_mode": mode,
        "verification": (
            "SIGNED_EXACT_ONE_UNIT_REDUCTION"
            if signed_reduction and authenticated and frozen_scope_preserved
            else "SIMULATED_RECORD_OMISSION"
            if not signed_reduction and frozen_scope_preserved
            else "INVALID_OR_UNAUTHENTICATED_REDUCTION"
        ),
        "origin_label": origin_label,
        "origin_label_authenticated": False,
        "new_issuer_statement_authenticated": bool(signed_reduction and authenticated),
        "record_omission_only_shows_schema_dependency": not signed_reduction,
        "frozen_scope_preserved": frozen_scope_preserved,
        "input": input_identity,
        "diagnostic": _decision_view(report),
    }


def certify_citation_positive(
    bundle: dict,
    match_ids: tuple[str, str],
    identity_attestations: list[dict],
    lineage_attestation: dict,
    *,
    identity_trust_roots: Mapping[tuple[str, str], bytes],
    lineage_trust_roots: Mapping[tuple[str, str], bytes],
    reduced_lineage_by_unit: Mapping[str, dict] | None = None,
) -> dict:
    """Replay a positive finding and one-unit deletions under fixed trust roots.

    ``reduced_lineage_by_unit`` contains optional independently signed reduced
    lineage envelopes. Each item is ``{"attestation": envelope,
    "origin_label": "synthetic_test_resigning" | "external_submitted"}``.
    The label is caller-supplied provenance, never authenticated by this API.
    Omitting a reduction records UNTESTED; this function never signs evidence.
    """
    baseline = diagnose_citation_mde(
        bundle,
        match_ids,
        identity_attestations,
        [lineage_attestation],
        identity_trust_roots=identity_trust_roots,
        lineage_trust_roots=lineage_trust_roots,
    )
    if baseline["status"] != TARGET_TRUE:
        raise ValueError("Citation positive certificate requires a positive MDE replay")
    if (
        not baseline["identity_assertion_authenticated"]
        or not baseline["lineage_assertion_authenticated"]
        or not baseline["coverage_complete"]
        or any(
            not baseline["primary_roots_by_match_id"].get(match_id)
            for match_id in match_ids
        )
        or not set(baseline["primary_roots_by_match_id"][match_ids[0]]).isdisjoint(
            baseline["primary_roots_by_match_id"][match_ids[1]]
        )
    ):
        raise ValueError("Citation finite model is empty or does not decide the target")
    finite_model = _finite_model(baseline, match_ids, lineage_attestation["payload"])
    if reduced_lineage_by_unit is None:
        reduced_lineage_by_unit = {}
    if not isinstance(reduced_lineage_by_unit, Mapping):
        raise ValueError("Citation reduced attestations must be keyed by unit")
    unit_ids = _signed_unit_ids(lineage_attestation["payload"], match_ids)
    if set(reduced_lineage_by_unit) - set(unit_ids):
        raise ValueError("Unknown Citation reduced evidence unit")

    ablations = []
    for match_id in match_ids:
        retained = [
            envelope
            for envelope in identity_attestations
            if envelope["payload"]["match_id"] != match_id
        ]
        ablations.append(
            _one_ablation(
                f"identity_record:{match_id}",
                "record_omission_simulation",
                bundle,
                match_ids,
                retained,
                [lineage_attestation],
                identity_trust_roots=identity_trust_roots,
                lineage_trust_roots=lineage_trust_roots,
                baseline=baseline,
                origin_label="local_counterfactual_not_issuer_statement",
                signed_reduction=False,
            )
        )
    ablations.append(
        _one_ablation(
            "lineage_record",
            "record_omission_simulation",
            bundle,
            match_ids,
            identity_attestations,
            [],
            identity_trust_roots=identity_trust_roots,
            lineage_trust_roots=lineage_trust_roots,
            baseline=baseline,
            origin_label="local_counterfactual_not_issuer_statement",
            signed_reduction=False,
        )
    )
    for unit_id in unit_ids:
        supplied = reduced_lineage_by_unit.get(unit_id)
        if supplied is None:
            ablations.append(
                {
                    "unit_id": unit_id,
                    "classification": "UNTESTED",
                    "ablation_mode": "issuer_signed_reduction_not_supplied",
                    "verification": None,
                    "origin_label": None,
                    "origin_label_authenticated": False,
                    "new_issuer_statement_authenticated": False,
                    "frozen_scope_preserved": None,
                    "input": None,
                    "diagnostic": None,
                }
            )
            continue
        if (
            not isinstance(supplied, dict)
            or set(supplied) != {"attestation", "origin_label"}
            or type(supplied["origin_label"]) is not str
            or supplied["origin_label"] not in {
                "synthetic_test_resigning", "external_submitted"
            }
        ):
            raise ValueError("Reduced evidence must include signed envelope and provenance label")
        envelope = supplied["attestation"]
        if not isinstance(envelope, dict):
            raise ValueError("Reduced evidence envelope is not a JSON object")
        # Reject malformed/deep/cyclic JSON before Python's recursive equality
        # comparison; such input must never become a certificate finding.
        _bounded_hash(envelope)
        expected_payload = _reduced_payload(lineage_attestation["payload"], unit_id)
        # A signature over an arbitrary replacement or another issuer is not a
        # comparable one-unit deletion, even if a diagnostic could return TRUE.
        comparable = (
            set(envelope) == set(lineage_attestation)
            and envelope.get("contract_version") == lineage_attestation["contract_version"]
            and envelope.get("issuer_id") == lineage_attestation["issuer_id"]
            and envelope.get("key_id") == lineage_attestation["key_id"]
            and envelope.get("payload") == expected_payload
        )
        reduction = _one_ablation(
            unit_id,
            "issuer_signed_one_unit_reduction",
            bundle,
            match_ids,
            identity_attestations,
            [envelope],
            identity_trust_roots=identity_trust_roots,
            lineage_trust_roots=lineage_trust_roots,
            baseline=baseline,
            origin_label=supplied["origin_label"],
            signed_reduction=True,
        )
        if not comparable:
            reduction["classification"] = "INVALID_ABLATION"
            reduction["frozen_scope_preserved"] = False
            reduction["verification"] = "INVALID_OR_UNAUTHENTICATED_REDUCTION"
        ablations.append(reduction)

    necessary = [item["unit_id"] for item in ablations if item["classification"] ==
                 "NECESSARY_IN_THIS_BUNDLE"]
    redundant = [item["unit_id"] for item in ablations if item["classification"] ==
                 "REDUNDANT_IN_THIS_BUNDLE"]
    body = {
        "certificate_version": CERTIFICATE_VERSION,
        "certificate_kind": CERTIFICATE_KIND,
        "generator_source_sha256": CERTIFICATE_GENERATOR_SHA256,
        "case_id": CASE_ID,
        "authority_effect": "candidate_diagnostic_only",
        "target": "Selected bibliographic target candidates have distinct attested primary evidence roots under signed coverage assertions.",
        "baseline": {
            "input": _input_identity(
                bundle,
                match_ids,
                identity_attestations,
                [lineage_attestation],
                baseline["host_trust_config_sha256"],
            ),
            "diagnostic": baseline,
            "conditional_finite_model": finite_model,
            "claimed_source_record_hashes": {
                "pdf_sha256": bundle["source_snapshot"]["pdf_sha256"],
                "identity_metadata_record_sha256": baseline[
                    "identity_metadata_record_sha256"
                ],
                "lineage_source_record_sha256": baseline[
                    "lineage_source_record_sha256"
                ],
                "underlying_metadata_and_lineage_bytes_fetched": False,
            },
        },
        "ablations": ablations,
        "necessary_information_in_this_bundle": necessary,
        "redundant_information_in_this_bundle": redundant,
        "unverified_assumptions": [
            "Enrolled public keys identify accepted assertion sources only under the host's trust configuration; ownership, revocation history, and organizational independence are not independently certified here.",
            "Canonical work identity and referenced metadata record bytes are signer assertions; the bytes are not fetched or authenticated by this certificate.",
            "Primary-source lineage, used-source coverage, and cross-work alias-index completeness are signed assertions, not independently reconstructed publication or data histories.",
            "A supplied reduction's origin_label is caller-reported; a valid signature authenticates its content under an enrolled key, not that it was actually issued as part of a historical source record.",
            "Finite graph consistency under the frozen checker does not enumerate all possible real-world histories or establish statistical independence, claim support, or truth.",
            "One-unit ablations establish at most one-step irreducibility relative to this evidence bundle, not global minimality across evidence schemes.",
        ],
        "qualification_granted": False,
        "current_use_bound": False,
        "authorization_granted": False,
        "candidate_only": True,
    }
    return finalize_certificate(body)
