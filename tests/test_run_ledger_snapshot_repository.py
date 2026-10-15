"""A Run projection reads complete native facts, not candidate authority labels."""

from __future__ import annotations

import json
import os
from hashlib import sha256
from uuid import uuid4

import pytest

import app.repository as repository_module
from app.core.contracts import RequestContext
from app.repository import PostgresRepository, SQLiteRepository
from app.services.ledger_projection import LedgerProjectionService

POSTGRES_URL = os.getenv("AIGC_LITE_TEST_POSTGRES_URL", "")


def _repository(backend: str, tmp_path):
    if backend == "postgres":
        if not POSTGRES_URL:
            pytest.skip("AIGC_LITE_TEST_POSTGRES_URL is not configured")
        repository = PostgresRepository(POSTGRES_URL)
    else:
        repository = SQLiteRepository(tmp_path / "ledger-snapshot.db")
    repository.init()
    return repository


def _insert(repository, statement: str, values: dict) -> None:
    if isinstance(repository, SQLiteRepository):
        with repository._connect() as db:
            db.execute(statement, values)
    else:
        repository._execute(statement, values)


def _insert_row(repository, table: str, values: dict) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join(f":{name}" for name in values)
    _insert(repository, f"INSERT INTO {table}({columns}) VALUES ({placeholders})", values)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_read_run_ledger_snapshot_is_complete_scoped_and_not_authority(
    backend: str, tmp_path
) -> None:
    repository = _repository(backend, tmp_path)
    workspace_id = str(uuid4())
    other_workspace = str(uuid4())
    repository.create_tenant("Ledger owner", workspace_id)
    repository.create_tenant("Other ledger", other_workspace)
    session = repository.create_session(workspace_id, "ledger")
    run = repository.create_run(
        workspace_id, session["id"], f"schedule:task:{uuid4()}", None, "test-model"
    )
    other_session = repository.create_session(other_workspace, "other")
    other_run = repository.create_run(
        other_workspace, other_session["id"], str(uuid4()), None, "other-model"
    )

    step = repository.append_run_step(
        workspace_id, run["id"], 1, "tool", "record", "succeeded", "", "", {}
    )
    for index in range(205):  # Above the existing get_run() artifact limit.
        content = f"candidate {index}"
        repository.create_artifact(
            workspace_id,
            {
                "run_id": run["id"], "step_id": step["id"],
                "name": f"candidate-{index}", "kind": "json",
                "media_type": "application/json", "content_text": content,
                "content_hash": sha256(content.encode()).hexdigest(),
            },
        )
    repository.create_artifact(
        other_workspace,
        {
            "run_id": other_run["id"], "name": "other", "kind": "json",
            "media_type": "application/json", "content_text": "other",
            "content_hash": sha256(b"other").hexdigest(),
        },
    )

    attempt_id = str(uuid4())
    repository.begin_model_usage({
        "attempt_id": attempt_id, "model_call_id": str(uuid4()),
        "step_id": str(uuid4()), "tenant_id": workspace_id,
        "principal_id": "test-principal", "api_key_id": None,
        "request_id": run["request_id"], "run_id": run["id"],
        "sequence": 2, "iteration": 1, "model": "test-model",
        "provider_config_id": None, "pricing_snapshot": {"version": 1},
        "input_content": "prompt", "step_metadata": {},
    })
    execution_receipt_id = str(uuid4())
    _insert_row(repository, "execution_receipts", {
        "id": execution_receipt_id, "tenant_id": workspace_id,
        "protocol_id": str(uuid4()), "run_id": run["id"],
        "status": "succeeded", "input_digest": "a" * 64,
        "output_digest": "b" * 64, "runtime": "{}", "budget": "{}",
        "artifact_ids": "[]", "metadata": '{"caller_declared":true}',
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    dispatch_id = str(uuid4())
    _insert_row(repository, "enforcement_dispatches", {
        "id": dispatch_id, "tenant_id": workspace_id,
        "contract_version": "test.v1", "adapter_id": str(uuid4()),
        "issuer_id": "test-issuer", "target_type": "tool_provider",
        "target_id": "test-target", "run_id": run["id"],
        "step_id": step["id"], "proposal_id": str(uuid4()),
        "request_hash": "c" * 64, "execution_envelope_hash": "d" * 64,
        "tool_spec_hash": "e" * 64, "arguments_digest": "f" * 64,
        "requested_at": "2026-01-01T00:00:00+00:00",
        "expires_at": "2026-01-01T00:01:00+00:00",
        "state": "indeterminate", "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    })
    grant_id = str(uuid4())
    _insert_row(repository, "authorization_grants", {
        "id": grant_id, "tenant_id": workspace_id,
        "qualification_receipt_id": str(uuid4()), "actor_id": "test-principal",
        "action": "test", "target": "test-target", "scope": "{}",
        "conditions": "{}", "budget": "{}", "max_calls": 1,
        "calls_used": 1, "policy_version": "test-policy.v1",
        "state": "active", "grant_receipt": "a" * 64,
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    invocation_id = str(uuid4())
    _insert_row(repository, "authorization_invocations", {
        "id": invocation_id, "tenant_id": workspace_id,
        "grant_id": grant_id, "grant_call_number": 1,
        "actor_id": "test-principal", "request_id": run["request_id"],
        "run_id": run["id"], "action": "test", "target": "test-target",
        "provider_id": "test-provider", "native_name": "test-native",
        "public_name": "test-public", "source": "local",
        "binding_id": str(uuid4()), "binding_hash": "a" * 64,
        "adapter_id": "test-adapter", "proposal_id": "test-proposal",
        "policy_hash": "b" * 64, "adapter_target_type": "tool_provider",
        "adapter_target_id": "test-target", "tool_spec_hash": "c" * 64,
        "arguments_digest": "d" * 64, "invocation_digest": "e" * 64,
        "consumed_at": "2026-01-01T00:00:00+00:00",
        "claim_expires_at": "2026-01-01T00:01:00+00:00",
        "dispatch_id": dispatch_id, "claimed_at": "2026-01-01T00:00:00+00:00",
    })
    _insert(
        repository,
        "INSERT INTO scheduled_task_firings("
        "id, tenant_id, task_id, due_at, lease_owner, lease_expires_at, "
        "state, claimed_at) VALUES (:id, :tenant_id, :task_id, :due_at, "
        ":lease_owner, :lease_expires_at, :state, :claimed_at)",
        {
            "id": str(uuid4()), "tenant_id": workspace_id, "task_id": "task",
            "due_at": "2026-01-01T00:00:00+00:00", "lease_owner": "worker",
            "lease_expires_at": "2026-01-01T00:01:00+00:00", "state": "running",
            "claimed_at": "2026-01-01T00:00:00+00:00",
        },
    )

    snapshot = repository.read_run_ledger_snapshot(workspace_id, run["id"])
    assert snapshot is not None
    assert snapshot["run"]["id"] == run["id"]
    assert len(snapshot["steps"]) == 2
    assert len(snapshot["artifacts"]) == 205
    assert {item["tenant_id"] for item in snapshot["artifacts"]} == {workspace_id}
    assert snapshot["model_usage_receipts"][0]["attempt_id"] == attempt_id
    assert snapshot["model_usage_receipts"][0]["usage_status"] == "IN_FLIGHT"
    assert snapshot["execution_receipts"][0]["id"] == execution_receipt_id
    assert snapshot["execution_receipts"][0]["metadata"] == '{"caller_declared":true}'
    assert snapshot["enforcement_dispatches"][0]["id"] == dispatch_id
    assert snapshot["enforcement_dispatches"][0]["state"] == "indeterminate"
    assert snapshot["authorization_invocations"][0]["id"] == invocation_id
    assert snapshot["authorization_grants"][0]["id"] == grant_id
    assert snapshot["association_basis"]["grant_related_via_consumed_invocation"] == [grant_id]
    assert snapshot["artifacts"][0]["kind"] == "json"
    assert "candidate_only" not in snapshot["artifacts"][0]
    assert snapshot["scheduled_task_firings"] == []
    assert {item["source"] for item in snapshot["omissions"]} >= {
        "scheduled_task_firings", "run_goal_binding", "claim_related_authority",
    }
    assert repository.read_run_ledger_snapshot(other_workspace, run["id"]) is None
    assert repository.read_run_ledger_snapshot(workspace_id, other_run["id"]) is None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_read_run_ledger_snapshot_marks_claim_authority_as_related_only(
    backend: str, tmp_path
) -> None:
    repository = _repository(backend, tmp_path)
    workspace_id = str(uuid4())
    repository.create_tenant("Claim link", workspace_id)
    session = repository.create_session(workspace_id, "claim")
    run = repository.create_run(workspace_id, session["id"], str(uuid4()), None, "checker")
    claim_id = str(uuid4())
    _insert(
        repository,
        "INSERT INTO research_verification_attempts("
        "id, tenant_id, research_case_id, claim_revision_id, receipt_id, run_id, "
        "kind, outcome, method, scope, input_digest, output_digest, created_at) "
        "VALUES (:id, :tenant_id, :research_case_id, :claim_revision_id, "
        ":receipt_id, :run_id, :kind, :outcome, :method, :scope, "
        ":input_digest, :output_digest, :created_at)",
        {
            "id": str(uuid4()), "tenant_id": workspace_id,
            "research_case_id": str(uuid4()), "claim_revision_id": claim_id,
            "receipt_id": str(uuid4()), "run_id": run["id"],
            "kind": "review", "outcome": "inconclusive", "method": "test",
            "scope": "candidate", "input_digest": "a" * 64,
            "output_digest": "b" * 64, "created_at": "2026-01-01T00:00:00+00:00",
        },
    )
    admission_id = str(uuid4())
    qualification_receipt_id = str(uuid4())
    evaluation_id = str(uuid4())
    _insert_row(repository, "qualification_evaluations", {
        "id": evaluation_id, "tenant_id": workspace_id,
        "claim_revision_id": claim_id, "claim_semantic_hash": "b" * 64,
        "profile_id": "other.profile.v1", "profile_version": 1,
        "profile_hash": "0" * 64, "profile_snapshot": "{}",
        "evidence_closure_hash": "e" * 64, "evidence_closure": "{}",
        "policy_version": "test.v1", "policy_hash": "f" * 64,
        "verdict": "ADMITTED", "criteria": "[]", "blockers": "[]",
        "evidence_vector": "{}", "independence_summary": "{}",
        "evaluated_by": "test-admin", "created_at": "2026-01-01T00:00:00+00:00",
    })
    _insert_row(repository, "qualification_receipts", {
        "id": qualification_receipt_id, "tenant_id": workspace_id,
        "evaluation_id": evaluation_id, "claim_revision_id": claim_id,
        "claim_semantic_hash": "b" * 64,
        "profile_id": "other.profile.v1", "profile_version": 1,
        "evidence_closure_hash": "e" * 64,
        "policy_version": "test.v1", "policy_hash": "f" * 64,
        "verdict": "ADMITTED", "criteria": "[]", "blockers": "[]",
        "evidence_vector": "{}", "independence_summary": "{}",
        "receipt_hash": "a" * 64,
        "issued_at": "2026-01-01T00:00:00+00:00",
    })
    _insert_row(repository, "knowledge_admission_receipts", {
        "id": admission_id, "tenant_id": workspace_id,
        "contract_version": "test.v1",
        "qualification_receipt_id": qualification_receipt_id,
        "qualification_receipt_hash": "a" * 64,
        "claim_revision_id": claim_id, "claim_semantic_hash": "b" * 64,
        "profile_id": "other.profile.v1", "profile_version": 1,
        "use_scope": "knowledge", "admission_policy_id": "test.v1",
        "admission_policy_version": 1, "admission_policy_hash": "c" * 64,
        "approved_by": "test-admin", "rationale": "historical admission",
        "receipt_hash": "d" * 64,
        "issued_at": "2026-01-01T00:00:00+00:00",
    })
    _insert(
        repository,
        "INSERT INTO current_use_bindings("
        "id, tenant_id, claim_revision_id, profile_id, use_scope, "
        "qualification_receipt_id, knowledge_admission_receipt_id, state, "
        "created_at, updated_at) "
        "VALUES (:id, :tenant_id, :claim_revision_id, :profile_id, "
        ":use_scope, :qualification_receipt_id, :knowledge_admission_receipt_id, "
        ":state, :created_at, :updated_at)",
        {
            "id": str(uuid4()), "tenant_id": workspace_id,
            "claim_revision_id": claim_id, "profile_id": "other.profile.v1",
            "use_scope": "knowledge", "qualification_receipt_id": qualification_receipt_id,
            "knowledge_admission_receipt_id": admission_id,
            "state": "stale", "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )
    invalidation_id = str(uuid4())
    _insert_row(repository, "invalidation_decisions", {
        "id": invalidation_id, "tenant_id": workspace_id,
        "notice_verification_id": str(uuid4()),
        "target_claim_revision_id": claim_id,
        "target_claim_semantic_hash": "a" * 64,
        "target_scope": "claim_revision", "reason_code": "WITHDRAWN_UPSTREAM_CONSTRUCTION",
        "evidence_artifact_ids": "[]", "evidence_artifact_hashes": "[]",
        "policy_id": "invalidation.pinned-source-admin.v1", "policy_hash": "b" * 64,
        "rationale": "Test decision", "decided_by": "test-admin",
        "effect_state": "stale", "affected_binding_ids": "[]",
        "decision_hash": "c" * 64,
        "decided_at": "2026-01-01T00:00:00+00:00",
    })

    snapshot = repository.read_run_ledger_snapshot(workspace_id, run["id"])
    assert snapshot is not None
    assert snapshot["association_basis"]["claim_related_via_recorded_attempt"] == [claim_id]
    assert len(snapshot["research_verification_attempts"]) == 1
    assert len(snapshot["current_use_bindings"]) == 1
    assert snapshot["current_use_bindings"][0]["state"] == "stale"
    assert [item["id"] for item in snapshot["knowledge_admission_receipts"]] == [admission_id]
    assert snapshot["association_basis"]["admission_related_via_current_binding"] == [admission_id]
    assert [item["id"] for item in snapshot["invalidation_decisions"]] == [invalidation_id]
    assert not any(
        item["source"] == "claim_related_authority" for item in snapshot["omissions"]
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_read_run_ledger_snapshot_holds_one_database_version(
    backend: str, tmp_path, monkeypatch
) -> None:
    repository = _repository(backend, tmp_path)
    workspace_id = str(uuid4())
    repository.create_tenant("Snapshot isolation", workspace_id)
    session = repository.create_session(workspace_id, "snapshot")
    run = repository.create_run(workspace_id, session["id"], str(uuid4()), None, "model")
    if isinstance(repository, SQLiteRepository):
        with repository._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
        competitor = SQLiteRepository(repository.database_path)
    else:
        competitor = PostgresRepository(repository.database_url)

    original = repository_module.read_run_ledger_rows

    def inject_concurrent_commit(rows, tenant_id: str, run_id: str):
        observed = rows(
            "SELECT * FROM agent_runs WHERE tenant_id = :tenant_id AND id = :run_id",
            {"tenant_id": tenant_id, "run_id": run_id},
        )
        assert observed[0]["id"] == run["id"]
        competitor.create_artifact(
            workspace_id,
            {
                "run_id": run["id"], "name": "arrived-after-snapshot",
                "kind": "json", "media_type": "application/json",
                "content_text": "later", "content_hash": sha256(b"later").hexdigest(),
            },
        )
        return original(rows, tenant_id, run_id)

    monkeypatch.setattr(repository_module, "read_run_ledger_rows", inject_concurrent_commit)
    snapshot = repository.read_run_ledger_snapshot(workspace_id, run["id"])
    assert snapshot is not None
    assert snapshot["artifacts"] == []
    monkeypatch.setattr(repository_module, "read_run_ledger_rows", original)
    later = repository.read_run_ledger_snapshot(workspace_id, run["id"])
    assert later is not None
    assert [item["name"] for item in later["artifacts"]] == ["arrived-after-snapshot"]
    projected = LedgerProjectionService(
        repository_provider=lambda: repository
    ).export_run(RequestContext(request_id="snapshot-read", workspace_id=workspace_id), run["id"])
    assert projected["artifacts"][0]["content_hash"] == sha256(b"later").hexdigest()
    assert json.loads(json.dumps(projected))["sha256"] == projected["sha256"]
