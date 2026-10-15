"""Native projection diagnostics cannot turn activity into goal authority."""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aigc_lite_casepacks.long_horizon_recovery_v0.host_projection_replay import (  # noqa: E402
    replay_host_projection,
)
from aigc_lite_casepacks.long_horizon_recovery_v0.replay import (  # noqa: E402
    ContractError,
    digest,
)

_T0 = "2026-10-15T00:00:00+00:00"
_T1 = "2026-10-15T00:00:01+00:00"
_GROUPS = {
    "agent_runs": ("run", "id"),
    "steps": ("steps", "id"),
    "model_usage_receipts": ("model_usage", "attempt_id"),
    "artifacts": ("artifacts", "id"),
    "execution_receipts": ("execution_receipts", "id"),
    "enforcement_dispatches": ("enforcement_dispatches", "id"),
    "authorization_invocations": ("authorization_invocations", "id"),
    "authorization_grants": ("authorization_grants", "id"),
    "research_verification_attempts": ("research_verification_attempts", "id"),
    "current_use_bindings": ("current_use_bindings", "id"),
    "knowledge_admission_receipts": ("knowledge_admission_receipts", "id"),
    "invalidation_decisions": ("invalidation_decisions", "id"),
}
_EXTRA_OMISSIONS = [
    {"source": "verified_evidence",
     "reason": "domain_verification_and_currentness_not_evaluated"},
    {"source": "step_completion",
     "reason": "native_run_steps_have_no_general_completion_timestamp"},
    {"source": "cross_snapshot_row_delta",
     "reason": "single_snapshot_cannot_identify_late_updates_to_existing_steps"},
]


def _hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _goal() -> dict:
    return {
        "contract": "LongHorizonGoal.v0",
        "protocol": {
            "id": "p-1", "workspace_id": "w-1", "version": 1,
            "status": "frozen", "purpose": "Bio lineage research",
            "scope": "Recorded run only",
            "completion_predicate": "Verified parent lineage established",
            "stop_conditions": ["Budget exhausted"],
            "budget": {"max_steps": 100, "max_wall_seconds": 1000},
        },
        "target": "authenticated-parent-lineage",
        "policy": {
            "milestones": ["lineage-established"],
            "evidence_criteria": ["authenticated-parent-lineage"],
            "resource_keys": [], "critical_observations": [],
            "freshness_seconds": 60, "stagnation_step_limit": 4,
            "forbidden_actions": [],
        },
    }


def _step(sequence: int, kind: str = "retrieval") -> dict:
    return {
        "id": f"step-{sequence}", "sequence": sequence,
        "kind": kind, "status": "succeeded", "recorded_at": _T1,
        "completed_at": None, "source_sha256": _hash({"native-step": sequence}),
    }


