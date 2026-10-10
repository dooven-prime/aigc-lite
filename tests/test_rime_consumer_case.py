"""Finite RIME Case controls: report identity, command domains, and authority."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import database, main
from app.adapters.research_import.rime_consumer import RimeConsumerWitnessAdapter, _LogRoute
from app.core.contracts import RequestContext
from app.services.rime_consumer_case import RimeConsumerCaseService
from app.tenancy import Tenant


def witness(*, prefix: str = "dppd", commands: list[dict] | None = None) -> dict:
    adapter = RimeConsumerWitnessAdapter()
    return {
        "contract": adapter.identity(),
        "environment": {
            "q_size": 7, "p": "successor_mod_n",
            "d": [0, 3, 2, 5, 1, 4, 0],
            "omega": list(range(7)), "iota": list(range(7)),
            "ufe_enumeration": list(range(7)),
            "kernel_ports": [0, 6], "collision_image": 0,
        },
        "prefix": prefix, "selected_block": [0, 6],
        "commands": commands or [], "candidate_reports": None,
    }


def test_three_routes_preserve_registered_f_when_latest_changes() -> None:
    result = RimeConsumerWitnessAdapter().replay(witness())
    assert result["outcome"] == "passed"
    report = result["trace"][0]["report"]
    assert report["selected_block"] == [0, 6]
    assert report["origin"]["fresh_block"] == [0, 6]
    assert result["trace"][0]["forest_latest"] == [3, 5]
    assert sorted(item["block"] for item in report["partition"]) == [
        [0, 6], [1], [2], [3, 5], [4]
    ]
    assert report["carrier"] == {"q": 2, "block": [0, 6]}
    assert report["absorptions"] == []


def test_v2_contract_binds_entire_public_source_package() -> None:
    identity = RimeConsumerWitnessAdapter().identity()
    assert identity["contract_version"] == "aigc-lite.rime.consumer-replay.v2"
    assert identity["source_contract_version"] == "rime.consumer-replay.v1"
    assert identity["source_commit"] == "8dc2e615c5b011efa498bbd6fc4dec613c637030"
    assert identity["source_manifest_sha256"] == (
        "0db53c0235a2add4189df1e8fcb5aa63da1443bc7451ab865313e842583b0eca"
    )
    assert {item["path"] for item in identity["source_files"]} == {
        ".gitattributes", "README.md", "candidate-reports.schema.json",
        "CONSUMER_SPEC.md", "FOREST_MODEL.md", "UFE_ADAPTER.md",
        "examples/witness.json",
    }
    assert all(item["size"] > 0 and len(item["sha256"]) == 64 for item in identity["source_files"])


def test_carry_d_unrelated_fusion_updates_partition_and_ufe_not_absorptions() -> None:
    # After the registered F forms, two p moves place F away from both d ports.
    result = RimeConsumerWitnessAdapter().replay(witness(
        prefix="dpp", commands=[{"kind": "Carry", "letter": "d"}]
    ))
    assert result["outcome"] == "passed"
    before, after = (item["report"] for item in result["trace"])
    assert before["absorptions"] == after["absorptions"] == []
    assert len(after["partition"]) == len(before["partition"]) - 1
    assert len(result["trace"][1]["ufe_union_history"]) == len(result["trace"][0]["ufe_union_history"]) + 1


def test_registered_absorb_changes_only_its_own_sequence() -> None:
    result = RimeConsumerWitnessAdapter().replay(witness(
        prefix="dppdppppp", commands=[{"kind": "Absorb", "letter": "d"}]
    ))
    assert result["outcome"] == "passed"
    assert len(result["trace"][1]["report"]["absorptions"]) == 1


def test_short_continuations_agree_for_every_p_d_word() -> None:
    adapter = RimeConsumerWitnessAdapter()
    for letters in itertools.product("pd", repeat=5):
        value = witness(prefix="d")
        oracle = _LogRoute(value["environment"])
        oracle.push("d")
        assert oracle.register((0, 6))
        value["commands"] = []
        for letter in letters:
            value["commands"].append({"kind": oracle.guard(letter), "letter": letter})
            oracle.push(letter)
        assert adapter.replay(value)["outcome"] == "passed", letters


def test_hostile_witnesses_fail_closed() -> None:
    adapter = RimeConsumerWitnessAdapter()
    nonexistent = witness()
    nonexistent["selected_block"] = [0, 1]
    assert adapter.replay(nonexistent)["failure"]["code"] == "registration_domain"
    bad_command = witness(commands=[{"kind": "Absorb", "letter": "p"}])
    assert adapter.replay(bad_command)["failure"]["code"] == "command_domain"
    accepted = adapter.replay(witness())
    altered = witness()
    altered["candidate_reports"] = [copy.deepcopy(accepted["trace"][0]["report"])]
    altered["candidate_reports"][0]["carrier"]["q"] = 1
    assert adapter.replay(altered)["failure"]["code"] == "candidate_mismatch"


def test_report_shape_is_rejected_before_semantic_comparison() -> None:
    from app.core.errors import InvalidEvidenceError

    adapter = RimeConsumerWitnessAdapter()
    result = adapter.replay(witness())
    malformed = witness()
    malformed["candidate_reports"] = [copy.deepcopy(result["trace"][0]["report"])]
    malformed["candidate_reports"][0]["carrier"] = [0, 6]
    with pytest.raises(InvalidEvidenceError, match="structural"):
        adapter.replay(malformed)
    valid_shape_wrong_semantics = witness()
    valid_shape_wrong_semantics["candidate_reports"] = [copy.deepcopy(result["trace"][0]["report"])]
    valid_shape_wrong_semantics["candidate_reports"][0]["carrier"]["q"] = 1
    assert adapter.replay(valid_shape_wrong_semantics)["failure"]["code"] == "candidate_mismatch"
    unsorted_but_schema_valid = witness()
    unsorted_but_schema_valid["candidate_reports"] = [
        copy.deepcopy(result["trace"][0]["report"])
    ]
    unsorted_but_schema_valid["candidate_reports"][0]["carrier"]["block"] = [6, 0]
    assert adapter.replay(unsorted_but_schema_valid)["failure"]["code"] == "candidate_mismatch"


def test_swapped_ports_and_mixed_checker_identity_are_rejected() -> None:
    from app.core.errors import InvalidEvidenceError

    adapter = RimeConsumerWitnessAdapter()
    original = adapter.replay(witness())
    swapped = witness()
    swapped["environment"]["kernel_ports"] = [6, 0]
    swapped["candidate_reports"] = [copy.deepcopy(original["trace"][0]["report"])]
    assert adapter.replay(swapped)["failure"]["code"] == "candidate_mismatch"
    mixed = witness()
    mixed["contract"]["checker_sha256"] = "0" * 64
    with pytest.raises(InvalidEvidenceError):
        adapter.replay(mixed)
    mixed_manifest = witness()
    mixed_manifest["contract"]["source_files"][4]["sha256"] = "0" * 64
    with pytest.raises(InvalidEvidenceError):
        adapter.replay(mixed_manifest)
    forged = witness()
    forged["claimed_verdict"] = "PASSED"
    with pytest.raises(InvalidEvidenceError):
        adapter.replay(forged)


def test_legacy_case_link_does_not_repair_unmaterialized_source() -> None:
    old_witness = witness()
    old_witness["contract"] = {
        "contract_version": "rime.consumer-replay.v1",
        "source_commit": "c211517c8f170b682db9d45faeb13e9837b73b0b",
    }
    old_text = json.dumps(old_witness)
    old_hash = hashlib.sha256(old_text.encode()).hexdigest()
    old_contract_text = json.dumps(old_witness["contract"])
    old_contract_hash = hashlib.sha256(old_contract_text.encode()).hexdigest()

    class PriorRepository:
        def get_research_case(self, _workspace, _case):
            return {"profile": "rime.event-anchored-consumer.case.v1",
                    "source_artifact_id": "old-source", "metadata": {"contract_sha256": old_contract_hash}}

        def list_research_claims(self, _workspace, *, research_case_id):
            return [{"claim_key": "finite-consumer-replay", "id": "old-claim",
                     "sources": [{"source_key": "witness", "locator": "artifact:old-witness",
                                  "content_hash": old_hash}]}]

        def get_artifact(self, _workspace, artifact_id):
            return {
                "old-witness": {"content_text": old_text, "content_hash": old_hash},
                "old-source": {"content_text": old_contract_text,
                               "content_hash": old_contract_hash},
            }[artifact_id]

        def list_research_verification_attempts(self, _workspace, _claim):
            return [{"id": "old-attempt"}]

    new_witness = witness()
    new_witness["prior_case_id"] = "old-case"
    link = RimeConsumerCaseService()._prior_link(
        RequestContext("request", "workspace-a"), new_witness, PriorRepository()
    )
    assert link["attempt_ids"] == ["old-attempt"]
    assert link["source_closure"] == "unmaterialized_at_declared_commit"


def test_http_case_records_attempt_without_qualification_or_binding(tmp_path, monkeypatch) -> None:
    def connect():
        db = sqlite3.connect(tmp_path / "rime-case.db", timeout=10)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(database, "_connect", connect)
    user = {"id": "admin-a", "tenant_id": "workspace-a", "email": "a@example.test",
            "name": "Admin", "role": "admin"}
    main.app.dependency_overrides[main.current_admin_user] = lambda: user
    main.app.dependency_overrides[main.current_tenant] = lambda: Tenant(
        "workspace-a", "Workspace A"
    )
    try:
        with TestClient(main.app) as client:
            assert client.get("/api/research/rime-consumer/contract").json()[
                "checker_sha256"
            ] == RimeConsumerWitnessAdapter().contract()["checker_sha256"]
            created = client.post("/api/research/rime-consumer/witnesses", json=witness())
            assert created.status_code == 201, created.text
            value = created.json()
            assert value["result"]["outcome"] == "passed"
            assert value["qualification_granted"] is False
            assert value["knowledge_admitted"] is False
            explorer = client.get(
                "/api/research/explorer", params={"research_case_id": value["case_id"]}
            )
            assert explorer.status_code == 200
            assert explorer.json()["claims"][0]["id"] == value["claim_revision_id"]
            qualified = client.get(
                "/api/qualification/search",
                params={"q": value["witness_sha256"], "profile": "math.formal.v1"},
            )
            assert qualified.status_code == 200
            assert qualified.json() == []
            repo = database.get_repository()
            assert repo.get_artifact("workspace-a", value["witness_artifact_id"])["content_hash"] == value["witness_sha256"]
            result_artifact = repo.get_artifact("workspace-a", value["result_artifact_id"])
            assert result_artifact["content_text"]
            attempt = repo.list_research_verification_attempts(
                "workspace-a", value["claim_revision_id"]
            )[0]
            assert attempt["output_digest"] == result_artifact["content_hash"]
            assert attempt["independent"] is False
            claim = repo.get_research_claim("workspace-a", value["claim_revision_id"])
            assert claim["closure_status"] == "blocked"
            assert len(claim["semantic_hash"]) == 64
            source_refs = {item["source_key"]: item for item in claim["sources"]}
            assert set(source_refs) == {
                "witness", "rime-contract-manifest", *(
                    f"rime-contract-{item['path']}"
                    for item in RimeConsumerWitnessAdapter().identity()["source_files"]
                ),
            }
            assert source_refs["rime-contract-FOREST_MODEL.md"]["locator"].startswith(
                "https://github.com/dooven-prime/rime-lite/blob/8dc2e615"
            )
            assert source_refs["rime-contract-manifest"]["content_hash"] == (
                RimeConsumerWitnessAdapter().identity()["source_manifest_sha256"]
            )
            for item in RimeConsumerWitnessAdapter().identity()["source_files"]:
                assert source_refs[f"rime-contract-{item['path']}"]["content_hash"] == (
                    item["sha256"]
                )
            run = repo.get_run("workspace-a", value["run_id"])
            assert run["status"] == "succeeded"
            assert len(run["steps"]) == len(witness()["prefix"]) + 1
            with connect() as db:
                assert db.execute("SELECT COUNT(*) FROM qualification_receipts").fetchone()[0] == 0
                assert db.execute("SELECT COUNT(*) FROM current_use_bindings").fetchone()[0] == 0
            replay = witness()
            replay["prior_case_id"] = value["case_id"]
            linked = client.post("/api/research/rime-consumer/witnesses", json=replay)
            assert linked.status_code == 201, linked.text
            linked_value = linked.json()
            assert linked_value["case_id"] != value["case_id"]
            assert linked_value["verification_attempt_id"] != value["verification_attempt_id"]
            assert linked_value["witness_sha256"] != value["witness_sha256"]
            assert linked_value["mathematical_witness_sha256"] == value["mathematical_witness_sha256"]
            assert value["verification_attempt_id"] in linked_value["prior_link"]["attempt_ids"]
            wrong_prior = witness(prefix="d")
            wrong_prior["prior_case_id"] = value["case_id"]
            assert client.post("/api/research/rime-consumer/witnesses", json=wrong_prior).status_code == 422
            rejected = witness(commands=[{"kind": "Absorb", "letter": "p"}])
            failed = client.post("/api/research/rime-consumer/witnesses", json=rejected)
            assert failed.status_code == 201, failed.text
            assert failed.json()["result"]["failure"]["code"] == "command_domain"
            with connect() as db:
                assert db.execute("SELECT COUNT(*) FROM qualification_receipts").fetchone()[0] == 0
                assert db.execute("SELECT COUNT(*) FROM current_use_bindings").fetchone()[0] == 0
    finally:
        main.app.dependency_overrides.clear()
