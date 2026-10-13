"""The same authentic L1 records can leave biological origin unidentified."""

from __future__ import annotations

from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_bio_in_vitro_case import witness
from test_bio_observation_provenance import (
    _independence_candidate,
    _key,
    _provenance_case,
    _signed_records,
)

from app.core.contracts import RequestContext
from app.core.errors import InvalidEvidenceError
from app.core.qualification import canonical_hash
from app.profiles.bio_information_sufficiency import (
    analyze_sufficiency,
    disclosed_lineage_control,
    observation_equivalence_invariant,
)
from app.profiles.bio_observation_provenance import (
    PROFILE_ID,
    BioObservationProvenanceVerifier,
)
from app.repository import SQLiteRepository
from app.services.bio_in_vitro_case import BioInVitroCaseService
from app.services.qualification import DomainVerifierRegistry, QualificationService


def test_two_latent_worlds_share_admitted_record_bytes_but_not_target(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "sufficiency.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    context = RequestContext("bio-sufficiency", "workspace", "research-admin")
    submitted = witness()
    arithmetic = BioInVitroCaseService(provider).submit(context, submitted)
    assert arithmetic["evaluation"]["verdict"] == "ADMITTED"

    instrument_key = Ed25519PrivateKey.generate()
    custodian_key = Ed25519PrivateKey.generate()
    registry = DomainVerifierRegistry()
    registry.register(
        BioObservationProvenanceVerifier(
            (
                _key("instrument", "device-registry-1", instrument_key),
                _key("custodian", "sample-office-1", custodian_key),
            )
        )
    )
    gate = QualificationService(provider, registry=registry)
    records = _signed_records(
        submitted,
        instrument_key,
        custodian_key,
        [f"Sample-{index + 1}" for index in range(len(submitted["pairs"]))],
    )
    provenance_claim = _provenance_case(repository, context, arithmetic, records, pass_attempt=True)
    l1 = gate.evaluate(context, provenance_claim["id"], PROFILE_ID)
    assert l1["evaluation"]["verdict"] == "ADMITTED"
    assert l1["qualification_receipt"] is not None

    report = analyze_sufficiency(records[0], records[1])
    assert report["observation_sha256"] == canonical_hash(
        {"instrument": records[0], "custodian": records[1]}
    )
    assert report["parent_source_distinctness"] == "UNRESOLVED"
    assert report["observation_equivalence_invariant"] is True
    assert "biological_independence" not in report
    assert report["record_binding"] == "CHECK_SEPARATELY_WITH_L1_GATE"
    assert {world["target_holds"] for world in report["counterworlds"]} == {True, False}
    assert {world["projected_samples_sha256"] for world in report["counterworlds"]} == {
        report["projected_samples_sha256"]
    }
    assert {world["projected_observation_sha256"] for world in report["counterworlds"]} == {
        report["observation_sha256"]
    }
    assert analyze_sufficiency(deepcopy(records[0]), deepcopy(records[1])) == report

    broader = _independence_candidate(
        repository,
        context,
        [provenance_claim],
        "These readings originate from distinct biological parent sources.",
    )
    # The L1 Gate cannot be repurposed as an L2 independence decision.
    l2_attempt = gate.evaluate(context, broader["id"], PROFILE_ID)
    assert l2_attempt["evaluation"]["verdict"] == "NOT_APPLICABLE"
    assert l2_attempt["qualification_receipt"] is None
    assert repository.list_qualification_receipts(context.workspace_id, broader["id"]) == []
    assert repository.list_current_use_bindings(context.workspace_id, PROFILE_ID) == []
    assert repository.list_authorization_grants(context.workspace_id) == []


def _counterworlds() -> tuple[dict, dict, dict]:
    submitted = witness()
    instrument, custodian, _ = _signed_records(
        submitted,
        Ed25519PrivateKey.generate(),
        Ed25519PrivateKey.generate(),
        [f"Sample-{index + 1}" for index in range(len(submitted["pairs"]))],
    )
    visible = {"instrument": instrument, "custodian": custodian}
    sample_ids = [item["sample_id"] for item in custodian["payload"]["samples"]]
    distinct = {
        "observation": deepcopy(visible),
        "latent_parent_sources": {
            sample_id: f"culture-{index + 1}"
            for index, sample_id in enumerate(sample_ids)
        },
    }
    shared = deepcopy(distinct)
    shared["latent_parent_sources"][sample_ids[1]] = distinct["latent_parent_sources"][
        sample_ids[0]
    ]
    return visible, distinct, shared


def test_hidden_parent_change_preserves_complete_bio_observation() -> None:
    visible, distinct, shared = _counterworlds()
    assert distinct["latent_parent_sources"] != shared["latent_parent_sources"]
    assert distinct["observation"] == shared["observation"] == visible
    for world in (distinct, shared):
        observation = world["observation"]
        assert observation["instrument"] == visible["instrument"]
        assert observation["custodian"] == visible["custodian"]
        assert observation["instrument"]["payload"]["study"] == visible["instrument"][
            "payload"
        ]["study"]
        assert observation["instrument"]["payload"]["pairs"] == visible["instrument"][
            "payload"
        ]["pairs"]
        assert observation["custodian"]["payload"]["samples"] == visible["custodian"][
            "payload"
        ]["samples"]
    assert observation_equivalence_invariant(visible, distinct, shared)


@pytest.mark.parametrize("world_to_change", ["distinct", "shared"])
@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("instrument", "issuer_id"), "different-instrument"),
        (("instrument", "signature"), "different-signature"),
        (("instrument", "payload", "study", "timepoint"), "later"),
        (("instrument", "payload", "pairs", 0, "batch_id"), "other-batch"),
        (("instrument", "payload", "pairs", 0, "control"), "9.9"),
        (("custodian", "payload", "instrument_sha256"), "different-source"),
        (("custodian", "payload", "samples", 0, "pair_id"), "other-pair"),
        (("custodian", "payload", "samples", 0, "sample_id"), "other-sample"),
        (("custodian", "payload", "samples", 0, "collected_at"), "later"),
        (("custodian", "payload", "samples", 0, "assayed_at"), "later"),
    ],
)
def test_visible_bio_field_change_breaks_equivalence(
    world_to_change: str, path: tuple[str | int, ...], value: str
) -> None:
    visible, distinct, shared = _counterworlds()
    world = distinct if world_to_change == "distinct" else shared
    field = world["observation"]
    for key in path[:-1]:
        field = field[key]
    field[path[-1]] = value
    assert not observation_equivalence_invariant(visible, distinct, shared)


