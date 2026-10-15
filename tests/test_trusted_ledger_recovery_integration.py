"""Real native records feed candidate Recovery without inventing evidence."""

from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.artifacts import ArtifactDraft, ArtifactKind
from app.core.contracts import NativeGoalDraft, RequestContext
from app.core.evidence import ExecutionReceiptDraft, ProtocolDraft, ProtocolStatus
from app.repository import SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.evidence import EvidenceService
from app.services.ledger_projection import LedgerProjectionService

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1]
           / "casepacks" / "long-horizon-recovery-v0" / "src"),
)

from aigc_lite_casepacks.long_horizon_recovery_v0.host_projection_replay import (  # noqa: E402
    replay_host_projection,
)
from aigc_lite_casepacks.long_horizon_recovery_v0.replay import (  # noqa: E402
    ContractError,
    digest,
)


def _goal(protocol: dict, workspace_id: str) -> dict:
    return {
        "contract": "LongHorizonGoal.v0",
        "protocol": {
            "id": protocol["id"], "workspace_id": workspace_id,
            "version": protocol["version"], "status": protocol["status"],
            "purpose": protocol["purpose"], "scope": protocol["scope"],
            "completion_predicate": protocol["completion_predicate"],
            "stop_conditions": protocol["stop_conditions"],
            "budget": protocol["budget"],
        },
        "target": "authenticated-parent-lineage",
        "policy": {
            "milestones": ["parent-lineage-verified"],
            "evidence_criteria": ["authenticated-parent-lineage"],
            "resource_keys": [], "critical_observations": [],
            "freshness_seconds": 60, "stagnation_step_limit": 2,
            "forbidden_actions": ["grant-authority"],
        },
    }


def _setup(tmp_path):
    repository = SQLiteRepository(tmp_path / "native-recovery.db")
    repository.init()
    workspace_id = str(uuid4())
    repository.create_tenant("Native recovery", workspace_id)
    context = RequestContext(request_id=str(uuid4()), workspace_id=workspace_id)
    provider = lambda: repository  # noqa: E731
    evidence = EvidenceService(provider)
    protocol = evidence.create_protocol(
        context,
        ProtocolDraft(
            name="Bio parent-source research",
            profile="long-horizon-recovery.v0",
            purpose="Seek authenticated parent-source lineage.",
            scope="One candidate-only diagnostic Run.",
            completion_predicate="Parent-source lineage independently verified.",
            stop_conditions=("Budget exhausted",),
            budget={"max_steps": 20, "max_wall_seconds": 3600},
            status=ProtocolStatus.FROZEN,
        ),
        content_hash=hashlib.sha256(b"host-owned goal draft").hexdigest(),
    )
    session = repository.create_session(workspace_id, "Bio lineage search")
    run = repository.create_run(
        workspace_id, session["id"], context.request_id, None, "research-route",
    )
    return repository, context, protocol, run


def _authority_rows(repository: SQLiteRepository, workspace_id: str) -> dict:
    tables = (
        "qualification_receipts", "current_use_bindings",
        "knowledge_admission_receipts", "authorization_grants",
        "authorization_invocations", "enforcement_dispatches",
    )
    with repository._connect() as db:
        return {
            table: [dict(row) for row in db.execute(
                f"SELECT * FROM {table} WHERE tenant_id = ? ORDER BY id", (workspace_id,),
            )]
            for table in tables
        }