def _projection(
    *, steps: list[dict] | None = None, artifacts: list[dict] | None = None,
    usage: list[dict] | None = None, dispatches: list[dict] | None = None,
    stream_state: str | None = None, error_code: str | None = None,
    run_status: str = "running", stream_claimed_at: str | None = None,
) -> dict:
    steps = steps or []
    artifacts = artifacts or []
    usage = usage or []
    dispatches = dispatches or []
    run = {
        "id": "run-1", "status": run_status, "effective_status": run_status,
        "stream_state": stream_state, "error_code": error_code,
        "request_id": "request-1", "created_at": _T0,
        "completed_at": _T1 if run_status != "running" else None,
        "stream_claimed_at": stream_claimed_at,
        "stream_start_deadline_at": None,
        "stream_lease_until": None,
        "stream_cancel_requested_at": None,
        "source_sha256": _hash({"native-run": "run-1"}),
    }
    groups: dict[str, object] = {
        "run": run, "steps": steps, "model_usage": usage,
        "artifacts": artifacts, "execution_receipts": [],
        "enforcement_dispatches": dispatches, "authorization_invocations": [],
        "authorization_grants": [], "research_verification_attempts": [],
        "current_use_bindings": [], "knowledge_admission_receipts": [],
        "invalidation_decisions": [],
    }
    source_rows = {}
    for source_name, (field, id_field) in _GROUPS.items():
        items = [groups[field]] if field == "run" else groups[field]
        source_rows[source_name] = [
            {"id": item[id_field], "sha256": item["source_sha256"]}
            for item in items
        ]
    source_omissions = [
        {"source": "run_goal_binding",
         "reason": "agent_runs_do_not_store_protocol_or_goal_identity"},
    ]
    association_basis = {
        "direct_run_id": [
            "steps", "model_usage_receipts", "artifacts", "execution_receipts",
            "enforcement_dispatches", "authorization_invocations",
            "research_verification_attempts",
        ],
        "claim_related_via_recorded_attempt": [],
        "grant_related_via_consumed_invocation": [],
        "admission_related_via_current_binding": [],
    }
    source = {
        "kind": "repository_snapshot", "rows": source_rows,
        "association_basis": association_basis, "omissions": source_omissions,
    }
    source["sha256"] = _hash({
        "workspace_id": "w-1", "run_id": "run-1", "rows": source_rows,
        "association_basis": association_basis, "omissions": source_omissions,
    })
    no_send_claim = stream_state == "pending" or error_code == "stream_not_started"
    prior_send = bool(stream_claimed_at or usage or any(step["kind"] == "model" for step in steps)
                      or dispatches)
    conflicts = []
    if no_send_claim and prior_send:
        conflicts.append("not_sent_claim_conflicts_with_recorded_activity")
    if stream_state == "pending" and run_status != "running":
        conflicts.append("pending_stream_has_terminal_run_status")
    if stream_state == "claimed" and run_status != "running":
        conflicts.append("claimed_stream_has_terminal_run_status")
    if stream_state == "closed" and run_status == "running":
        conflicts.append("closed_stream_has_running_run_status")
    if error_code == "stream_not_started" and (stream_state != "closed" or run_status != "failed"):
        conflicts.append("not_started_terminal_state_conflict")
    stream_request_state = (
        "NOT_STREAM" if stream_state is None else
        "INCONSISTENT" if conflicts else
        "NOT_SENT" if no_send_claim else
        "MAY_HAVE_SENT" if stream_state in {"claimed", "closed"} else "UNKNOWN"
    )
    in_flight_model = sorted(item["attempt_id"] for item in usage
                             if item["usage_status"] == "IN_FLIGHT")
    unknown_usage = sorted(item["attempt_id"] for item in usage
                           if item["usage_status"] == "UNKNOWN")
    invalid_usage = sorted(item["attempt_id"] for item in usage
                           if item["usage_status"] == "INVALID")
    step_by_id = {step["id"]: step for step in steps}

    def lifecycle_conflict(item: dict) -> bool:
        step = step_by_id.get(item["step_id"])
        if step is not None and step["kind"] != "model":
            return True
        if item["usage_status"] == "IN_FLIGHT":
            return (
                run_status != "running" or item["termination"] is not None
                or item["prompt_tokens"] is not None
                or item["completion_tokens"] is not None
                or item["finalized_at"] is not None or item["finalization_hash_present"]
                or (step is not None and step["status"] != "running")
            )
        expected_step = {
            "COMPLETED": "succeeded",
            "CANCELLED_OR_TIMEOUT": "cancelled",
            "TRUNCATED": "failed",
            "STREAM_INTERRUPTED": "failed",
            "NOT_SENT": "failed",
            "FAILED_OR_INDETERMINATE": "failed",
        }.get(item["termination"])
        return (
            item["termination"] is None or item["finalized_at"] is None
            or not item["finalization_hash_present"]
            or (step is not None and step["status"] == "running")
            or (item["usage_status"] == "UNKNOWN"
                and item["prompt_tokens"] is not None
                and item["completion_tokens"] is not None)
            or (item["usage_status"] == "INVALID"
                and (item["prompt_tokens"] is not None
                     or item["completion_tokens"] is not None))
            or expected_step is None
            or (step is not None and step["status"] != expected_step)
        )

    lifecycle_conflicts = sorted(
        item["attempt_id"] for item in usage if lifecycle_conflict(item)
    )
    orphan_usage = sorted(item["attempt_id"] for item in usage
                          if item["step_id"] not in {step["id"] for step in steps})
    usage_conflicts = sorted(item["attempt_id"] for item in usage
                             if item["usage_status"] == "REPORTED" and
                             (item["prompt_tokens"] is None or item["completion_tokens"] is None))
    pending = sorted(
        [f"dispatch:{item['id']}" for item in dispatches
         if item["state"] in {"dispatching", "indeterminate"}]
        + [f"model_call:{item}" for item in in_flight_model]
        + [f"usage_lifecycle_conflict:{item}" for item in lifecycle_conflicts]
        + [f"usage_orphan:{item}" for item in orphan_usage]
        + [f"usage_conflict:{item}" for item in usage_conflicts]
        + (["stream:run-1"] if stream_state == "claimed" or
           error_code == "stream_execution_indeterminate" or conflicts else [])
    )
    if (error_code == "stream_execution_indeterminate" or conflicts or orphan_usage
            or usage_conflicts or lifecycle_conflicts):
        run["effective_status"] = "indeterminate"
    body = {
        "contract": "HostLedgerProjection.v0", "candidate_only": True,
        "workspace_id": "w-1", "run_id": "run-1", "source": source,
        **groups,
        "signals": {
            "stream_request_state": stream_request_state,
            "stream_conflicts": conflicts, "pending_operations": pending,
            "in_flight_model_attempt_ids": in_flight_model,
            "unknown_usage_attempt_ids": unknown_usage,
            "invalid_usage_attempt_ids": invalid_usage,
            "usage_lifecycle_conflict_attempt_ids": lifecycle_conflicts,
            "orphan_usage_attempt_ids": orphan_usage,
            "usage_conflict_attempt_ids": usage_conflicts,
            "orphan_claimed_invocation_ids": [],
            "verified_evidence": [], "goal_binding": "NATIVE_UNBOUND",
        },
        "omissions": [*source_omissions, *_EXTRA_OMISSIONS],
        "authority_effects_of_projection": {
            "qualification_receipt_created": False,
            "current_use_binding_created": False,
            "authorization_grant_created": False,
            "dispatch_performed": False,
        },
    }
    return {**body, "sha256": _hash(body)}


