"""The Run ledger projection preserves provenance without conferring authority."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.contracts import RequestContext
from app.core.errors import ResourceNotFoundError
from app.repository import PostgresRepository, SQLiteRepository
from app.services.ledger_projection import LedgerProjectionService, project_run_snapshot


def _repository(tmp_path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "ledger-projection.db")
    repository.init()
    repository.create_tenant("Projection owner", "projection-owner")
    repository.create_tenant("Other workspace", "projection-other")
    return repository


def _run(repository: SQLiteRepository, workspace_id: str = "projection-owner", **kwargs) -> dict:
    session = repository.create_session(workspace_id, "projection")
    return repository.create_run(
        workspace_id, session["id"], str(uuid4()), None, "test-model", **kwargs
    )


def _context(workspace_id: str = "projection-owner") -> RequestContext:
    return RequestContext(request_id="projection-read", workspace_id=workspace_id)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def _insert_row(repository: SQLiteRepository, table: str, values: dict) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join(f":{key}" for key in values)
    with repository._connect() as db:
        db.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", values)


def _table_counts(repository: SQLiteRepository) -> dict[str, int]:
    with repository._connect() as db:
        tables = [
            row["name"] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }


def _authority_rows(repository: SQLiteRepository) -> dict[str, list[dict]]:
    tables = (
        "qualification_receipts", "knowledge_admission_receipts", "current_use_bindings",
        "authorization_grants", "authorization_invocations", "enforcement_dispatches",
        "research_verification_attempts",
    )
    with repository._connect() as db:
        return {
            table: [dict(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY id')]
            for table in tables
        }


def _begin_usage(repository: SQLiteRepository, run: dict, sequence: int) -> dict:
    return repository.begin_model_usage({
        "attempt_id": str(uuid4()), "model_call_id": str(uuid4()),
        "step_id": str(uuid4()), "tenant_id": run["tenant_id"],
        "principal_id": None, "api_key_id": None,
        "request_id": run["request_id"], "run_id": run["id"],
        "sequence": sequence, "iteration": sequence,
        "model": "test-model", "provider_config_id": None,
        "pricing_snapshot": {"version": 1}, "input_content": "prompt",
        "step_metadata": {"candidate_verdict": "ADMITTED"},
    })


def test_projection_hashes_real_rows_and_does_not_promote_metadata_or_write_authority(
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    run = _run(repository)
    step = repository.append_run_step(
        "projection-owner", run["id"], 1, "tool", "candidate", "succeeded", "input",
        "output", {"claimed_fact": "verified-from-step", "claimed_goal": "bound"},
    )
    receipt_id = str(uuid4())
    _insert_row(repository, "execution_receipts", {
        "id": receipt_id, "tenant_id": "projection-owner",
        "protocol_id": str(uuid4()), "run_id": run["id"],
        "status": "succeeded", "input_digest": "a" * 64,
        "output_digest": "b" * 64, "runtime": "{}", "budget": "{}",
        "artifact_ids": "[]",
        "metadata": '{"claimed_fact":"verified-from-receipt","claimed_verdict":"ADMITTED"}',
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    claim_revision_id = str(uuid4())
    _insert_row(repository, "research_verification_attempts", {
        "id": str(uuid4()), "tenant_id": "projection-owner",
        "research_case_id": str(uuid4()), "claim_revision_id": claim_revision_id,
        "receipt_id": receipt_id, "run_id": run["id"],
        "kind": "review", "outcome": "inconclusive", "method": "test",
        "scope": "candidate", "input_digest": "c" * 64,
        "output_digest": "d" * 64, "created_at": "2026-01-01T00:00:01+00:00",
    })
    qualification_id = str(uuid4())
    _insert_row(repository, "qualification_receipts", {
        "id": qualification_id, "tenant_id": "projection-owner",
        "evaluation_id": str(uuid4()), "claim_revision_id": claim_revision_id,
        "claim_semantic_hash": "b" * 64,
        "profile_id": "example.profile.v1", "profile_version": 1,
        "evidence_closure_hash": "e" * 64,
        "policy_version": "test.v1", "policy_hash": "f" * 64,
        "verdict": "ADMITTED", "criteria": "[]", "blockers": "[]",
        "evidence_vector": "{}", "independence_summary": "{}",
        "receipt_hash": "a" * 64,
        "issued_at": "2026-01-01T00:00:01+00:00",
    })
    admission_id = str(uuid4())
    _insert_row(repository, "knowledge_admission_receipts", {
        "id": admission_id, "tenant_id": "projection-owner",
        "contract_version": "test.v1", "qualification_receipt_id": qualification_id,
        "qualification_receipt_hash": "a" * 64,
        "claim_revision_id": claim_revision_id, "claim_semantic_hash": "b" * 64,
        "profile_id": "example.profile.v1", "profile_version": 1,
        "use_scope": "knowledge", "admission_policy_id": "test.v1",
        "admission_policy_version": 1, "admission_policy_hash": "c" * 64,
        "approved_by": "test-admin", "rationale": "historical admission",
        "receipt_hash": "d" * 64,
        "issued_at": "2026-01-01T00:00:01+00:00",
    })
    _insert_row(repository, "current_use_bindings", {
        "id": str(uuid4()), "tenant_id": "projection-owner",
        "claim_revision_id": claim_revision_id, "profile_id": "example.profile.v1",
        "use_scope": "knowledge", "qualification_receipt_id": qualification_id,
        "knowledge_admission_receipt_id": admission_id,
        "state": "current", "created_at": "2026-01-01T00:00:02+00:00",
        "updated_at": "2026-01-01T00:00:02+00:00",
    })
    decision_id = str(uuid4())
    _insert_row(repository, "invalidation_decisions", {
        "id": decision_id, "tenant_id": "projection-owner",
        "notice_verification_id": str(uuid4()),
        "target_claim_revision_id": claim_revision_id,
        "target_claim_semantic_hash": "b" * 64,
        "target_receipt_id": qualification_id, "target_attempt_id": None,
        "target_scope": "receipt", "reason_code": "RECEIPT_DEFECT",
        "evidence_artifact_ids": "[]", "evidence_artifact_hashes": "[]",
        "policy_id": "invalidation.pinned-source-admin.v1", "policy_hash": "c" * 64,
        "rationale": "Receipt-scoped historical decision", "decided_by": "test-admin",
        "effect_state": "stale", "affected_binding_ids": "[]",
        "decision_hash": "d" * 64,
        "decided_at": "2026-01-01T00:00:02+00:00",
    })
    grant_id = str(uuid4())
    _insert_row(repository, "authorization_grants", {
        "id": grant_id, "tenant_id": "projection-owner",
        "qualification_receipt_id": qualification_id,
        "actor_id": "test-principal", "action": "test", "target": "target",
        "scope": "{}", "conditions": "{}", "budget": "{}",
        "max_calls": 1, "calls_used": 1, "policy_version": "test.v1",
        "state": "active", "grant_receipt": "g" * 64,
        "created_at": "2026-01-01T00:00:03+00:00",
    })
    invocation_id = str(uuid4())
    _insert_row(repository, "authorization_invocations", {
        "id": invocation_id, "tenant_id": "projection-owner",
        "grant_id": grant_id, "grant_call_number": 1,
        "actor_id": "test-principal", "request_id": run["request_id"],
        "run_id": run["id"], "action": "test", "target": "target",
        "provider_id": "test-provider", "native_name": "test-native",
        "public_name": "test-public", "source": "local",
        "binding_id": str(uuid4()), "binding_hash": "a" * 64,
        "adapter_id": "test-adapter", "proposal_id": "test-proposal",
        "policy_hash": "b" * 64, "adapter_target_type": "tool_provider",
        "adapter_target_id": "target", "tool_spec_hash": "c" * 64,
        "arguments_digest": "d" * 64, "invocation_digest": "e" * 64,
        "consumed_at": "2026-01-01T00:00:03+00:00",
        "claim_expires_at": "2026-01-01T00:01:00+00:00",
    })
    snapshot = repository.read_run_ledger_snapshot("projection-owner", run["id"])
    assert snapshot is not None
    assert "verified-from-step" in snapshot["steps"][0]["metadata"]
    assert "verified-from-receipt" in snapshot["execution_receipts"][0]["metadata"]

    counts_before = _table_counts(repository)
    authority_before = _authority_rows(repository)
    service = LedgerProjectionService(repository_provider=lambda: repository)
    projected = service.export_run(_context(), run["id"])
    second_reader = SQLiteRepository(repository.database_path)
    assert LedgerProjectionService(
        repository_provider=lambda: second_reader
    ).export_run(_context(), run["id"]) == projected
    assert _table_counts(repository) == counts_before
    assert _authority_rows(repository) == authority_before

    assert projected["contract"] == "HostLedgerProjection.v0"
    assert projected["candidate_only"] is True
    assert projected["source"]["kind"] == "repository_snapshot"
    assert projected["source"]["rows"]["agent_runs"] == [
        {"id": run["id"], "sha256": _digest(snapshot["run"])}
    ]
    assert projected["source"]["rows"]["steps"] == [
        {"id": step["id"], "sha256": _digest(snapshot["steps"][0])}
    ]
    assert projected["source"]["rows"]["execution_receipts"] == [
        {"id": receipt_id, "sha256": _digest(snapshot["execution_receipts"][0])}
    ]
    assert projected["source"]["rows"]["authorization_grants"] == [
        {"id": grant_id, "sha256": _digest(snapshot["authorization_grants"][0])}
    ]
    assert projected["source"]["rows"]["knowledge_admission_receipts"] == [
        {"id": admission_id, "sha256": _digest(snapshot["knowledge_admission_receipts"][0])}
    ]
    assert projected["source"]["sha256"] == _digest({
        "workspace_id": "projection-owner", "run_id": run["id"],
        "rows": projected["source"]["rows"],
        "association_basis": snapshot["association_basis"],
        "omissions": snapshot["omissions"],
    })
    assert projected["sha256"] == _digest({
        key: value for key, value in projected.items() if key != "sha256"
    })
    assert projected["steps"][0]["completed_at"] is None
    assert projected["execution_receipts"][0]["fact_status"] == "UNVERIFIED_CALLER_METADATA"
    assert projected["research_verification_attempts"][0]["domain_status"] == (
        "NOT_EVALUATED_BY_PROJECTION"
    )
    assert projected["current_use_bindings"][0]["stored_state"] == "current"
    assert projected["current_use_bindings"][0]["freshness_status"] == (
        "NOT_EVALUATED_BY_PROJECTION"
    )
    assert projected["authorization_invocations"][0]["grant_id"] == grant_id
    assert projected["authorization_grants"][0]["id"] == grant_id
    assert projected["authorization_grants"][0]["stored_state"] == "active"
    assert projected["authorization_grants"][0]["current_authority_status"] == (
        "NOT_EVALUATED_BY_PROJECTION"
    )
    assert projected["knowledge_admission_receipts"][0]["id"] == admission_id
    assert projected["knowledge_admission_receipts"][0]["current_use_status"] == (
        "NOT_EVALUATED_BY_PROJECTION"
    )
    decision = projected["invalidation_decisions"][0]
    assert decision["id"] == decision_id
    assert decision["target_scope"] == "receipt"
    assert decision["target_receipt_id"] == qualification_id
    assert decision["target_attempt_id"] is None
    assert decision["affected_binding_ids"] == []
    assert decision["current_effect_status"] == "NOT_EVALUATED_BY_PROJECTION"
    assert projected["signals"]["verified_evidence"] == []
    assert projected["signals"]["goal_binding"] == "NATIVE_UNBOUND"
    assert not any(projected["authority_effects_of_projection"].values())
    serialized = json.dumps(projected)
    assert "verified-from-step" not in serialized
    assert "verified-from-receipt" not in serialized

    with repository._connect() as db:
        db.execute(
            "UPDATE run_steps SET metadata = ? WHERE tenant_id = ? AND id = ?",
            ('{"claimed_fact":"different"}', "projection-owner", step["id"]),
        )
    changed = service.export_run(_context(), run["id"])
    assert changed["source"]["rows"]["steps"][0]["sha256"] != (
        projected["source"]["rows"]["steps"][0]["sha256"]
    )
    assert changed["source"]["sha256"] != projected["source"]["sha256"]
    assert changed["sha256"] != projected["sha256"]
    assert _authority_rows(repository) == authority_before


def test_projection_is_workspace_scoped_and_rejects_mixed_snapshots(tmp_path) -> None:
    repository = _repository(tmp_path)
    own_run = _run(repository)
    other_run = _run(repository, "projection-other")
    repository.append_run_step(
        "projection-other", other_run["id"], 1, "tool", "foreign", "succeeded",
        "", "", {},
    )
    service = LedgerProjectionService(repository_provider=lambda: repository)
    own = service.export_run(_context(), own_run["id"])
    assert own["workspace_id"] == "projection-owner"
    assert own["source"]["rows"]["steps"] == []
    with pytest.raises(ResourceNotFoundError):
        service.export_run(_context(), other_run["id"])
    with pytest.raises(ResourceNotFoundError):
        service.export_run(_context("projection-other"), own_run["id"])

    snapshot = repository.read_run_ledger_snapshot("projection-owner", own_run["id"])
    foreign = repository.read_run_ledger_snapshot("projection-other", other_run["id"])
    assert snapshot is not None and foreign is not None
    mixed = deepcopy(snapshot)
    mixed["steps"] = foreign["steps"]
    with pytest.raises(ValueError, match="Cross-workspace or cross-Run steps row"):
        project_run_snapshot(mixed, "projection-owner", own_run["id"])


def test_stream_projection_preserves_pending_claimed_and_indeterminate_meanings(
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    service = LedgerProjectionService(repository_provider=lambda: repository)
    now = datetime.now(UTC)
    deadline = (now + timedelta(minutes=5)).isoformat()

    pending = _run(repository, stream_start_deadline_at=deadline)
    before_send = service.export_run(_context(), pending["id"])
    assert before_send["run"]["effective_status"] == "running"
    assert before_send["run"]["stream_start_deadline_at"] == deadline
    assert before_send["run"]["stream_claimed_at"] is None
    assert before_send["signals"]["stream_request_state"] == "NOT_SENT"
    assert before_send["signals"]["stream_conflicts"] == []
    assert before_send["signals"]["pending_operations"] == []
    assert before_send["model_usage"] == []
    repository.reconcile_stale_stream_runs((now + timedelta(minutes=6)).isoformat())
    not_started = service.export_run(_context(), pending["id"])
    assert not_started["run"]["error_code"] == "stream_not_started"
    assert not_started["signals"]["stream_request_state"] == "NOT_SENT"
    assert not_started["signals"]["unknown_usage_attempt_ids"] == []

    claimed = _run(repository, stream_start_deadline_at=deadline)
    assert repository.claim_stream_run(
        "projection-owner", claimed["id"], claimed_at=now.isoformat(),
        lease_until=(now + timedelta(minutes=10)).isoformat(),
    )
    in_flight = _begin_usage(repository, claimed, 1)
    may_have_sent = service.export_run(_context(), claimed["id"])
    assert may_have_sent["signals"]["stream_request_state"] == "MAY_HAVE_SENT"
    assert may_have_sent["run"]["stream_claimed_at"] is not None
    assert may_have_sent["signals"]["pending_operations"] == sorted([
        f"model_call:{in_flight['attempt_id']}", f"stream:{claimed['id']}",
    ])
    assert may_have_sent["signals"]["in_flight_model_attempt_ids"] == [
        in_flight["attempt_id"]
    ]
    assert may_have_sent["signals"]["unknown_usage_attempt_ids"] == []
    assert may_have_sent["model_usage"][0]["usage_status"] == "IN_FLIGHT"

    repository.reconcile_stale_stream_runs((now + timedelta(minutes=11)).isoformat())
    indeterminate = service.export_run(_context(), claimed["id"])
    assert indeterminate["run"]["status"] == "failed"
    assert indeterminate["run"]["effective_status"] == "indeterminate"
    assert indeterminate["run"]["error_code"] == "stream_execution_indeterminate"
    assert indeterminate["signals"]["stream_request_state"] == "MAY_HAVE_SENT"
    assert indeterminate["signals"]["pending_operations"] == [f"stream:{claimed['id']}"]
    assert indeterminate["signals"]["unknown_usage_attempt_ids"] == [
        in_flight["attempt_id"]
    ]
    assert indeterminate["signals"]["in_flight_model_attempt_ids"] == []
    assert indeterminate["model_usage"][0]["usage_status"] == "UNKNOWN"
    assert indeterminate["model_usage"][0]["prompt_tokens"] is None
    assert indeterminate["model_usage"][0]["completion_tokens"] is None


def test_contradictory_stream_rows_cannot_claim_request_was_not_sent(tmp_path) -> None:
    repository = _repository(tmp_path)
    service = LedgerProjectionService(repository_provider=lambda: repository)
    now = datetime.now(UTC)
    deadline = (now + timedelta(minutes=5)).isoformat()

    pending_with_claim = _run(repository, stream_start_deadline_at=deadline)
    with repository._connect() as db:
        db.execute(
            "UPDATE agent_runs SET stream_claimed_at = ? WHERE tenant_id = ? AND id = ?",
            (now.isoformat(), "projection-owner", pending_with_claim["id"]),
        )
    pending_signal = service.export_run(_context(), pending_with_claim["id"])["signals"]
    assert pending_signal["stream_request_state"] == "INCONSISTENT"
    assert "not_sent_claim_conflicts_with_recorded_activity" in pending_signal["stream_conflicts"]

    claimed_with_usage = _run(repository, stream_start_deadline_at=deadline)
    assert repository.claim_stream_run(
        "projection-owner", claimed_with_usage["id"], claimed_at=now.isoformat(),
        lease_until=(now + timedelta(minutes=10)).isoformat(),
    )
    receipt = _begin_usage(repository, claimed_with_usage, 1)
    with repository._connect() as db:
        db.execute(
            "UPDATE agent_runs SET stream_state = 'pending' WHERE tenant_id = ? AND id = ?",
            ("projection-owner", claimed_with_usage["id"]),
        )
    contradictory = service.export_run(_context(), claimed_with_usage["id"])
    assert contradictory["signals"]["stream_request_state"] == "INCONSISTENT"
    assert contradictory["run"]["effective_status"] == "indeterminate"
    assert contradictory["signals"]["in_flight_model_attempt_ids"] == [
        receipt["attempt_id"]
    ]
    assert contradictory["signals"]["unknown_usage_attempt_ids"] == []

    with repository._connect() as db:
        db.execute(
            "UPDATE agent_runs SET stream_state = 'closed', status = 'failed', "
            "error_code = 'stream_not_started' WHERE tenant_id = ? AND id = ?",
            ("projection-owner", claimed_with_usage["id"]),
        )
    falsely_not_started = service.export_run(_context(), claimed_with_usage["id"])
    assert falsely_not_started["signals"]["stream_request_state"] == "INCONSISTENT"
    assert falsely_not_started["run"]["effective_status"] == "indeterminate"

    claimed_terminal = _run(repository, stream_start_deadline_at=deadline)
    assert repository.claim_stream_run(
        "projection-owner", claimed_terminal["id"], claimed_at=now.isoformat(),
        lease_until=(now + timedelta(minutes=10)).isoformat(),
    )
    with repository._connect() as db:
        db.execute(
            "UPDATE agent_runs SET status = 'failed', completed_at = ? "
            "WHERE tenant_id = ? AND id = ?",
            (now.isoformat(), "projection-owner", claimed_terminal["id"]),
        )
    claimed_conflict = service.export_run(_context(), claimed_terminal["id"])
    assert claimed_conflict["signals"]["stream_request_state"] == "INCONSISTENT"
    assert "claimed_stream_has_terminal_run_status" in (
        claimed_conflict["signals"]["stream_conflicts"]
    )
    assert claimed_conflict["run"]["effective_status"] == "indeterminate"

    closed_running = _run(repository, stream_start_deadline_at=deadline)
    with repository._connect() as db:
        db.execute(
            "UPDATE agent_runs SET stream_state = 'closed' "
            "WHERE tenant_id = ? AND id = ?",
            ("projection-owner", closed_running["id"]),
        )
    closed_conflict = service.export_run(_context(), closed_running["id"])
    assert closed_conflict["signals"]["stream_request_state"] == "INCONSISTENT"
    assert "closed_stream_has_running_run_status" in (
        closed_conflict["signals"]["stream_conflicts"]
    )
    assert closed_conflict["run"]["effective_status"] == "indeterminate"


def test_reported_zero_usage_is_distinct_from_unknown_usage(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = _run(repository)
    unknown = _begin_usage(repository, run, 1)
    reported = _begin_usage(repository, run, 2)
    for receipt, status, tokens in ((unknown, "UNKNOWN", None), (reported, "REPORTED", 0)):
        repository.finish_model_usage("projection-owner", receipt["attempt_id"], {
            "usage_status": status,
            "prompt_tokens": tokens, "completion_tokens": tokens,
            "estimated_cost": None, "termination": "COMPLETED",
            "finish_reason": "stop", "provider_request_id": None,
            "output_bytes": 0, "finalization_hash": str(uuid4()),
            "step_status": "succeeded", "output_content": "",
            "step_metadata": {"claimed_verdict": "ADMITTED"},
        })
    projected = LedgerProjectionService(
        repository_provider=lambda: repository
    ).export_run(_context(), run["id"])
    by_id = {item["attempt_id"]: item for item in projected["model_usage"]}
    assert projected["signals"]["unknown_usage_attempt_ids"] == [unknown["attempt_id"]]
    assert by_id[unknown["attempt_id"]]["usage_status"] == "UNKNOWN"
    assert by_id[unknown["attempt_id"]]["prompt_tokens"] is None
    assert by_id[reported["attempt_id"]]["usage_status"] == "REPORTED"
    assert by_id[reported["attempt_id"]]["prompt_tokens"] == 0
    assert by_id[reported["attempt_id"]]["completion_tokens"] == 0
    assert projected["signals"]["verified_evidence"] == []

    with repository._connect() as db:
        db.execute(
            "UPDATE model_usage_receipts SET completion_tokens = NULL "
            "WHERE tenant_id = ? AND attempt_id = ?",
            ("projection-owner", reported["attempt_id"]),
        )
    contradictory = LedgerProjectionService(
        repository_provider=lambda: repository
    ).export_run(_context(), run["id"])
    assert contradictory["signals"]["usage_conflict_attempt_ids"] == [
        reported["attempt_id"]
    ]
    assert f"usage_conflict:{reported['attempt_id']}" in (
        contradictory["signals"]["pending_operations"]
    )
    assert contradictory["run"]["effective_status"] == "indeterminate"


def test_in_flight_model_call_remains_pending_even_when_run_says_succeeded(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = _run(repository)
    receipt = _begin_usage(repository, run, 1)
    service = LedgerProjectionService(repository_provider=lambda: repository)

    running = service.export_run(_context(), run["id"])
    assert running["run"]["effective_status"] == "running"
    assert running["signals"]["in_flight_model_attempt_ids"] == [receipt["attempt_id"]]
    assert running["signals"]["unknown_usage_attempt_ids"] == []
    assert running["signals"]["invalid_usage_attempt_ids"] == []
    assert running["signals"]["pending_operations"] == [
        f"model_call:{receipt['attempt_id']}"
    ]

    # The schema permits this cross-table contradiction; the projection must
    # survive it even if normal application paths later reject it.
    with repository._connect() as db:
        db.execute(
            "UPDATE agent_runs SET status = 'succeeded', completed_at = ? "
            "WHERE tenant_id = ? AND id = ?",
            (datetime.now(UTC).isoformat(), "projection-owner", run["id"]),
        )
        stored = db.execute(
            "SELECT status FROM agent_runs WHERE tenant_id = ? AND id = ?",
            ("projection-owner", run["id"]),
        ).fetchone()
        assert stored["status"] == "succeeded"

    contradictory = service.export_run(_context(), run["id"])
    assert contradictory["run"]["status"] == "succeeded"
    assert contradictory["run"]["effective_status"] == "indeterminate"
    assert contradictory["signals"]["usage_lifecycle_conflict_attempt_ids"] == [
        receipt["attempt_id"]
    ]
    assert contradictory["signals"]["in_flight_model_attempt_ids"] == [
        receipt["attempt_id"]
    ]
    assert contradictory["signals"]["pending_operations"] == sorted([
        f"model_call:{receipt['attempt_id']}",
        f"usage_lifecycle_conflict:{receipt['attempt_id']}",
    ])


def test_terminal_unknown_usage_is_not_an_unfinished_model_call(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = _run(repository)
    receipt = _begin_usage(repository, run, 1)
    repository.finish_model_usage("projection-owner", receipt["attempt_id"], {
        "usage_status": "UNKNOWN",
        "prompt_tokens": None, "completion_tokens": None,
        "estimated_cost": None, "termination": "COMPLETED",
        "finish_reason": "stop", "provider_request_id": None,
        "output_bytes": 0, "finalization_hash": str(uuid4()),
        "step_status": "succeeded", "output_content": "answer",
        "step_metadata": {},
    })
    repository.finish_run("projection-owner", run["id"], "succeeded")

    projected = LedgerProjectionService(
        repository_provider=lambda: repository
    ).export_run(_context(), run["id"])
    assert projected["run"]["status"] == "succeeded"
    assert projected["run"]["effective_status"] == "succeeded"
    assert projected["signals"]["in_flight_model_attempt_ids"] == []
    assert projected["signals"]["unknown_usage_attempt_ids"] == [receipt["attempt_id"]]
    assert projected["signals"]["invalid_usage_attempt_ids"] == []
    assert projected["signals"]["usage_lifecycle_conflict_attempt_ids"] == []
    assert projected["signals"]["pending_operations"] == []
    assert projected["model_usage"][0]["finalized_at"] is not None
    assert projected["model_usage"][0]["finalization_hash_present"] is True


def test_usage_lifecycle_conflicts_survive_database_rows(tmp_path) -> None:
    repository = _repository(tmp_path)
    service = LedgerProjectionService(repository_provider=lambda: repository)
    cases = (
        ("in_flight_terminal_step", "IN_FLIGHT",
         "UPDATE run_steps SET status = 'succeeded' WHERE tenant_id = ? AND id = ?"),
        ("in_flight_terminal_fields", "IN_FLIGHT",
         "UPDATE model_usage_receipts SET termination = 'COMPLETED', "
         "finalized_at = '2026-10-15T00:00:00+00:00', finalization_hash = 'hash' "
         "WHERE tenant_id = ? AND step_id = ?"),
        ("in_flight_with_tokens", "IN_FLIGHT",
         "UPDATE model_usage_receipts SET prompt_tokens = 1 "
         "WHERE tenant_id = ? AND step_id = ?"),
        ("in_flight_non_model_step", "IN_FLIGHT",
         "UPDATE run_steps SET kind = 'tool' WHERE tenant_id = ? AND id = ?"),
        ("terminal_missing_termination", "UNKNOWN",
         "UPDATE model_usage_receipts SET termination = NULL "
         "WHERE tenant_id = ? AND step_id = ?"),
        ("terminal_missing_finalized_at", "UNKNOWN",
         "UPDATE model_usage_receipts SET finalized_at = NULL "
         "WHERE tenant_id = ? AND step_id = ?"),
        ("terminal_missing_finalization_hash", "UNKNOWN",
         "UPDATE model_usage_receipts SET finalization_hash = NULL "
         "WHERE tenant_id = ? AND step_id = ?"),
        ("terminal_running_step", "UNKNOWN",
         "UPDATE run_steps SET status = 'running' WHERE tenant_id = ? AND id = ?"),
        ("completed_termination_failed_step", "UNKNOWN",
         "UPDATE run_steps SET status = 'failed' WHERE tenant_id = ? AND id = ?"),
        ("unknown_with_tokens", "UNKNOWN",
         "UPDATE model_usage_receipts SET prompt_tokens = 1, completion_tokens = 2 "
         "WHERE tenant_id = ? AND step_id = ?"),
        ("invalid_with_tokens", "INVALID",
         "UPDATE model_usage_receipts SET prompt_tokens = 1, completion_tokens = 2 "
         "WHERE tenant_id = ? AND step_id = ?"),
    )
    for case_name, usage_status, update_sql in cases:
        run = _run(repository)
        receipt = _begin_usage(repository, run, 1)
        in_flight = usage_status == "IN_FLIGHT"
        if not in_flight:
            repository.finish_model_usage("projection-owner", receipt["attempt_id"], {
                "usage_status": usage_status,
                "prompt_tokens": None, "completion_tokens": None,
                "estimated_cost": None, "termination": "COMPLETED",
                "finish_reason": "stop", "provider_request_id": None,
                "output_bytes": 0, "finalization_hash": str(uuid4()),
                "step_status": "succeeded", "output_content": "answer",
                "step_metadata": {},
            })
        with repository._connect() as db:
            assert db.execute(
                update_sql, ("projection-owner", receipt["step_id"]),
            ).rowcount == 1, case_name

        projected = service.export_run(_context(), run["id"])
        assert projected["run"]["effective_status"] == "indeterminate", case_name
        assert projected["signals"]["usage_lifecycle_conflict_attempt_ids"] == [
            receipt["attempt_id"]
        ], case_name
        assert f"usage_lifecycle_conflict:{receipt['attempt_id']}" in (
            projected["signals"]["pending_operations"]
        ), case_name
        assert (f"model_call:{receipt['attempt_id']}" in
                projected["signals"]["pending_operations"]) is in_flight, case_name
        if case_name == "terminal_missing_finalization_hash":
            assert projected["model_usage"][0]["finalization_hash_present"] is False


@pytest.mark.skipif(
    not os.getenv("AIGC_LITE_TEST_POSTGRES_URL"),
    reason="AIGC_LITE_TEST_POSTGRES_URL is not configured",
)
def test_postgres_terminal_unknown_and_succeeded_in_flight_projection() -> None:
    repository = PostgresRepository(os.environ["AIGC_LITE_TEST_POSTGRES_URL"])
    repository.init()
    workspace_id = f"projection-{uuid4()}"
    repository.create_tenant("PostgreSQL projection contract", workspace_id)
    service = LedgerProjectionService(repository_provider=lambda: repository)

    completed_run = _run(repository, workspace_id)
    unknown = _begin_usage(repository, completed_run, 1)
    repository.finish_model_usage(workspace_id, unknown["attempt_id"], {
        "usage_status": "UNKNOWN",
        "prompt_tokens": None, "completion_tokens": None,
        "estimated_cost": None, "termination": "COMPLETED",
        "finish_reason": "stop", "provider_request_id": None,
        "output_bytes": 0, "finalization_hash": str(uuid4()),
        "step_status": "succeeded", "output_content": "answer",
        "step_metadata": {},
    })
    repository.finish_run(workspace_id, completed_run["id"], "succeeded")
    completed = service.export_run(_context(workspace_id), completed_run["id"])
    assert completed["run"]["effective_status"] == "succeeded"
    assert completed["signals"]["unknown_usage_attempt_ids"] == [unknown["attempt_id"]]
    assert completed["signals"]["in_flight_model_attempt_ids"] == []
    assert completed["signals"]["pending_operations"] == []

    contradictory_run = _run(repository, workspace_id)
    in_flight = _begin_usage(repository, contradictory_run, 1)
    with repository.engine.begin() as connection:
        updated = connection.execute(text(
            "UPDATE agent_runs SET status = 'succeeded', completed_at = :completed_at "
            "WHERE tenant_id = :tenant_id AND id = :run_id"
        ), {
            "completed_at": datetime.now(UTC).isoformat(),
            "tenant_id": workspace_id, "run_id": contradictory_run["id"],
        })
        assert updated.rowcount == 1
    contradictory = service.export_run(_context(workspace_id), contradictory_run["id"])
    assert contradictory["run"]["status"] == "succeeded"
    assert contradictory["run"]["effective_status"] == "indeterminate"
    assert contradictory["signals"]["in_flight_model_attempt_ids"] == [
        in_flight["attempt_id"]
    ]
    assert contradictory["signals"]["usage_lifecycle_conflict_attempt_ids"] == [
        in_flight["attempt_id"]
    ]
    assert f"model_call:{in_flight['attempt_id']}" in (
        contradictory["signals"]["pending_operations"]
    )