def test_bio_world_constraints_are_checked_separately() -> None:
    visible, distinct, shared = _counterworlds()
    sample_id = visible["custodian"]["payload"]["samples"][0]["sample_id"]
    del distinct["latent_parent_sources"][sample_id]
    assert not observation_equivalence_invariant(visible, distinct, shared)

    visible, distinct, shared = _counterworlds()
    shared["observation"]["custodian"]["payload"]["study_id"] = "another-study"
    assert not observation_equivalence_invariant(visible, distinct, shared)


def test_explicit_same_parent_control_is_refuted_not_unknown() -> None:
    submitted = witness()
    instrument_key = Ed25519PrivateKey.generate()
    custodian_key = Ed25519PrivateKey.generate()
    instrument, custodian, _ = _signed_records(
        submitted,
        instrument_key,
        custodian_key,
        [f"Sample-{index + 1}" for index in range(len(submitted["pairs"]))],
    )
    ids = [item["sample_id"] for item in custodian["payload"]["samples"]]
    unknown = analyze_sufficiency(instrument, custodian)
    same_source = disclosed_lineage_control(
        instrument, custodian, {sample_id: "Culture-001" for sample_id in ids}
    )
    assert unknown["parent_source_distinctness"] == "UNRESOLVED"
    assert same_source["status"] == "REFUTED_IN_DECLARED_CONTROL"
    assert same_source["lineage_authenticated"] is False
    assert same_source["qualification_granted"] is False
    distinct_sources = disclosed_lineage_control(
        instrument,
        custodian,
        {sample_id: f"Culture-{index}" for index, sample_id in enumerate(ids)},
    )
    assert distinct_sources["status"] == "NOT_REFUTED_BY_CONTROL"
    assert distinct_sources["qualification_granted"] is False