def _replay(projection: dict, *, previous_sequence: int = 0) -> dict:
    goal = _goal()
    return replay_host_projection(
        goal, projection,
        expected_goal_sha256=digest(goal),
        expected_projection_sha256=projection["sha256"],
        previous_sequence=previous_sequence,
    )


def test_bio_like_activity_rises_without_projected_verified_evidence() -> None:
    steps = [_step(sequence) for sequence in range(1, 13)]
    artifacts = [
        {"id": f"summary-{sequence}", "step_id": f"step-{sequence}",
         "kind": "research-summary", "content_hash": _hash({"summary": sequence}),
         "recorded_at": _T1, "candidate_only": True,
         "source_sha256": _hash({"native-artifact": sequence})}
        for sequence in (4, 8, 12)
    ]
    projection = _projection(steps=steps, artifacts=artifacts)
    result = _replay(projection, previous_sequence=1)
    assert result == _replay(projection, previous_sequence=1)
    assert result["goal_binding"] == "HOST_ASSERTED_NOT_NATIVE"
    assert result["progress_assessment"]["E"] == {
        "status": "NOT_EVALUATED", "projected_verified_count": 0,
        "candidate_artifact_counted": False,
        "requested_criteria": ["authenticated-parent-lineage"],
    }
    assert result["progress_assessment"]["B"]["recorded_steps_in_window"] == 11
    assert result["progress_assessment"]["B"]["candidate_artifacts_in_window"] == 3
    assert result["progress_assessment"]["completion_evaluated"] is False
    assert result["recovery_decision"]["action"] == "REASSESS"
    assert result["recovery_decision"]["execution_authorized"] is False


