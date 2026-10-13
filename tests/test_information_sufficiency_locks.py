"""Lock the exact inputs and limited contracts of two independent counterworld Cases."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_bio_in_vitro_case import witness
from test_bio_observation_provenance import _signed_records
from test_citation_extraction import CONTEXT, URI, _pdf, _setup

from app.core.qualification import canonical_hash
from app.profiles import bio_information_sufficiency as bio_case
from app.profiles import citation_information_sufficiency as citation_case

ROOT = Path(__file__).resolve().parents[1]
LOCKS = ROOT / "contracts" / "information-sufficiency"
CASE_IDS = {
    "bio": "bio.biological-source-sufficiency.v1",
    "citation": "citation.primary-evidence-root-sufficiency.v1",
}
FILE_PATHS = {
    "bio": {
        "checker_source": "app/profiles/bio_information_sufficiency.py",
        "record_binding_profile_source": "app/profiles/bio_observation_provenance.py",
        "regression_test_source": "tests/test_bio_information_sufficiency.py",
        "contract_doc": "docs/BIO_INFORMATION_SUFFICIENCY_CASE.md",
        "input_fixture": "contracts/information-sufficiency/bio/fixture.json",
    },
    "citation": {
        "checker_source": "app/profiles/citation_information_sufficiency.py",
        "regression_test_source": "tests/test_citation_information_sufficiency.py",
        "candidate_bundle_service_source": "app/services/citation_extraction.py",
        "contract_doc": "docs/CITATION_INFORMATION_SUFFICIENCY_CASE.md",
        "input_fixture": "contracts/information-sufficiency/citation/fixture.json",
    },
}
HELPER_PATHS = {
    "bio": [
        "tests/test_bio_in_vitro_case.py",
        "tests/test_bio_observation_provenance.py",
    ],
    "citation": ["tests/test_citation_extraction.py"],
}
SEMANTIC_COMPONENTS = {
    "observation_projection",
    "latent_world_constraints",
    "target_property",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _lock(domain: str) -> dict:
    return _load_json(LOCKS / domain / "case-lock.json")


def _fixture(domain: str) -> dict:
    return _load_json(LOCKS / domain / "fixture.json")


def _check_file(entry: dict, expected_path: str) -> None:
    assert entry["path"] == expected_path
    digest = entry["sha256"]
    assert len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)
    assert hashlib.sha256((ROOT / expected_path).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("domain", ["bio", "citation"])
def test_case_lock_pins_sources_fixture_and_semantic_contract(domain: str) -> None:
    lock = _lock(domain)
    assert lock["schema"] == "information-sufficiency-case-lock.v1"
    assert lock["case_id"] == CASE_IDS[domain]
    assert lock["authority"] == "candidate_only"
    assert set(lock["files"]) == set(FILE_PATHS[domain]) | {"fixture_helper_sources"}
    for role, path in FILE_PATHS[domain].items():
        _check_file(lock["files"][role], path)
    helpers = lock["files"]["fixture_helper_sources"]
    assert len(helpers) == len(HELPER_PATHS[domain])
    for entry, path in zip(helpers, HELPER_PATHS[domain], strict=True):
        _check_file(entry, path)

    assert set(lock) == {
        "schema",
        "case_id",
        "authority",
        "hash_rules",
        "files",
        *SEMANTIC_COMPONENTS,
    }
    assert "self_reference" in lock["hash_rules"]
    assert all(
        entry["path"] not in {
            f"contracts/information-sufficiency/{domain}/case-lock.json",
            "tests/test_information_sufficiency_locks.py",
        }
        for entry in [*lock["files"].values(), *helpers]
        if isinstance(entry, dict)
    )
    for component in SEMANTIC_COMPONENTS:
        item = lock[component]
        assert set(item) == {"value", "sha256"}
        assert canonical_hash(item["value"]) == item["sha256"]
    fixture = _fixture(domain)
    assert fixture["schema"] == "information-sufficiency-input-fixture.v1"
    assert fixture["case_id"] == CASE_IDS[domain]


def test_bio_lock_replays_unsigned_fixture_with_ephemeral_signatures() -> None:
    lock = _lock("bio")
    fixture = _fixture("bio")
    submitted = witness()
    assert fixture["kind"] == "unsigned_l1_record_template"
    assert fixture["instrument"]["payload"] == {
        "study": submitted["study"],
        "pairs": submitted["pairs"],
    }
    sample_ids = [item["sample_id"] for item in fixture["custodian"]["payload"]["samples"]]
    instrument, custodian, _ = _signed_records(
        submitted,
        Ed25519PrivateKey.generate(),
        Ed25519PrivateKey.generate(),
        sample_ids,
    )
    for name, signed in (("instrument", instrument), ("custodian", custodian)):
        unsigned = {key: value for key, value in signed.items() if key != "signature"}
        assert unsigned == fixture[name]
        assert isinstance(signed["signature"], str) and signed["signature"]

    visible = {"instrument": instrument, "custodian": custodian}
    distinct = {
        "observation": deepcopy(visible),
        "latent_parent_sources": {
            sample_id: f"culture-{index + 1}" for index, sample_id in enumerate(sample_ids)
        },
    }
    shared = {
        "observation": deepcopy(visible),
        "latent_parent_sources": {sample_id: "culture-1" for sample_id in sample_ids},
    }
    assert bio_case.observation_equivalence_invariant(visible, distinct, shared)
    report = bio_case.analyze_sufficiency(instrument, custodian)
    assert report["checker_source_sha256"] == lock["files"]["checker_source"]["sha256"]
    assert report["target"] == lock["target_property"]["value"]["statement"]
    assert report["observation_sha256"] == canonical_hash(visible)
    assert {world["target_holds"] for world in report["counterworlds"]} == {True, False}
    assert report["parent_source_distinctness"] == "UNRESOLVED"
    assert report["record_binding"] == "CHECK_SEPARATELY_WITH_L1_GATE"


def test_citation_lock_replays_pinned_candidate_pdf(tmp_path: Path) -> None:
    lock = _lock("citation")
    fixture = _fixture("citation")
    assert fixture["kind"] == "generated_pdf_candidate_bundle"
    assert fixture["pdf_generator"] == "tests/test_citation_extraction.py::_pdf"
    assert fixture["source_uri"] == URI
    pdf = _pdf(fixture["pdf_lines"])
    assert hashlib.sha256(pdf).hexdigest() == fixture["pdf_sha256"]
    assert fixture["pdf_sha256"] == citation_case.FIXTURE_PDF_SHA256

    _, _, service = _setup(tmp_path)
    bundle, _ = service._prepare(CONTEXT, pdf, URI, None)
    assert bundle["admission_state"] == "candidate_only"
    assert bundle["checker_source_sha256"] == lock["files"][
        "candidate_bundle_service_source"
    ]["sha256"]
    assert bundle["source_snapshot"]["pdf_sha256"] == fixture["pdf_sha256"]
    assert bundle["source_snapshot"]["pages"][0]["text_sha256"] == fixture[
        "page_text_sha256"
    ]
    assert fixture["page_text_sha256"] == citation_case.FIXTURE_PAGE_TEXT_SHA256
    matches = bundle["proposal"]["bibliographic_matches"]
    assert [match["target_identifier"] for match in matches] == fixture[
        "bibliographic_target_candidates"
    ]
    match_ids = tuple(match["id"] for match in matches)
    report = citation_case.analyze_sufficiency(bundle, match_ids)
    assert report["checker_source_sha256"] == lock["files"]["checker_source"]["sha256"]
    assert report["target"] == lock["target_property"]["value"]["statement"]
    assert report["observation_sha256"] == canonical_hash(bundle)
    assert report["selected_target_identifiers"] == fixture["bibliographic_target_candidates"]
    assert {world["target_holds"] for world in report["counterworlds"]} == {True, False}
    assert report["primary_evidence_root_disjointness"] == "UNRESOLVED"