def test_real_bio_like_ledger_activity_cannot_become_verified_evidence(tmp_path) -> None:
    repository, context, protocol, run = _setup(tmp_path)
    workspace_id = context.workspace_id
    artifact_service = ArtifactService(repository_provider=lambda: repository)
    evidence = EvidenceService(lambda: repository)
    steps = [
        repository.append_run_step(
            workspace_id, run["id"], sequence, "retrieval", "paper-search",
            "succeeded", "query", "candidate summary",
            {"model_claim": "lineage verified", "verified_evidence": True},
        )
        for sequence in range(1, 4)
    ]
    candidates = [
        artifact_service.create_artifact(
            context,
            ArtifactDraft(
                name=f"summary-{sequence}.json", kind=ArtifactKind.JSON,
                media_type="application/json", content_text=f'{{"summary":{sequence}}}',
            ),
            run_id=run["id"], step_id=steps[sequence - 1]["id"],
        )
        for sequence in (2, 3)
    ]
    evidence.create_receipt(
        context, protocol["id"],
        ExecutionReceiptDraft(
            status="succeeded", input_digest=candidates[0]["content_hash"],
            output_digest=candidates[1]["content_hash"], run_id=run["id"],
            artifact_ids=tuple(item["id"] for item in candidates),
            metadata={"fact": {"kind": "evidence", "criterion_id": "authenticated-parent-lineage"}},
        ),
    )
    repository.finish_run(workspace_id, run["id"], "succeeded")
    before = _authority_rows(repository, workspace_id)

    projection = LedgerProjectionService(
        repository_provider=lambda: repository,
    ).export_run(context, run["id"])
    frozen_goal = _goal(protocol, workspace_id)
    result = replay_host_projection(
        frozen_goal, projection,
        expected_goal_sha256=digest(frozen_goal),
        expected_projection_sha256=projection["sha256"],
        previous_sequence=1, current_sequence=3,
    )

    assert len(result["window"]["recorded_step_refs"]) == 2
    assert len(result["window"]["candidate_artifact_refs"]) == 2
    assert result["progress_assessment"]["E"]["projected_verified_count"] == 0
    assert result["progress_assessment"]["E"]["candidate_artifact_counted"] is False
    assert result["progress_assessment"]["completion_evaluated"] is False
    assert result["goal_binding"] == "HOST_ASSERTED_NOT_NATIVE"
    assert result["recovery_decision"]["action"] == "REASSESS"
    assert result["recovery_decision"]["state"] == "PROPOSED"
    assert result["recovery_decision"]["execution_authorized"] is False
    assert _authority_rows(repository, workspace_id) == before

    altered = {**projection, "run": {**projection["run"], "status": "running"}}
    with pytest.raises(ContractError, match="digest mismatch"):
        replay_host_projection(
            frozen_goal, altered,
            expected_goal_sha256=digest(frozen_goal),
            expected_projection_sha256=projection["sha256"],
        )


def test_real_native_goal_binding_replays_without_granting_goal_progress(tmp_path) -> None:
    repository, context, protocol, _ = _setup(tmp_path)
    goal = _goal(protocol, context.workspace_id)
    session = repository.create_session(context.workspace_id, "Bound recovery")
    run = repository.create_run(
        context.workspace_id, session["id"], str(uuid4()), None, "research-route",
        goal_binding=NativeGoalDraft(protocol["id"], protocol["version"], goal),
    )
    repository.append_run_step(
        context.workspace_id, run["id"], 1, "retrieval", "paper-search",
        "succeeded", "query", "candidate summary",
        {"verified_evidence": True},
    )
    repository.finish_run(context.workspace_id, run["id"], "succeeded")
    before = _authority_rows(repository, context.workspace_id)

    projection = LedgerProjectionService(
        repository_provider=lambda: repository,
    ).export_run(context, run["id"])
    assert projection["contract"] == "HostLedgerProjection.v1"
    assert projection["signals"]["goal_binding"] == "NATIVE_BOUND"
    replay = replay_host_projection(
        goal, projection,
        expected_goal_sha256=run["goal_sha256"],
        expected_projection_sha256=projection["sha256"],
    )
    assert replay["kind"] == "HostProjectionReplay.v1"
    assert replay["goal_binding"] == "NATIVE_BOUND"
    assert replay["progress_assessment"]["completion_evaluated"] is False
    assert replay["progress_assessment"]["E"]["projected_verified_count"] == 0
    assert replay["recovery_decision"]["state"] == "PROPOSED"
    assert replay["recovery_decision"]["execution_authorized"] is False
    assert _authority_rows(repository, context.workspace_id) == before