def test_not_started_stream_has_no_fabricated_model_request_or_usage() -> None:
    projection = _projection(
        stream_state="closed", error_code="stream_not_started", run_status="failed",
    )
    result = _replay(projection)
    assert result["run"]["stream_request_state"] == "NOT_SENT"
    assert result["progress_assessment"]["B"]["model_attempts_in_window"] == 0
    assert result["recovery_decision"]["action"] == "PAUSE"


def test_single_snapshot_does_not_mislabel_late_artifact_as_new_step_activity() -> None:
    artifact = {
        "id": "late-artifact", "step_id": "step-1", "kind": "research-summary",
        "content_hash": _hash({"summary": "arrived after first checkpoint"}),
        "recorded_at": _T1, "candidate_only": True,
        "source_sha256": _hash({"native-artifact": "late"}),
    }
    projection = _projection(steps=[_step(1)], artifacts=[artifact])
    result = _replay(projection, previous_sequence=1)
    assert result["window"]["basis"] == "CURRENT_SNAPSHOT_STEP_SEQUENCE_NOT_TEMPORAL_DELTA"
    assert result["window"]["candidate_artifact_refs"] == []
    assert result["recovery_decision"]["action"] == "PAUSE"
    assert {"source": "cross_snapshot_row_delta",
            "reason": "single_snapshot_cannot_identify_late_updates_to_existing_steps"} in (
                projection["omissions"]
            )


def test_receipt_scoped_invalidation_keeps_its_exact_target() -> None:
    projection = _projection(steps=[_step(1)])
    decision = {
        "id": "decision-1", "target_claim_revision_id": "claim-1",
        "target_claim_semantic_hash": "a" * 64,
        "target_scope": "receipt", "target_receipt_id": "qualification-1",
        "target_attempt_id": None, "reason_code": "RECEIPT_DEFECT",
        "effect_state": "stale", "affected_binding_ids": ["binding-1"],
        "current_effect_status": "NOT_EVALUATED_BY_PROJECTION",
        "source_sha256": _hash({"native-decision": 1}),
    }
    projection["invalidation_decisions"].append(decision)
    projection["source"]["rows"]["invalidation_decisions"].append({
        "id": decision["id"], "sha256": decision["source_sha256"],
    })
    projection["source"]["sha256"] = _hash({
        "workspace_id": "w-1", "run_id": "run-1",
        "rows": projection["source"]["rows"],
        "association_basis": projection["source"]["association_basis"],
        "omissions": projection["source"]["omissions"],
    })
    projection["sha256"] = _hash({
        key: value for key, value in projection.items() if key != "sha256"
    })
    assert _replay(projection)["recovery_decision"]["execution_authorized"] is False

    forged = deepcopy(projection)
    forged["invalidation_decisions"][0]["target_scope"] = "claim_revision"
    forged["sha256"] = _hash({
        key: value for key, value in forged.items() if key != "sha256"
    })
    with pytest.raises(ContractError, match="target IDs conflict"):
        _replay(forged)


def test_indeterminate_stream_and_unknown_usage_pause_without_retry() -> None:
    usage = [{
        "attempt_id": "attempt-1", "step_id": "step-1",
        "usage_status": "UNKNOWN", "termination": "STREAM_INTERRUPTED",
        "prompt_tokens": None, "completion_tokens": None,
        "finalized_at": _T1, "finalization_hash_present": True,
        "source_sha256": _hash({"native-usage": 1}),
    }]
    failed_model_step = _step(1, "model")
    failed_model_step["status"] = "failed"
    projection = _projection(
        steps=[failed_model_step], usage=usage,
        stream_state="closed", error_code="stream_execution_indeterminate",
        run_status="failed", stream_claimed_at=_T0,
    )
    result = _replay(projection)
    assert result["run"]["effective_status"] == "indeterminate"
    assert result["window"]["model_usage_refs"][0]["usage_status"] == "UNKNOWN"
    assert result["recovery_decision"]["action"] == "PAUSE"
    assert "stream:run-1" in result["progress_assessment"]["U"]["pending_operations"]