def test_counterworld_case_rejects_unobserved_field_smuggling() -> None:
    submitted = witness()
    instrument_key = Ed25519PrivateKey.generate()
    custodian_key = Ed25519PrivateKey.generate()
    instrument, custodian, _ = _signed_records(
        submitted,
        instrument_key,
        custodian_key,
        [f"Sample-{index + 1}" for index in range(len(submitted["pairs"]))],
    )
    custodian["payload"]["samples"][0]["parent_source_id"] = "Culture-001"
    with pytest.raises(InvalidEvidenceError, match="exact L1 fields"):
        analyze_sufficiency(instrument, custodian)


def test_counterworld_case_requires_frozen_l1_contract_versions() -> None:
    submitted = witness()
    instrument, custodian, _ = _signed_records(
        submitted,
        Ed25519PrivateKey.generate(),
        Ed25519PrivateKey.generate(),
        [f"Sample-{index + 1}" for index in range(len(submitted["pairs"]))],
    )
    instrument["contract_version"] = "bio.future-record.v9"
    with pytest.raises(InvalidEvidenceError, match="L1 contract versions"):
        analyze_sufficiency(instrument, custodian)

    instrument["contract_version"] = "bio.instrument-record.v0"
    custodian["contract_version"] = "bio.future-record.v9"
    with pytest.raises(InvalidEvidenceError, match="L1 contract versions"):
        analyze_sufficiency(instrument, custodian)


def test_counterworld_case_rejects_payload_level_parent_lineage() -> None:
    submitted = witness()
    instrument_key = Ed25519PrivateKey.generate()
    custodian_key = Ed25519PrivateKey.generate()
    instrument, custodian, _ = _signed_records(
        submitted,
        instrument_key,
        custodian_key,
        [f"Sample-{index + 1}" for index in range(len(submitted["pairs"]))],
    )
    custodian["payload"]["parent_lineage"] = {
        item["sample_id"]: f"Culture-{index + 1}"
        for index, item in enumerate(custodian["payload"]["samples"])
    }
    with pytest.raises(InvalidEvidenceError, match="Outside the frozen L1 payload shape"):
        analyze_sufficiency(instrument, custodian)

    del custodian["payload"]["parent_lineage"]
    instrument["payload"]["parent_lineage"] = {"Sample-1": "Culture-1"}
    with pytest.raises(InvalidEvidenceError, match="Outside the frozen L1 payload shape"):
        analyze_sufficiency(instrument, custodian)


@pytest.mark.parametrize(
    ("location", "expected_error"),
    [
        ("instrument_envelope", "L1 envelope shape"),
        ("custodian_envelope", "L1 envelope shape"),
        ("study", "L1 study shape"),
        ("pair", "L1 pair shape"),
    ],
)
def test_counterworld_case_rejects_lineage_outside_l1_shape(
    location: str, expected_error: str
) -> None:
    submitted = witness()
    instrument, custodian, _ = _signed_records(
        submitted,
        Ed25519PrivateKey.generate(),
        Ed25519PrivateKey.generate(),
        [f"Sample-{index + 1}" for index in range(len(submitted["pairs"]))],
    )
    if location == "instrument_envelope":
        instrument["parent_lineage"] = {"Sample-1": "Culture-1"}
    elif location == "custodian_envelope":
        custodian["parent_lineage"] = {"Sample-1": "Culture-1"}
    elif location == "study":
        instrument["payload"]["study"]["parent_lineage"] = {"Sample-1": "Culture-1"}
    else:
        instrument["payload"]["pairs"][0]["parent_lineage"] = "Culture-1"
    with pytest.raises(InvalidEvidenceError, match=expected_error):
        analyze_sufficiency(instrument, custodian)
