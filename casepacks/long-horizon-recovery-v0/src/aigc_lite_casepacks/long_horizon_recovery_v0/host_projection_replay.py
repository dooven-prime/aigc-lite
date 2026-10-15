"""Candidate-only diagnostic replay over one native HostLedgerProjection.v0.

This is deliberately separate from LongHorizonLedger.v0 replay. Native Run
rows do not bind a frozen goal, Step rows have no completion timestamp, and
the projection does not establish domain verification or current use.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from .replay import ContractError, _validate_goal, digest

_OUTPUT_DOMAIN = b"aigc-lite.host-projection-replay.v0\n"
_HEX = set("0123456789abcdef")
_MAX_BYTES = 8_000_000
_MAX_ROWS = 20_000
_DIRECT_RUN_GROUPS = [
    "steps", "model_usage_receipts", "artifacts", "execution_receipts",
    "enforcement_dispatches", "authorization_invocations",
    "research_verification_attempts",
]
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
_PROJECTION_FIELDS = {
    "contract", "candidate_only", "workspace_id", "run_id", "source",
    "run", "steps", "model_usage", "artifacts", "execution_receipts",
    "enforcement_dispatches", "authorization_invocations", "authorization_grants",
    "research_verification_attempts", "current_use_bindings",
    "knowledge_admission_receipts", "invalidation_decisions", "signals", "omissions",
    "authority_effects_of_projection", "sha256",
}
_NO_AUTHORITY = {
    "qualification_receipt_created": False,
    "current_use_binding_created": False,
    "authorization_grant_created": False,
    "dispatch_performed": False,
}
_EXTRA_OMISSIONS = [
    {"source": "verified_evidence",
     "reason": "domain_verification_and_currentness_not_evaluated"},
    {"source": "step_completion",
     "reason": "native_run_steps_have_no_general_completion_timestamp"},
    {"source": "cross_snapshot_row_delta",
     "reason": "single_snapshot_cannot_identify_late_updates_to_existing_steps"},
]


def _json_bytes(value: Any) -> bytes:
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ContractError("Invalid canonical JSON") from exc
    if len(raw) > _MAX_BYTES:
        raise ContractError("Host projection exceeds size limit")
    return raw


def _plain_hash(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _output_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(_OUTPUT_DOMAIN + _json_bytes(value)).hexdigest()


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "sha256": _output_hash(value)}


def _object(value: Any, fields: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ContractError(f"Malformed {where}")
    return value


def _array(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list) or len(value) > _MAX_ROWS:
        raise ContractError(f"{where} must be a bounded array")
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ContractError(f"{where} must be a nonempty bounded string")
    return value


def _hash(value: Any, where: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - _HEX:
        raise ContractError(f"{where} must be lowercase SHA-256 hex")
    return value


def _time_or_none(value: Any, where: str) -> None:
    if value is None:
        return
    _string(value, where)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{where} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{where} must include a timezone")


def _time(value: Any, where: str) -> None:
    _string(value, where)
    _time_or_none(value, where)


def _refs(projection: dict[str, Any], rows: dict[str, Any]) -> None:
    if set(rows) != set(_GROUPS):
        raise ContractError("Source row groups differ from native projection")
    for group, (field, id_field) in _GROUPS.items():
        projected = [projection[field]] if field == "run" else _array(
            projection[field], f"projection.{field}",
        )
        references = _array(rows[group], f"source.rows.{group}")
        expected = []
        seen: set[str] = set()
        for item in projected:
            if not isinstance(item, dict):
                raise ContractError(f"Malformed projection.{field} item")
            row_id = _string(item.get(id_field), f"{field}.{id_field}")
            source_hash = _hash(item.get("source_sha256"), f"{field}.source_sha256")
            if row_id in seen:
                raise ContractError(f"Duplicate native {group} row identity")
            seen.add(row_id)
            expected.append({"id": row_id, "sha256": source_hash})
        if references != expected:
            raise ContractError(f"Source references differ from projected {group}")


def _validate_projection(projection: Any, expected_sha256: str) -> dict[str, Any]:
    _hash(expected_sha256, "expected_projection_sha256")
    projection = _object(projection, _PROJECTION_FIELDS, "HostLedgerProjection.v0")
    if projection["contract"] != "HostLedgerProjection.v0" or projection["candidate_only"] is not True:
        raise ContractError("Unsupported or authority-bearing host projection")
    if projection["sha256"] != expected_sha256 or _plain_hash({
        key: value for key, value in projection.items() if key != "sha256"
    }) != expected_sha256:
        raise ContractError("Pinned host projection digest mismatch")
    workspace_id = _string(projection["workspace_id"], "workspace_id")
    run_id = _string(projection["run_id"], "run_id")
    source = _object(
        projection["source"],
        {"kind", "rows", "association_basis", "omissions", "sha256"},
        "projection.source",
    )
    if source["kind"] != "repository_snapshot":
        raise ContractError("Unsupported source kind")
    if not isinstance(source["rows"], dict):
        raise ContractError("Malformed source rows")
    _refs(projection, source["rows"])
    source_body = {
        "workspace_id": workspace_id, "run_id": run_id,
        "rows": source["rows"],
        "association_basis": source["association_basis"],
        "omissions": source["omissions"],
    }
    if _plain_hash(source_body) != _hash(source["sha256"], "source.sha256"):
        raise ContractError("Source snapshot digest mismatch")
    basis = _object(
        source["association_basis"],
        {"direct_run_id", "claim_related_via_recorded_attempt",
         "grant_related_via_consumed_invocation",
         "admission_related_via_current_binding"},
        "source association basis",
    )
    if basis["direct_run_id"] != _DIRECT_RUN_GROUPS:
        raise ContractError("Source direct Run association differs from native ledger")
    for field in ("claim_related_via_recorded_attempt",
                  "grant_related_via_consumed_invocation",
                  "admission_related_via_current_binding"):
        values = basis[field]
        if (not isinstance(values, list) or
                any(not isinstance(value, str) or not value for value in values) or
                values != sorted(set(values))):
            raise ContractError(f"Malformed source association basis {field}")
    if projection["omissions"] != [
        *_array(source["omissions"], "source.omissions"), *_EXTRA_OMISSIONS,
    ]:
        raise ContractError("Projection omissions differ from source")
    if projection["authority_effects_of_projection"] != _NO_AUTHORITY:
        raise ContractError("Projection claims an authority effect")

    run = _object(
        projection["run"],
        {"id", "status", "effective_status", "stream_state", "error_code",
         "request_id", "created_at", "completed_at", "stream_claimed_at",
         "stream_start_deadline_at", "stream_lease_until",
         "stream_cancel_requested_at",
         "source_sha256"},
        "projection.run",
    )
    if run["id"] != run_id or run["status"] not in {
        "running", "succeeded", "failed", "cancelled", "limit_reached",
    }:
        raise ContractError("Invalid native Run identity or status")
    if run["stream_state"] not in {None, "pending", "claimed", "closed"}:
        raise ContractError("Unsupported native stream state")
    if run["error_code"] is not None:
        _string(run["error_code"], "run.error_code")
    _string(run["request_id"], "run.request_id")
    _time(run["created_at"], "run.created_at")
    _time_or_none(run["completed_at"], "run.completed_at")
    _time_or_none(run["stream_claimed_at"], "run.stream_claimed_at")
    for field in ("stream_start_deadline_at", "stream_lease_until",
                  "stream_cancel_requested_at"):
        _time_or_none(run[field], f"run.{field}")

    steps = _array(projection["steps"], "steps")
    sequences: set[int] = set()
    last_sequence = 0
    step_by_id: dict[str, dict[str, Any]] = {}
    for step in steps:
        _object(step, {"id", "sequence", "kind", "status", "recorded_at",
                       "completed_at", "source_sha256"}, "step")
        sequence = step["sequence"]
        if type(sequence) is not int or sequence <= last_sequence or sequence in sequences:
            raise ContractError("Native Step sequence is not strictly increasing")
        last_sequence = sequence
        sequences.add(sequence)
        if step["kind"] not in {"agent", "model", "tool", "retrieval"} or step["status"] not in {
            "running", "succeeded", "failed", "cancelled",
        }:
            raise ContractError("Unsupported native Step kind or status")
        _time(step["recorded_at"], "step.recorded_at")
        if step["completed_at"] is not None:
            raise ContractError("Native Step completion is not recorded")
        step_by_id[step["id"]] = step

    in_flight_model: list[str] = []
    unknown_usage: list[str] = []
    invalid_usage: list[str] = []
    orphan_usage: list[str] = []
    usage_conflicts: list[str] = []
    usage_lifecycle_conflicts: list[str] = []
    for usage in _array(projection["model_usage"], "model_usage"):
        _object(usage, {"attempt_id", "step_id", "usage_status", "termination",
                        "prompt_tokens", "completion_tokens", "finalized_at",
                        "finalization_hash_present", "source_sha256"}, "model_usage")
        _string(usage["step_id"], "model_usage.step_id")
        if usage["usage_status"] not in {"IN_FLIGHT", "REPORTED", "UNKNOWN", "INVALID"}:
            raise ContractError("Unsupported model usage status")
        if usage["termination"] is not None:
            _string(usage["termination"], "model_usage.termination")
        _time_or_none(usage["finalized_at"], "model_usage.finalized_at")
        if type(usage["finalization_hash_present"]) is not bool:
            raise ContractError("Invalid model usage finalization marker")
        for token_field in ("prompt_tokens", "completion_tokens"):
            token = usage[token_field]
            if token is not None and (type(token) is not int or token < 0):
                raise ContractError(f"Invalid model usage {token_field}")
        if usage["step_id"] not in step_by_id:
            orphan_usage.append(usage["attempt_id"])
        if usage["usage_status"] == "IN_FLIGHT":
            in_flight_model.append(usage["attempt_id"])
        elif usage["usage_status"] == "UNKNOWN":
            unknown_usage.append(usage["attempt_id"])
        elif usage["usage_status"] == "INVALID":
            invalid_usage.append(usage["attempt_id"])
        if usage["usage_status"] == "REPORTED" and (
            usage["prompt_tokens"] is None or usage["completion_tokens"] is None
        ):
            usage_conflicts.append(usage["attempt_id"])
        step = step_by_id.get(usage["step_id"])
        if usage["usage_status"] == "IN_FLIGHT":
            lifecycle_conflict = (
                run["status"] != "running"
                or usage["prompt_tokens"] is not None
                or usage["completion_tokens"] is not None
                or usage["finalized_at"] is not None
                or usage["finalization_hash_present"]
                or usage["termination"] is not None
                or (step is not None and step["status"] != "running")
            )
        else:
            expected_step = {
                "COMPLETED": "succeeded",
                "CANCELLED_OR_TIMEOUT": "cancelled",
                "TRUNCATED": "failed",
                "STREAM_INTERRUPTED": "failed",
                "NOT_SENT": "failed",
                "FAILED_OR_INDETERMINATE": "failed",
            }.get(usage["termination"])
            lifecycle_conflict = (
                usage["finalized_at"] is None
                or not usage["finalization_hash_present"]
                or usage["termination"] is None
                or (step is not None and step["status"] == "running")
                or (usage["usage_status"] == "UNKNOWN" and
                    usage["prompt_tokens"] is not None and
                    usage["completion_tokens"] is not None)
                or (usage["usage_status"] == "INVALID" and
                    (usage["prompt_tokens"] is not None or
                     usage["completion_tokens"] is not None))
                or expected_step is None
                or (step is not None and step["status"] != expected_step)
            )
        if step is not None and step["kind"] != "model":
            lifecycle_conflict = True
        if lifecycle_conflict:
            usage_lifecycle_conflicts.append(usage["attempt_id"])

    for artifact in _array(projection["artifacts"], "artifacts"):
        _object(artifact, {"id", "step_id", "kind", "content_hash", "recorded_at",
                           "candidate_only", "source_sha256"}, "artifact")
        if artifact["candidate_only"] is not True:
            raise ContractError("Native artifact cannot be promoted by projection")
        if artifact["step_id"] is not None:
            _string(artifact["step_id"], "artifact.step_id")
        _string(artifact["kind"], "artifact.kind")
        _hash(artifact["content_hash"], "artifact.content_hash")
        _time(artifact["recorded_at"], "artifact.recorded_at")

    for receipt in _array(projection["execution_receipts"], "execution_receipts"):
        _object(receipt, {"id", "status", "protocol_id", "recorded_at",
                          "fact_status", "source_sha256"}, "execution_receipt")
        if receipt["fact_status"] != "UNVERIFIED_CALLER_METADATA":
            raise ContractError("Execution Receipt fact promoted without verification")
        _string(receipt["status"], "execution_receipt.status")
        _string(receipt["protocol_id"], "execution_receipt.protocol_id")
        _time(receipt["recorded_at"], "execution_receipt.recorded_at")

    pending: list[str] = []
    for dispatch in _array(projection["enforcement_dispatches"], "enforcement_dispatches"):
        _object(dispatch, {"id", "state", "step_id", "updated_at", "source_sha256"},
                "enforcement_dispatch")
        if dispatch["state"] not in {"dispatching", "terminal", "indeterminate", "reconciled"}:
            raise ContractError("Unsupported enforcement dispatch state")
        if dispatch["step_id"] is not None:
            _string(dispatch["step_id"], "enforcement_dispatch.step_id")
        _time(dispatch["updated_at"], "enforcement_dispatch.updated_at")
        if dispatch["state"] in {"dispatching", "indeterminate"}:
            pending.append(f"dispatch:{dispatch['id']}")
    dispatch_ids = {item["id"] for item in projection["enforcement_dispatches"]}
    orphan_claimed_invocations: list[str] = []
    grant_refs: set[str] = set()
    for invocation in _array(projection["authorization_invocations"], "authorization_invocations"):
        _object(invocation, {"id", "grant_id", "dispatch_id", "claimed_at", "source_sha256"},
                "authorization_invocation")
        grant_refs.add(_string(invocation["grant_id"], "authorization_invocation.grant_id"))
        if invocation["dispatch_id"] is not None:
            _string(invocation["dispatch_id"], "authorization_invocation.dispatch_id")
            if invocation["dispatch_id"] not in dispatch_ids:
                orphan_claimed_invocations.append(invocation["id"])
        else:
            pending.append(f"invocation:{invocation['id']}")
        _time_or_none(invocation["claimed_at"], "authorization_invocation.claimed_at")
    for grant in _array(projection["authorization_grants"], "authorization_grants"):
        _object(grant, {"id", "stored_state", "calls_used", "expires_at",
                        "current_authority_status", "source_sha256"}, "authorization_grant")
        if grant["current_authority_status"] != "NOT_EVALUATED_BY_PROJECTION":
            raise ContractError("Authorization Grant authority projected")
        _string(grant["stored_state"], "authorization_grant.stored_state")
        if type(grant["calls_used"]) is not int or grant["calls_used"] < 0:
            raise ContractError("Invalid stored grant call count")
        _time_or_none(grant["expires_at"], "authorization_grant.expires_at")

    for attempt in _array(projection["research_verification_attempts"], "research_verification_attempts"):
        _object(attempt, {"id", "claim_revision_id", "domain_status", "source_sha256"},
                "research_verification_attempt")
        if attempt["domain_status"] != "NOT_EVALUATED_BY_PROJECTION":
            raise ContractError("Research verification authority projected")
    for binding in _array(projection["current_use_bindings"], "current_use_bindings"):
        _object(binding, {"id", "claim_revision_id", "profile_id", "use_scope",
                          "qualification_receipt_id", "knowledge_admission_receipt_id",
                          "stored_state", "freshness_status", "source_sha256"},
                "current_use_binding")
        if binding["freshness_status"] != "NOT_EVALUATED_BY_PROJECTION":
            raise ContractError("Current-use freshness projected")
        for field in ("claim_revision_id", "profile_id", "use_scope",
                      "qualification_receipt_id", "stored_state"):
            _string(binding[field], f"current_use_binding.{field}")
        if binding["knowledge_admission_receipt_id"] is not None:
            _string(binding["knowledge_admission_receipt_id"],
                    "current_use_binding.knowledge_admission_receipt_id")
    for admission in _array(projection["knowledge_admission_receipts"],
                            "knowledge_admission_receipts"):
        _object(admission, {"id", "claim_revision_id", "qualification_receipt_id",
                            "current_use_status", "source_sha256"},
                "knowledge_admission_receipt")
        if admission["current_use_status"] != "NOT_EVALUATED_BY_PROJECTION":
            raise ContractError("Knowledge admission current use projected")
    for decision in _array(projection["invalidation_decisions"], "invalidation_decisions"):
        _object(decision, {"id", "target_claim_revision_id", "target_claim_semantic_hash",
                           "target_scope", "target_receipt_id", "target_attempt_id",
                           "reason_code", "effect_state", "affected_binding_ids",
                           "current_effect_status", "source_sha256"},
                "invalidation_decision")
        if decision["target_scope"] not in {"claim_revision", "receipt", "proof_attempt"}:
            raise ContractError("Unsupported invalidation target scope")
        _hash(decision["target_claim_semantic_hash"],
              "invalidation_decision.target_claim_semantic_hash")
        for field in ("target_receipt_id", "target_attempt_id"):
            if decision[field] is not None:
                _string(decision[field], f"invalidation_decision.{field}")
        target_scope = decision["target_scope"]
        receipt_present = decision["target_receipt_id"] is not None
        attempt_present = decision["target_attempt_id"] is not None
        if ((target_scope == "claim_revision" and (receipt_present or attempt_present))
                or (target_scope == "receipt" and (not receipt_present or attempt_present))
                or (target_scope == "proof_attempt" and
                    (not receipt_present or not attempt_present))):
            raise ContractError("Invalidation target IDs conflict with recorded scope")
        affected_ids = _array(decision["affected_binding_ids"], "affected_binding_ids")
        if any(not isinstance(item, str) or not item for item in affected_ids):
            raise ContractError("Invalid affected binding ID list")
        if decision["current_effect_status"] != "NOT_EVALUATED_BY_PROJECTION":
            raise ContractError("Invalidation effect promoted by projection")

    if basis["claim_related_via_recorded_attempt"] != sorted({
        item["claim_revision_id"] for item in projection["research_verification_attempts"]
    }) or basis["grant_related_via_consumed_invocation"] != sorted(grant_refs) or basis[
        "admission_related_via_current_binding"
    ] != sorted({
        item["knowledge_admission_receipt_id"]
        for item in projection["current_use_bindings"]
        if item["knowledge_admission_receipt_id"] is not None
    }):
        raise ContractError("Source association basis conflicts with projected refs")

    no_send_claim = run["stream_state"] == "pending" or run["error_code"] == "stream_not_started"
    prior_send_evidence = bool(
        run["stream_claimed_at"] or projection["model_usage"] or
        any(step["kind"] == "model" for step in steps) or
        projection["enforcement_dispatches"] or projection["authorization_invocations"]
    )
    stream_conflicts: list[str] = []
    if no_send_claim and prior_send_evidence:
        stream_conflicts.append("not_sent_claim_conflicts_with_recorded_activity")
    if run["stream_state"] == "pending" and run["status"] != "running":
        stream_conflicts.append("pending_stream_has_terminal_run_status")
    if run["stream_state"] == "claimed" and run["status"] != "running":
        stream_conflicts.append("claimed_stream_has_terminal_run_status")
    if run["stream_state"] == "closed" and run["status"] == "running":
        stream_conflicts.append("closed_stream_has_running_run_status")
    if (run["error_code"] == "stream_not_started" and
            (run["stream_state"] != "closed" or run["status"] != "failed")):
        stream_conflicts.append("not_started_terminal_state_conflict")
    stream_request_state = (
        "NOT_STREAM" if run["stream_state"] is None else
        "INCONSISTENT" if stream_conflicts else
        "NOT_SENT" if no_send_claim else
        "MAY_HAVE_SENT" if run["stream_state"] in {"claimed", "closed"} else "UNKNOWN"
    )
    pending.extend(f"invocation_orphan:{value}" for value in orphan_claimed_invocations)
    pending.extend(f"model_call:{value}" for value in in_flight_model)
    pending.extend(f"usage_orphan:{value}" for value in orphan_usage)
    pending.extend(f"usage_conflict:{value}" for value in usage_conflicts)
    pending.extend(
        f"usage_lifecycle_conflict:{value}" for value in usage_lifecycle_conflicts
    )
    if (run["stream_state"] == "claimed" or
            run["error_code"] == "stream_execution_indeterminate" or stream_conflicts):
        pending.append(f"stream:{run_id}")
    effective = (
        "indeterminate" if (
            run["error_code"] == "stream_execution_indeterminate" or
            stream_conflicts or orphan_usage or usage_conflicts or
            usage_lifecycle_conflicts or orphan_claimed_invocations
        ) else run["status"]
    )
    if run["effective_status"] != effective:
        raise ContractError("Run effective status differs from native signals")
    missing_grants = grant_refs - {grant["id"] for grant in projection["authorization_grants"]}
    admission_refs = {
        binding["knowledge_admission_receipt_id"]
        for binding in projection["current_use_bindings"]
        if binding["knowledge_admission_receipt_id"] is not None
    }
    missing_admissions = admission_refs - {
        admission["id"] for admission in projection["knowledge_admission_receipts"]
    }
    expected_missing = [
        {"source": "authorization_grants", "reason": "referenced_grant_row_missing",
         "ids": sorted(missing_grants)}
    ] if missing_grants else []
    if missing_admissions:
        expected_missing.append({
            "source": "knowledge_admission_receipts",
            "reason": "referenced_admission_row_missing", "ids": sorted(missing_admissions),
        })
    actual_missing = [item for item in source["omissions"] if isinstance(item, dict)
                      and item.get("reason") in {
                          "referenced_grant_row_missing", "referenced_admission_row_missing",
                      }]
    if actual_missing != expected_missing:
        raise ContractError("Referenced authority row omissions conflict with native refs")
    signals = _object(
        projection["signals"],
        {"stream_request_state", "stream_conflicts", "pending_operations",
         "in_flight_model_attempt_ids", "unknown_usage_attempt_ids",
         "invalid_usage_attempt_ids", "orphan_usage_attempt_ids",
         "usage_conflict_attempt_ids", "usage_lifecycle_conflict_attempt_ids",
         "orphan_claimed_invocation_ids",
         "verified_evidence", "goal_binding"},
        "projection.signals",
    )
    if signals != {
        "stream_request_state": stream_request_state,
        "stream_conflicts": stream_conflicts,
        "pending_operations": sorted(pending),
        "in_flight_model_attempt_ids": sorted(in_flight_model),
        "unknown_usage_attempt_ids": sorted(unknown_usage),
        "invalid_usage_attempt_ids": sorted(invalid_usage),
        "orphan_usage_attempt_ids": sorted(orphan_usage),
        "usage_conflict_attempt_ids": sorted(usage_conflicts),
        "usage_lifecycle_conflict_attempt_ids": sorted(usage_lifecycle_conflicts),
        "orphan_claimed_invocation_ids": sorted(orphan_claimed_invocations),
        "verified_evidence": [],
        "goal_binding": "NATIVE_UNBOUND",
    }:
        raise ContractError("Projected signals conflict with native facts")
    return projection


def replay_host_projection(
    goal: dict[str, Any], projection: dict[str, Any], *,
    expected_goal_sha256: str, expected_projection_sha256: str,
    previous_sequence: int = 0, current_sequence: int | None = None,
) -> dict[str, Any]:
    """Compare recorded activity while leaving goal progress unestablished.

    The caller supplies a separately pinned frozen goal. Its association with
    this native Run is a host assertion; the Run row has no Protocol/goal key.
    No output authorizes dispatch, retry, qualification, or current use.
    """
    _validate_goal(goal)
    _hash(expected_goal_sha256, "expected_goal_sha256")
    if digest(goal) != expected_goal_sha256:
        raise ContractError("Pinned goal digest mismatch")
    projection = _validate_projection(projection, expected_projection_sha256)
    if goal["protocol"]["workspace_id"] != projection["workspace_id"]:
        raise ContractError("Goal and native Run belong to different workspaces")
    steps = projection["steps"]
    highest = steps[-1]["sequence"] if steps else 0
    if current_sequence is None:
        current_sequence = highest
    if (type(previous_sequence) is not int or type(current_sequence) is not int or
            previous_sequence < 0 or previous_sequence > current_sequence or
            current_sequence > highest):
        raise ContractError("Invalid native Step sequence window")
    step_by_id = {step["id"]: step for step in steps}
    new_steps = [step for step in steps if previous_sequence < step["sequence"] <= current_sequence]
    usage = [item for item in projection["model_usage"] if (
        item["step_id"] in step_by_id and
        previous_sequence < step_by_id[item["step_id"]]["sequence"] <= current_sequence
    )]
    artifacts = [item for item in projection["artifacts"] if (
        item["step_id"] in step_by_id and
        previous_sequence < step_by_id[item["step_id"]]["sequence"] <= current_sequence
    )]
    signals = projection["signals"]
    historical_only = current_sequence < highest
    unresolved = sorted(set(
        signals["pending_operations"] +
        [f"orphan_usage:{value}" for value in signals["orphan_usage_attempt_ids"]] +
        [f"missing_grant:{value}" for value in sorted(
            {item["grant_id"] for item in projection["authorization_invocations"]} -
            {item["id"] for item in projection["authorization_grants"]}
        )] +
        [f"missing_admission:{value}" for value in sorted(
            {item["knowledge_admission_receipt_id"]
             for item in projection["current_use_bindings"]
             if item["knowledge_admission_receipt_id"] is not None} -
            {item["id"] for item in projection["knowledge_admission_receipts"]}
        )]
    ))
    if historical_only:
        action, reason = "PAUSE", "historical_window_not_current_state"
    elif unresolved or projection["run"]["effective_status"] == "indeterminate":
        action, reason = "PAUSE", "indeterminate_or_pending_native_operation"
    elif signals["stream_request_state"] in {"NOT_SENT", "UNKNOWN", "INCONSISTENT"}:
        action, reason = "PAUSE", "stream_not_started_or_request_state_unknown"
    elif new_steps:
        action, reason = "REASSESS", "activity_without_projected_verified_goal_evidence"
    else:
        action, reason = "PAUSE", "native_goal_progress_not_established"
    body = {
        "kind": "HostProjectionReplay.v0",
        "candidate_only": True,
        "workspace_id": projection["workspace_id"],
        "run_id": projection["run_id"],
        "goal_sha256": expected_goal_sha256,
        "projection_sha256": expected_projection_sha256,
        "source_sha256": projection["source"]["sha256"],
        "goal_binding": "HOST_ASSERTED_NOT_NATIVE",
        "goal_target": goal["target"],
        "goal_evidence_criteria": goal["policy"]["evidence_criteria"],
        "run": {
            "status": projection["run"]["status"],
            "effective_status": projection["run"]["effective_status"],
            "stream_request_state": signals["stream_request_state"],
            "source_sha256": projection["run"]["source_sha256"],
        },
        "window": {
            "previous_sequence": previous_sequence,
            "current_sequence": current_sequence,
            "historical_only": historical_only,
            "basis": "CURRENT_SNAPSHOT_STEP_SEQUENCE_NOT_TEMPORAL_DELTA",
            "recorded_step_refs": [
                {"id": step["id"], "sequence": step["sequence"],
                 "kind": step["kind"], "status": step["status"],
                 "source_sha256": step["source_sha256"]}
                for step in new_steps
            ],
            "model_usage_refs": [
                {"attempt_id": item["attempt_id"], "usage_status": item["usage_status"],
                 "source_sha256": item["source_sha256"]} for item in usage
            ],
            "candidate_artifact_refs": [
                {"id": item["id"], "source_sha256": item["source_sha256"]}
                for item in artifacts
            ],
        },
        "progress_assessment": {
            "kind": "NativeProgressDiagnostic.v0",
            "M": {"status": "NOT_EVALUATED", "projected_verified_count": 0},
            "R": {"status": "NOT_EVALUATED"},
            "E": {"status": "NOT_EVALUATED", "projected_verified_count": 0,
                  "candidate_artifact_counted": False,
                  "requested_criteria": goal["policy"]["evidence_criteria"]},
            "U": {"pending_operations": unresolved,
                  "unknown_usage_attempt_ids": signals["unknown_usage_attempt_ids"],
                  "invalid_usage_attempt_ids": signals["invalid_usage_attempt_ids"],
                  "goal_binding": "HOST_ASSERTED_NOT_NATIVE"},
            "B": {"status": "NOT_EVALUATED", "recorded_steps_in_window": len(new_steps),
                  "model_attempts_in_window": len(usage),
                  "candidate_artifacts_in_window": len(artifacts)},
            "completion_evaluated": False,
        },
        "recovery_decision": {
            "kind": "NativeRecoveryProposal.v0", "state": "PROPOSED",
            "action": action, "reason": reason,
            "requires_host_state_refresh": True,
            "requires_existing_authorization": True,
            "execution_authorized": False,
            "qualification_granted": False,
            "current_use_bound": False,
        },
        "authority_effects_of_replay": _NO_AUTHORITY,
    }
    return _seal(body)