def test_in_flight_model_call_requires_reconciliation() -> None:
    model_step = _step(1, "model")
    model_step["status"] = "running"
    usage = [{
        "attempt_id": "attempt-1", "step_id": model_step["id"],
        "usage_status": "IN_FLIGHT", "termination": None,
        "prompt_tokens": None, "completion_tokens": None,
        "finalized_at": None, "finalization_hash_present": False,
        "source_sha256": _hash({"native-usage": "in-flight"}),
    }]
    projection = _projection(steps=[model_step], usage=usage)
    assert projection["signals"]["in_flight_model_attempt_ids"] == ["attempt-1"]
    assert projection["signals"]["unknown_usage_attempt_ids"] == []

    result = _replay(projection)
    assert result["run"]["effective_status"] == "running"
    assert result["recovery_decision"]["action"] == "PAUSE"
    assert result["progress_assessment"]["U"]["pending_operations"] == [
        "model_call:attempt-1"
    ]
    assert result["recovery_decision"]["execution_authorized"] is False


def test_terminal_unknown_usage_does_not_imply_active_model_call_or_retry() -> None:
    usage = [{
        "attempt_id": "attempt-1", "step_id": "step-1",
        "usage_status": "UNKNOWN", "termination": "COMPLETED",
        "prompt_tokens": None, "completion_tokens": None,
        "finalized_at": _T1, "finalization_hash_present": True,
        "source_sha256": _hash({"native-usage": "unknown"}),
    }]
    projection = _projection(
        steps=[_step(1, "model")], usage=usage, run_status="succeeded",
    )
    assert projection["signals"]["unknown_usage_attempt_ids"] == ["attempt-1"]
    assert projection["signals"]["in_flight_model_attempt_ids"] == []
    assert projection["signals"]["pending_operations"] == []

    result = _replay(projection)
    assert result["run"]["effective_status"] == "succeeded"
    assert result["progress_assessment"]["U"]["pending_operations"] == []
    assert result["window"]["model_usage_refs"][0]["usage_status"] == "UNKNOWN"
    assert result["recovery_decision"]["state"] == "PROPOSED"
    assert result["recovery_decision"]["action"] == "REASSESS"
    assert result["recovery_decision"]["execution_authorized"] is False


def test_succeeded_run_with_in_flight_call_is_indeterminate() -> None:
    model_step = _step(1, "model")
    model_step["status"] = "running"
    usage = [{
        "attempt_id": "attempt-1", "step_id": model_step["id"],
        "usage_status": "IN_FLIGHT", "termination": None,
        "prompt_tokens": None, "completion_tokens": None,
        "finalized_at": None, "finalization_hash_present": False,
        "source_sha256": _hash({"native-usage": "unfinished"}),
    }]
    projection = _projection(
        steps=[model_step], usage=usage, run_status="succeeded",
    )
    assert projection["signals"]["usage_lifecycle_conflict_attempt_ids"] == [
        "attempt-1"
    ]
    result = _replay(projection)
    assert result["run"]["status"] == "succeeded"
    assert result["run"]["effective_status"] == "indeterminate"
    assert result["recovery_decision"]["action"] == "PAUSE"
    assert "model_call:attempt-1" in result["progress_assessment"]["U"]["pending_operations"]

    forged = deepcopy(projection)
    forged["run"]["effective_status"] = "succeeded"
    forged["sha256"] = _hash({key: value for key, value in forged.items() if key != "sha256"})
    with pytest.raises(ContractError, match="effective status"):
        _replay(forged)