def test_real_claimed_stream_with_unknown_usage_pauses_recovery(tmp_path) -> None:
    repository, context, protocol, _ = _setup(tmp_path)
    now = datetime.now(UTC)
    session = repository.create_session(context.workspace_id, "stream research")
    run = repository.create_run(
        context.workspace_id, session["id"], str(uuid4()), None, "research-route",
        stream_start_deadline_at=(now + timedelta(minutes=5)).isoformat(),
    )
    assert repository.claim_stream_run(
        context.workspace_id, run["id"], claimed_at=now.isoformat(),
        lease_until=(now + timedelta(minutes=10)).isoformat(),
    )
    usage = repository.begin_model_usage({
        "attempt_id": str(uuid4()), "model_call_id": str(uuid4()),
        "step_id": str(uuid4()), "tenant_id": context.workspace_id,
        "principal_id": None, "api_key_id": None,
        "request_id": run["request_id"], "run_id": run["id"],
        "sequence": 1, "iteration": 1,
        "model": "research-route", "provider_config_id": None,
        "pricing_snapshot": {"version": 1}, "input_content": "candidate search",
        "step_metadata": {},
    })
    repository.reconcile_stale_stream_runs((now + timedelta(minutes=11)).isoformat())
    before = _authority_rows(repository, context.workspace_id)
    projection = LedgerProjectionService(
        repository_provider=lambda: repository,
    ).export_run(context, run["id"])
    frozen_goal = _goal(protocol, context.workspace_id)
    result = replay_host_projection(
        frozen_goal, projection,
        expected_goal_sha256=digest(frozen_goal),
        expected_projection_sha256=projection["sha256"],
    )

    assert projection["run"]["status"] == "failed"
    assert projection["run"]["effective_status"] == "indeterminate"
    assert projection["signals"]["unknown_usage_attempt_ids"] == [usage["attempt_id"]]
    assert result["recovery_decision"]["action"] == "PAUSE"
    assert result["progress_assessment"]["E"]["projected_verified_count"] == 0
    assert result["recovery_decision"]["execution_authorized"] is False
    assert _authority_rows(repository, context.workspace_id) == before


def test_real_terminal_unknown_usage_does_not_imply_active_provider_call(tmp_path) -> None:
    repository, context, protocol, run = _setup(tmp_path)
    usage = repository.begin_model_usage({
        "attempt_id": str(uuid4()), "model_call_id": str(uuid4()),
        "step_id": str(uuid4()), "tenant_id": context.workspace_id,
        "principal_id": None, "api_key_id": None,
        "request_id": run["request_id"], "run_id": run["id"],
        "sequence": 1, "iteration": 1,
        "model": "research-route", "provider_config_id": None,
        "pricing_snapshot": {"version": 1}, "input_content": "candidate search",
        "step_metadata": {},
    })
    repository.finish_model_usage(context.workspace_id, usage["attempt_id"], {
        "usage_status": "UNKNOWN", "prompt_tokens": None,
        "completion_tokens": None, "estimated_cost": None,
        "termination": "COMPLETED", "finish_reason": "stop",
        "provider_request_id": None, "output_bytes": 6,
        "finalization_hash": str(uuid4()), "step_status": "succeeded",
        "output_content": "answer", "step_metadata": {},
    })
    repository.finish_run(context.workspace_id, run["id"], "succeeded")
    before = _authority_rows(repository, context.workspace_id)
    projection = LedgerProjectionService(
        repository_provider=lambda: repository,
    ).export_run(context, run["id"])
    frozen_goal = _goal(protocol, context.workspace_id)
    result = replay_host_projection(
        frozen_goal, projection,
        expected_goal_sha256=digest(frozen_goal),
        expected_projection_sha256=projection["sha256"],
    )

    assert projection["run"]["effective_status"] == "succeeded"
    assert projection["signals"]["in_flight_model_attempt_ids"] == []
    assert projection["signals"]["unknown_usage_attempt_ids"] == [usage["attempt_id"]]
    assert projection["signals"]["pending_operations"] == []
    assert result["progress_assessment"]["U"]["pending_operations"] == []
    assert result["progress_assessment"]["U"]["unknown_usage_attempt_ids"] == [
        usage["attempt_id"]
    ]
    assert result["recovery_decision"]["action"] == "REASSESS"
    assert result["recovery_decision"]["execution_authorized"] is False
    assert _authority_rows(repository, context.workspace_id) == before
