"""Read-only, source-bound projection of one native Run ledger.

This is an execution-fact view, not a domain verifier or an authority view.
In particular, caller-writable Step/Receipt metadata never becomes a verified
observation, and a stored current-use row is not a fresh qualification check.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..core.contracts import RequestContext
from ..core.errors import ResourceNotFoundError
from ..core.goal_binding import assess_native_goal_binding
from ..database import get_repository
from ..repository import Repository

RepositoryProvider = Callable[[], Repository]

_DIRECT_RUN_ROWS = (
    "steps",
    "model_usage_receipts",
    "artifacts",
    "execution_receipts",
    "enforcement_dispatches",
    "authorization_invocations",
    "research_verification_attempts",
)
_RELATED_ROWS = (
    "invalidation_decisions", "current_use_bindings",
    "authorization_grants", "knowledge_admission_receipts",
)
_ROW_ID = {"model_usage_receipts": "attempt_id"}


def _plain(value: Any) -> Any:
    """Normalize SQL driver values before hashing, without parsing untrusted JSON."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not (-float("inf") < value < float("inf")):
            raise ValueError("Non-finite ledger value")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite ledger decimal")
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    raise TypeError(f"Unsupported ledger value type: {type(value).__name__}")


def _hash(value: Any) -> str:
    encoded = json.dumps(
        _plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _row_refs(rows: list[dict[str, Any]], id_field: str = "id") -> list[dict[str, str]]:
    return [
        {"id": str(row[id_field]), "sha256": _hash(row)}
        for row in rows
    ]


def _recorded_id_list(raw: str) -> list[str]:
    """Decode a native decision's ID list without accepting malformed storage."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Invalid recorded decision ID list") from exc
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError("Invalid recorded decision ID list")
    return value


def _usage_lifecycle_conflict(
    item: dict[str, Any], step: dict[str, Any] | None, run_status: str,
) -> bool:
    """Flag impossible receipt/Step combinations without guessing provider state."""
    status = item["usage_status"]
    prompt = item.get("prompt_tokens")
    completion = item.get("completion_tokens")
    termination = item.get("termination")
    if step is not None and step["kind"] != "model":
        return True
    if status == "IN_FLIGHT":
        return (
            run_status != "running" or prompt is not None or completion is not None
            or item.get("finalized_at") is not None
            or item.get("finalization_hash") is not None
            or termination is not None
            or (step is not None and step["status"] != "running")
        )
    if (
        item.get("finalized_at") is None
        or item.get("finalization_hash") is None
        or termination is None
        or (step is not None and step["status"] == "running")
    ):
        return True
    if status == "UNKNOWN" and prompt is not None and completion is not None:
        return True
    if status == "INVALID" and (prompt is not None or completion is not None):
        return True
    expected_step = {
        "COMPLETED": "succeeded",
        "CANCELLED_OR_TIMEOUT": "cancelled",
        "TRUNCATED": "failed",
        "STREAM_INTERRUPTED": "failed",
        "NOT_SENT": "failed",
        "FAILED_OR_INDETERMINATE": "failed",
    }.get(termination)
    return expected_step is None or (step is not None and step["status"] != expected_step)


class LedgerProjectionService:
    """Export native facts from a single DB snapshot without changing any ledger."""

    def __init__(
        self, *, repository_provider: RepositoryProvider = get_repository,
    ) -> None:
        self._repository_provider = repository_provider

    def export_run(self, context: RequestContext, run_id: str) -> dict[str, Any]:
        snapshot = self._repository_provider().read_run_ledger_snapshot(
            context.workspace_id, run_id
        )
        if snapshot is None:
            raise ResourceNotFoundError("run", run_id)
        return project_run_snapshot(snapshot, context.workspace_id, run_id)


def project_run_snapshot(
    snapshot: dict[str, Any], workspace_id: str, run_id: str,
) -> dict[str, Any]:
    """Project one repository-owned snapshot; do not trust caller-provided facts."""
    run = snapshot["run"]
    if (
        snapshot["tenant_id"] != workspace_id
        or snapshot["run_id"] != run_id
        or run["tenant_id"] != workspace_id
        or run["id"] != run_id
    ):
        raise ValueError("Cross-workspace or cross-Run ledger snapshot")
    for group in _DIRECT_RUN_ROWS:
        for row in snapshot[group]:
            if row["tenant_id"] != workspace_id or row["run_id"] != run_id:
                raise ValueError(f"Cross-workspace or cross-Run {group} row")
    for group in _RELATED_ROWS:
        for row in snapshot[group]:
            if row["tenant_id"] != workspace_id:
                raise ValueError(f"Cross-workspace {group} row")
    for row in snapshot.get("goal_protocols", []):
        if row["tenant_id"] != workspace_id:
            raise ValueError("Cross-workspace goal Protocol row")

    goal_status, native_goal = assess_native_goal_binding(
        run,
        snapshot.get("goal_protocols", [None])[0]
        if snapshot.get("goal_protocols") else None,
        workspace_id,
    )
    goal_requested = goal_status != "NATIVE_UNBOUND"

    source_rows = {
        "agent_runs": _row_refs([run]),
        **{
            group: _row_refs(snapshot[group], _ROW_ID.get(group, "id"))
            for group in (*_DIRECT_RUN_ROWS, *_RELATED_ROWS)
        },
    }
    if goal_requested:
        source_rows["goal_protocols"] = _row_refs(snapshot["goal_protocols"])
    source = {
        "kind": "repository_snapshot",
        "rows": source_rows,
        "association_basis": snapshot["association_basis"],
        "omissions": snapshot["omissions"],
        "sha256": _hash({
            "workspace_id": workspace_id,
            "run_id": run_id,
            "rows": source_rows,
            "association_basis": snapshot["association_basis"],
            "omissions": snapshot["omissions"],
        }),
    }
    steps = snapshot["steps"]
    usage = snapshot["model_usage_receipts"]
    step_ids = {step["id"] for step in steps}
    step_by_id = {step["id"]: step for step in steps}
    orphan_usage = sorted(
        receipt["attempt_id"] for receipt in usage
        if receipt["step_id"] not in step_ids
    )
    usage_conflicts = sorted(
        item["attempt_id"] for item in usage
        if item["usage_status"] == "REPORTED" and (
            item["prompt_tokens"] is None or item["completion_tokens"] is None
        )
    )
    in_flight_model = sorted(
        item["attempt_id"] for item in usage
        if item["usage_status"] == "IN_FLIGHT"
    )
    unknown_usage = sorted(
        item["attempt_id"] for item in usage
        if item["usage_status"] == "UNKNOWN"
    )
    invalid_usage = sorted(
        item["attempt_id"] for item in usage
        if item["usage_status"] == "INVALID"
    )
    usage_lifecycle_conflicts = sorted(
        item["attempt_id"] for item in usage
        if _usage_lifecycle_conflict(item, step_by_id.get(item["step_id"]), run["status"])
    )
    no_send_claim = (
        run.get("stream_state") == "pending"
        or run.get("error_code") == "stream_not_started"
    )
    prior_send_evidence = bool(
        run.get("stream_claimed_at")
        or usage
        or any(step["kind"] == "model" for step in steps)
        or snapshot["enforcement_dispatches"]
        or snapshot["authorization_invocations"]
    )
    stream_conflicts: list[str] = []
    if no_send_claim and prior_send_evidence:
        stream_conflicts.append("not_sent_claim_conflicts_with_recorded_activity")
    if run.get("stream_state") == "pending" and run["status"] != "running":
        stream_conflicts.append("pending_stream_has_terminal_run_status")
    if run.get("stream_state") == "claimed" and run["status"] != "running":
        stream_conflicts.append("claimed_stream_has_terminal_run_status")
    if run.get("stream_state") == "closed" and run["status"] == "running":
        stream_conflicts.append("closed_stream_has_running_run_status")
    if (run.get("error_code") == "stream_not_started"
            and (run.get("stream_state") != "closed" or run["status"] != "failed")):
        stream_conflicts.append("not_started_terminal_state_conflict")
    if run.get("stream_state") is None:
        stream_request_state = "NOT_STREAM"
    elif stream_conflicts:
        stream_request_state = "INCONSISTENT"
    elif no_send_claim:
        stream_request_state = "NOT_SENT"
    elif run.get("stream_state") in {"claimed", "closed"}:
        stream_request_state = "MAY_HAVE_SENT"
    else:
        stream_request_state = "UNKNOWN"

    pending_dispatches = sorted(
        item["id"] for item in snapshot["enforcement_dispatches"]
        if item["state"] in {"dispatching", "indeterminate"}
    )
    unclaimed_invocations = sorted(
        item["id"] for item in snapshot["authorization_invocations"]
        if item["dispatch_id"] is None
    )
    dispatch_ids = {item["id"] for item in snapshot["enforcement_dispatches"]}
    orphan_claimed_invocations = sorted(
        item["id"] for item in snapshot["authorization_invocations"]
        if item["dispatch_id"] is not None and item["dispatch_id"] not in dispatch_ids
    )
    pending_operations = sorted(
        [f"dispatch:{value}" for value in pending_dispatches]
        + [f"invocation:{value}" for value in unclaimed_invocations]
        + [f"invocation_orphan:{value}" for value in orphan_claimed_invocations]
        + [f"model_call:{value}" for value in in_flight_model]
        + [f"usage_orphan:{value}" for value in orphan_usage]
        + [f"usage_conflict:{value}" for value in usage_conflicts]
        + [f"usage_lifecycle_conflict:{value}" for value in usage_lifecycle_conflicts]
    )
    if goal_status == "INCONSISTENT":
        pending_operations.append(f"goal_binding:{run_id}")
        pending_operations.sort()
    if (run.get("stream_state") == "claimed"
            or run.get("error_code") == "stream_execution_indeterminate"
            or stream_conflicts):
        pending_operations.append(f"stream:{run_id}")
        pending_operations.sort()
    if (run.get("error_code") == "stream_execution_indeterminate"
            or stream_conflicts or orphan_usage or usage_conflicts
            or usage_lifecycle_conflicts
            or orphan_claimed_invocations or goal_status == "INCONSISTENT"):
        effective_status = "indeterminate"
    else:
        effective_status = run["status"]

    body: dict[str, Any] = {
        "contract": "HostLedgerProjection.v1" if goal_requested else "HostLedgerProjection.v0",
        "candidate_only": True,
        "workspace_id": workspace_id,
        "run_id": run_id,
        "source": source,
        "run": {
            "id": run_id, "status": run["status"],
            "effective_status": effective_status,
            "stream_state": run.get("stream_state"),
            "stream_start_deadline_at": run.get("stream_start_deadline_at"),
            "stream_claimed_at": run.get("stream_claimed_at"),
            "stream_lease_until": run.get("stream_lease_until"),
            "stream_cancel_requested_at": run.get("stream_cancel_requested_at"),
            "error_code": run.get("error_code"),
            "request_id": run["request_id"],
            "created_at": run["created_at"],
            "completed_at": run.get("completed_at"),
            "source_sha256": _hash(run),
        },
        "steps": [{
            "id": step["id"], "sequence": step["sequence"],
            "kind": step["kind"], "status": step["status"],
            "recorded_at": step["created_at"],
            "completed_at": None,
            "source_sha256": _hash(step),
        } for step in steps],
        "model_usage": [{
            "attempt_id": item["attempt_id"], "step_id": item["step_id"],
            "usage_status": item["usage_status"],
            "termination": item.get("termination"),
            "prompt_tokens": item.get("prompt_tokens"),
            "completion_tokens": item.get("completion_tokens"),
            "finalized_at": item.get("finalized_at"),
            "finalization_hash_present": item.get("finalization_hash") is not None,
            "source_sha256": _hash(item),
        } for item in usage],
        "artifacts": [{
            "id": item["id"], "step_id": item.get("step_id"),
            "kind": item["kind"], "content_hash": item["content_hash"],
            "recorded_at": item["created_at"], "candidate_only": True,
            "source_sha256": _hash(item),
        } for item in snapshot["artifacts"]],
        "execution_receipts": [{
            "id": item["id"], "status": item["status"],
            "protocol_id": item["protocol_id"],
            "recorded_at": item["created_at"],
            "fact_status": "UNVERIFIED_CALLER_METADATA",
            "source_sha256": _hash(item),
        } for item in snapshot["execution_receipts"]],
        "enforcement_dispatches": [{
            "id": item["id"], "state": item["state"],
            "step_id": item.get("step_id"),
            "updated_at": item["updated_at"],
            "source_sha256": _hash(item),
        } for item in snapshot["enforcement_dispatches"]],
        "authorization_invocations": [{
            "id": item["id"], "grant_id": item["grant_id"],
            "dispatch_id": item["dispatch_id"],
            "claimed_at": item["claimed_at"],
            "source_sha256": _hash(item),
        } for item in snapshot["authorization_invocations"]],
        "authorization_grants": [{
            "id": item["id"], "stored_state": item["state"],
            "calls_used": item["calls_used"],
            "expires_at": item["expires_at"],
            "current_authority_status": "NOT_EVALUATED_BY_PROJECTION",
            "source_sha256": _hash(item),
        } for item in snapshot["authorization_grants"]],
        "research_verification_attempts": [{
            "id": item["id"], "claim_revision_id": item["claim_revision_id"],
            "domain_status": "NOT_EVALUATED_BY_PROJECTION",
            "source_sha256": _hash(item),
        } for item in snapshot["research_verification_attempts"]],
        "current_use_bindings": [{
            "id": item["id"], "claim_revision_id": item["claim_revision_id"],
            "profile_id": item["profile_id"], "use_scope": item["use_scope"],
            "qualification_receipt_id": item["qualification_receipt_id"],
            "knowledge_admission_receipt_id": item.get("knowledge_admission_receipt_id"),
            "stored_state": item["state"],
            "freshness_status": "NOT_EVALUATED_BY_PROJECTION",
            "source_sha256": _hash(item),
        } for item in snapshot["current_use_bindings"]],
        "knowledge_admission_receipts": [{
            "id": item["id"], "claim_revision_id": item["claim_revision_id"],
            "qualification_receipt_id": item["qualification_receipt_id"],
            "current_use_status": "NOT_EVALUATED_BY_PROJECTION",
            "source_sha256": _hash(item),
        } for item in snapshot["knowledge_admission_receipts"]],
        "invalidation_decisions": [{
            "id": item["id"], "target_claim_revision_id": item["target_claim_revision_id"],
            "target_claim_semantic_hash": item["target_claim_semantic_hash"],
            "target_scope": item["target_scope"],
            "target_receipt_id": item["target_receipt_id"],
            "target_attempt_id": item["target_attempt_id"],
            "reason_code": item["reason_code"],
            "effect_state": item["effect_state"],
            "affected_binding_ids": _recorded_id_list(item["affected_binding_ids"]),
            "current_effect_status": "NOT_EVALUATED_BY_PROJECTION",
            "source_sha256": _hash(item),
        } for item in snapshot["invalidation_decisions"]],
        "signals": {
            "stream_request_state": stream_request_state,
            "stream_conflicts": stream_conflicts,
            "pending_operations": pending_operations,
            "in_flight_model_attempt_ids": in_flight_model,
            "unknown_usage_attempt_ids": unknown_usage,
            "invalid_usage_attempt_ids": invalid_usage,
            "orphan_usage_attempt_ids": orphan_usage,
            "usage_conflict_attempt_ids": usage_conflicts,
            "usage_lifecycle_conflict_attempt_ids": usage_lifecycle_conflicts,
            "orphan_claimed_invocation_ids": orphan_claimed_invocations,
            "verified_evidence": [],
            "goal_binding": goal_status,
        },
        "omissions": [*snapshot["omissions"], {
            "source": "verified_evidence",
            "reason": "domain_verification_and_currentness_not_evaluated",
        }, {
            "source": "step_completion",
            "reason": "native_run_steps_have_no_general_completion_timestamp",
        }, {
            "source": "cross_snapshot_row_delta",
            "reason": "single_snapshot_cannot_identify_late_updates_to_existing_steps",
        }],
        "authority_effects_of_projection": {
            "qualification_receipt_created": False,
            "current_use_binding_created": False,
            "authorization_grant_created": False,
            "dispatch_performed": False,
        },
    }
    if goal_requested:
        body["native_goal"] = {
            "status": goal_status,
            "binding": native_goal,
            "source_protocol_row_sha256": (
                _hash(snapshot["goal_protocols"][0])
                if len(snapshot["goal_protocols"]) == 1 else None
            ),
        }
        if goal_status == "INCONSISTENT":
            body["omissions"].append({
                "source": "run_goal_binding",
                "reason": "native_binding_inconsistent",
            })
    return {**body, "sha256": _hash(body)}