@pytest.mark.parametrize("defect", [
    "in_flight_terminal_step",
    "in_flight_terminal_fields",
    "in_flight_with_tokens",
    "in_flight_non_model_step",
    "terminal_missing_termination",
    "terminal_missing_finalized_at",
    "terminal_missing_finalization_hash",
    "terminal_running_step",
    "completed_termination_failed_step",
    "unknown_with_tokens",
    "invalid_with_tokens",
])
def test_usage_lifecycle_conflicts_pause_replay(defect: str) -> None:
    in_flight = defect.startswith("in_flight")
    model_step = _step(1, "model")
    model_step["status"] = "running" if in_flight else "succeeded"
    usage_status = "IN_FLIGHT" if in_flight else (
        "INVALID" if defect == "invalid_with_tokens" else "UNKNOWN"
    )
    usage = {
        "attempt_id": "attempt-1", "step_id": model_step["id"],
        "usage_status": usage_status,
        "termination": None if in_flight else "COMPLETED",
        "prompt_tokens": None, "completion_tokens": None,
        "finalized_at": None if in_flight else _T1,
        "finalization_hash_present": not in_flight,
        "source_sha256": _hash({"native-usage": defect}),
    }
    if defect == "in_flight_terminal_step":
        model_step["status"] = "succeeded"
    elif defect == "in_flight_terminal_fields":
        usage["termination"] = "COMPLETED"
        usage["finalized_at"] = _T1
        usage["finalization_hash_present"] = True
    elif defect == "in_flight_with_tokens":
        usage["prompt_tokens"] = 1
    elif defect == "in_flight_non_model_step":
        model_step["kind"] = "tool"
    elif defect == "terminal_missing_termination":
        usage["termination"] = None
    elif defect == "terminal_missing_finalized_at":
        usage["finalized_at"] = None
    elif defect == "terminal_missing_finalization_hash":
        usage["finalization_hash_present"] = False
    elif defect == "terminal_running_step":
        model_step["status"] = "running"
    elif defect == "completed_termination_failed_step":
        model_step["status"] = "failed"
    elif defect in {"unknown_with_tokens", "invalid_with_tokens"}:
        usage["prompt_tokens"] = 1
        usage["completion_tokens"] = 2

    projection = _projection(steps=[model_step], usage=[usage])
    assert projection["signals"]["usage_lifecycle_conflict_attempt_ids"] == [
        "attempt-1"
    ]
    assert "usage_lifecycle_conflict:attempt-1" in projection["signals"]["pending_operations"]
    result = _replay(projection)
    assert result["run"]["effective_status"] == "indeterminate"
    assert result["recovery_decision"]["action"] == "PAUSE"
    assert result["recovery_decision"]["execution_authorized"] is False
    assert ("model_call:attempt-1" in result["progress_assessment"]["U"][
        "pending_operations"
    ]) is in_flight


def test_indeterminate_dispatch_pauses() -> None:
    dispatch = {
        "id": "dispatch-1", "state": "indeterminate", "step_id": "step-1",
        "updated_at": _T1, "source_sha256": _hash({"native-dispatch": 1}),
    }
    projection = _projection(steps=[_step(1, "tool")], dispatches=[dispatch])
    result = _replay(projection)
    assert result["recovery_decision"]["action"] == "PAUSE"
    assert "dispatch:dispatch-1" in result["progress_assessment"]["U"]["pending_operations"]


def test_not_sent_claim_with_recorded_model_activity_is_indeterminate() -> None:
    projection = _projection(steps=[_step(1, "model")], stream_state="pending")
    result = _replay(projection)
    assert result["run"]["stream_request_state"] == "INCONSISTENT"
    assert result["run"]["effective_status"] == "indeterminate"
    assert result["recovery_decision"]["action"] == "PAUSE"


@pytest.mark.parametrize("stream_state,run_status,conflict", [
    ("claimed", "failed", "claimed_stream_has_terminal_run_status"),
    ("closed", "running", "closed_stream_has_running_run_status"),
])
def test_stream_lifecycle_conflicts_pause_replay(
    stream_state: str, run_status: str, conflict: str,
) -> None:
    projection = _projection(
        stream_state=stream_state, run_status=run_status,
        stream_claimed_at=_T0 if stream_state == "claimed" else None,
    )
    assert conflict in projection["signals"]["stream_conflicts"]
    result = _replay(projection)
    assert result["run"]["stream_request_state"] == "INCONSISTENT"
    assert result["run"]["effective_status"] == "indeterminate"
    assert result["recovery_decision"]["action"] == "PAUSE"
    assert "stream:run-1" in result["progress_assessment"]["U"]["pending_operations"]


def test_missing_referenced_grant_row_pauses() -> None:
    projection = _projection(steps=[_step(1, "tool")])
    invocation = {
        "id": "invocation-1", "grant_id": "missing-grant", "dispatch_id": None,
        "claimed_at": None, "source_sha256": _hash({"native-invocation": 1}),
    }
    projection["authorization_invocations"].append(invocation)
    projection["source"]["rows"]["authorization_invocations"].append({
        "id": invocation["id"], "sha256": invocation["source_sha256"],
    })
    projection["source"]["association_basis"]["grant_related_via_consumed_invocation"] = [
        "missing-grant",
    ]
    projection["source"]["omissions"].append({
        "source": "authorization_grants", "reason": "referenced_grant_row_missing",
        "ids": ["missing-grant"],
    })
    projection["omissions"] = [*projection["source"]["omissions"], *_EXTRA_OMISSIONS]
    projection["signals"]["pending_operations"] = ["invocation:invocation-1"]
    projection["source"]["sha256"] = _hash({
        "workspace_id": "w-1", "run_id": "run-1",
        "rows": projection["source"]["rows"],
        "association_basis": projection["source"]["association_basis"],
        "omissions": projection["source"]["omissions"],
    })
    projection["sha256"] = _hash({
        key: value for key, value in projection.items() if key != "sha256"
    })
    result = _replay(projection)
    assert result["recovery_decision"]["action"] == "PAUSE"
    assert "missing_grant:missing-grant" in result["progress_assessment"]["U"]["pending_operations"]


def test_rejects_unpinned_projection_forged_completion_and_authority() -> None:
    projection = _projection(steps=[_step(1)])
    goal = _goal()
    with pytest.raises(ContractError, match="Pinned host projection digest mismatch"):
        replay_host_projection(
            goal, projection, expected_goal_sha256=digest(goal),
            expected_projection_sha256="0" * 64,
        )
    forged = deepcopy(projection)
    forged["steps"][0]["completed_at"] = _T1
    forged["sha256"] = _hash({key: value for key, value in forged.items() if key != "sha256"})
    with pytest.raises(ContractError, match="completion is not recorded"):
        _replay(forged)
    forged = deepcopy(projection)
    forged["signals"]["verified_evidence"] = ["summary-1"]
    forged["sha256"] = _hash({key: value for key, value in forged.items() if key != "sha256"})
    with pytest.raises(ContractError, match="signals conflict"):
        _replay(forged)


def test_rejects_source_reference_and_workspace_goal_mismatch() -> None:
    projection = _projection(steps=[_step(1)])
    forged = deepcopy(projection)
    forged["source"]["rows"]["steps"][0]["sha256"] = "a" * 64
    forged["source"]["sha256"] = _hash({
        "workspace_id": "w-1", "run_id": "run-1",
        "rows": forged["source"]["rows"],
        "association_basis": forged["source"]["association_basis"],
        "omissions": forged["source"]["omissions"],
    })
    forged["sha256"] = _hash({key: value for key, value in forged.items() if key != "sha256"})
    with pytest.raises(ContractError, match="Source references differ"):
        _replay(forged)
    goal = _goal()
    goal["protocol"]["workspace_id"] = "another-workspace"
    with pytest.raises(ContractError, match="different workspaces"):
        replay_host_projection(
            goal, projection, expected_goal_sha256=digest(goal),
            expected_projection_sha256=projection["sha256"],
        )
