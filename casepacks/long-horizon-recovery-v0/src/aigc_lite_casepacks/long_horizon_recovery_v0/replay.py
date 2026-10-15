"""Offline replay of bounded state, progress, recovery and compaction facts.

The caller supplies a host-exported ledger projection and separately pinned
SHA-256 identities. This module has no access to a Qualification or Execution
Authority port, and its output is never an authorization to act.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any

DOMAIN = b"aigc-lite.long-horizon-recovery.v0\n"
HEX = set("0123456789abcdef")
MAX_CANONICAL_BYTES = 2_000_000
MAX_STEPS = 10_000
MAX_RECEIPTS = 20_000
MAX_ARTIFACTS = 20_000
MAX_FIRINGS = 10_000
FACT_KINDS = {
    "resource",
    "observation",
    "milestone",
    "evidence",
    "uncertainty",
    "evidence_unavailable",
    "operation_reconciled",
    "invalidation",
}
RECOVERY_ACTIONS = {
    "CONTINUE",
    "REPLAN",
    "REOBSERVE",
    "REASSESS",
    "WAIT_FOR_EVIDENCE",
    "PAUSE",
    "STOP",
}


class ContractError(ValueError):
    """Malformed or unpinned evidence; never a positive control result."""


def canonical_bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ContractError("Invalid canonical JSON") from exc
    if len(encoded) > MAX_CANONICAL_BYTES:
        raise ContractError("Canonical input exceeds size limit")
    return encoded


def digest(value: Any) -> str:
    """Domain-separated content identity, excluding a top-level self hash."""
    if isinstance(value, dict):
        value = {key: item for key, item in value.items() if key != "sha256"}
    return hashlib.sha256(DOMAIN + canonical_bytes(value)).hexdigest()


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(value)
    sealed["sha256"] = digest(sealed)
    return sealed


def _fields(value: Any, required: set[str], optional: set[str], where: str) -> dict:
    if not isinstance(value, dict) or isinstance(value, bool):
        raise ContractError(f"{where} must be an object")
    actual = set(value)
    if not required <= actual or actual - required - optional:
        raise ContractError(f"{where} has missing or unexpected fields")
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ContractError(f"{where} must be a nonempty bounded string")
    return value


def _strings(value: Any, where: str, *, maximum: int = 256) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ContractError(f"{where} must be a bounded array")
    checked = [_string(item, where) for item in value]
    if len(set(checked)) != len(checked):
        raise ContractError(f"{where} contains duplicates")
    return checked


def _integer(value: Any, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f"{where} must be an integer >= {minimum}")
    return value


def _hash(value: Any, where: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - HEX:
        raise ContractError(f"{where} must be a lowercase SHA-256 hex digest")
    return value


def _time(value: Any, where: str) -> datetime:
    _string(value, where)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{where} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{where} must include a timezone")
    return parsed.astimezone(UTC)


def _validate_goal(goal: Any) -> dict:
    _fields(
        goal, {"contract", "protocol", "target", "policy"}, set(), "goal",
    )
    if goal["contract"] != "LongHorizonGoal.v0":
        raise ContractError("Unsupported goal contract")
    protocol = _fields(
        goal["protocol"],
        {"id", "workspace_id", "version", "status", "purpose", "scope",
         "completion_predicate", "stop_conditions", "budget"},
        set(), "protocol",
    )
    for name in ("id", "workspace_id", "purpose", "scope", "completion_predicate"):
        _string(protocol[name], f"protocol.{name}")
    _integer(protocol["version"], "protocol.version", minimum=1)
    if protocol["status"] != "frozen":
        raise ContractError("Only frozen Protocols are eligible")
    _strings(protocol["stop_conditions"], "protocol.stop_conditions")
    budget = _fields(
        protocol["budget"], {"max_steps", "max_wall_seconds"}, set(),
        "protocol.budget",
    )
    _integer(budget["max_steps"], "protocol.budget.max_steps", minimum=1)
    _integer(budget["max_wall_seconds"], "protocol.budget.max_wall_seconds", minimum=1)
    _string(goal["target"], "goal.target")
    policy = _fields(
        goal["policy"],
        {"milestones", "evidence_criteria", "resource_keys",
         "critical_observations", "freshness_seconds",
         "stagnation_step_limit", "forbidden_actions"},
        set(), "goal.policy",
    )
    for name in ("milestones", "evidence_criteria", "resource_keys",
                 "critical_observations", "forbidden_actions"):
        _strings(policy[name], f"goal.policy.{name}")
    _integer(policy["freshness_seconds"], "goal.policy.freshness_seconds", minimum=1)
    _integer(policy["stagnation_step_limit"], "goal.policy.stagnation_step_limit", minimum=1)
    return goal


def _validate_ledger(goal: dict, ledger: Any) -> dict:
    _fields(
        ledger,
        {"contract", "workspace_id", "run", "steps", "receipts",
         "artifacts", "scheduler_firings"}, set(), "ledger",
    )
    if ledger["contract"] != "LongHorizonLedger.v0":
        raise ContractError("Unsupported ledger contract")
    if ledger["workspace_id"] != goal["protocol"]["workspace_id"]:
        raise ContractError("Ledger workspace differs from frozen Protocol")
    run = _fields(
        ledger["run"], {"id", "workspace_id", "protocol_id", "protocol_version",
                        "goal_sha256", "started_at", "status", "status_at"},
        set(), "ledger.run",
    )
    _string(run["id"], "ledger.run.id")
    _integer(run["protocol_version"], "ledger.run.protocol_version", minimum=1)
    _hash(run["goal_sha256"], "ledger.run.goal_sha256")
    _time(run["started_at"], "ledger.run.started_at")
    if _time(run["status_at"], "ledger.run.status_at") < _time(run["started_at"], "run.started_at"):
        raise ContractError("Run status predates Run")
    if run["workspace_id"] != ledger["workspace_id"]:
        raise ContractError("Run workspace mismatch")
    if (run["protocol_id"] != goal["protocol"]["id"] or
            run["protocol_version"] != goal["protocol"]["version"] or
            run["goal_sha256"] != digest(goal)):
        raise ContractError("Run Protocol identity mismatch")
    if run["status"] not in {"running", "succeeded", "failed", "cancelled",
                             "indeterminate", "limit_reached"}:
        raise ContractError("Unsupported Run status")
    _bounded_list(ledger["steps"], MAX_STEPS, "ledger.steps")
    _bounded_list(ledger["receipts"], MAX_RECEIPTS, "ledger.receipts")
    _bounded_list(ledger["artifacts"], MAX_ARTIFACTS, "ledger.artifacts")
    _bounded_list(ledger["scheduler_firings"], MAX_FIRINGS, "ledger.scheduler_firings")
    step_ids: set[str] = set()
    sequences: set[int] = set()
    last_step_at = _time(run["started_at"], "run.started_at")
    for step in ledger["steps"]:
        _fields(step, {"id", "run_id", "sequence", "kind", "status", "finished_at"},
                set(), "ledger.step")
        step_id = _string(step["id"], "step.id")
        seq = _integer(step["sequence"], "step.sequence", minimum=1)
        if step_id in step_ids or seq in sequences:
            raise ContractError("Duplicate Step identity or sequence")
        step_ids.add(step_id)
        sequences.add(seq)
        if step["run_id"] != run["id"]:
            raise ContractError("Cross-run Step")
        if step["kind"] not in {"tool", "observation", "external_dispatch", "research",
                                "scheduler", "verification", "reconciliation"}:
            raise ContractError("Unsupported Step kind")
        if step["status"] not in {"running", "succeeded", "failed", "cancelled", "indeterminate"}:
            raise ContractError("Unsupported Step status")
        step_at = _time(step["finished_at"], "step.finished_at")
        if step_at < last_step_at:
            raise ContractError("Step completion order rolled back")
        last_step_at = step_at
    if sequences != set(range(1, len(sequences) + 1)):
        raise ContractError("Step sequence is not contiguous")
    if [step["sequence"] for step in ledger["steps"]] != sorted(sequences):
        raise ContractError("Steps must be in sequence order")
    if run["status"] != "running" and _time(run["status_at"], "run.status_at") < last_step_at:
        raise ContractError("Terminal Run status predates its final Step")
    step_map = {step["id"]: step for step in ledger["steps"]}
    receipt_ids: set[str] = set()
    for receipt in ledger["receipts"]:
        _fields(receipt, {"id", "run_id", "step_id", "status", "output_digest",
                          "recorded_at", "fact"},
                set(), "ledger.receipt")
        receipt_id = _string(receipt["id"], "receipt.id")
        if receipt_id in receipt_ids:
            raise ContractError("Duplicate Receipt identity")
        receipt_ids.add(receipt_id)
        if receipt["run_id"] != run["id"] or receipt["step_id"] not in step_ids:
            raise ContractError("Receipt has invalid Step/Run binding")
        if receipt["status"] not in {"succeeded", "failed", "indeterminate"}:
            raise ContractError("Unsupported Receipt status")
        receipt_step = step_map[receipt["step_id"]]
        if _time(receipt["recorded_at"], "receipt.recorded_at") < _time(
            receipt_step["finished_at"], "receipt.step.finished_at"
        ):
            raise ContractError("Receipt predates Step completion")
        _hash(receipt["output_digest"], "receipt.output_digest")
        _validate_fact(receipt["fact"])
    artifact_ids: set[str] = set()
    for artifact in ledger["artifacts"]:
        _fields(artifact, {"id", "run_id", "step_id", "kind", "content_hash",
                           "candidate_only", "recorded_at"}, set(), "ledger.artifact")
        artifact_id = _string(artifact["id"], "artifact.id")
        if artifact_id in artifact_ids:
            raise ContractError("Duplicate Artifact identity")
        artifact_ids.add(artifact_id)
        if artifact["run_id"] != run["id"] or artifact["step_id"] not in step_ids:
            raise ContractError("Artifact has invalid Step/Run binding")
        _string(artifact["kind"], "artifact.kind")
        _hash(artifact["content_hash"], "artifact.content_hash")
        artifact_step = step_map[artifact["step_id"]]
        if _time(artifact["recorded_at"], "artifact.recorded_at") < _time(
            artifact_step["finished_at"], "artifact.step.finished_at"
        ):
            raise ContractError("Artifact predates Step completion")
        if not isinstance(artifact["candidate_only"], bool):
            raise ContractError("Artifact candidate_only must be boolean")
    for receipt in ledger["receipts"]:
        fact = receipt["fact"]
        if fact["kind"] == "evidence" and fact["artifact_id"] not in artifact_ids:
            raise ContractError("Evidence Receipt references absent Artifact")
    firing_event_ids: set[str] = set()
    firing_sequences: dict[str, int] = {}
    firing_times: dict[str, datetime] = {}
    for firing in ledger["scheduler_firings"]:
        _fields(firing, {"id", "firing_id", "run_id", "sequence", "state",
                         "recorded_at"}, set(),
                "ledger.scheduler_firing")
        event_id = _string(firing["id"], "scheduler_firing.id")
        firing_id = _string(firing["firing_id"], "scheduler_firing.firing_id")
        if event_id in firing_event_ids:
            raise ContractError("Duplicate scheduler firing event")
        firing_event_ids.add(event_id)
        if firing["run_id"] != run["id"]:
            raise ContractError("Cross-run scheduler firing")
        _integer(firing["sequence"], "scheduler_firing.sequence")
        if firing["sequence"] > len(sequences):
            raise ContractError("Scheduler firing exceeds Run Step sequence")
        firing_at = _time(firing["recorded_at"], "firing.recorded_at")
        if firing_at < _time(
            run["started_at"], "run.started_at"
        ):
            raise ContractError("Scheduler firing predates Run")
        if firing_id in firing_sequences and firing["sequence"] < firing_sequences[firing_id]:
            raise ContractError("Scheduler firing Step sequence rolled back")
        if firing_id in firing_times and firing_at <= firing_times[firing_id]:
            raise ContractError("Scheduler firing event time is not increasing")
        firing_sequences[firing_id] = firing["sequence"]
        firing_times[firing_id] = firing_at
        if firing["state"] not in {"scheduled", "running", "succeeded", "failed", "indeterminate"}:
            raise ContractError("Unsupported scheduler firing state")
    canonical_bytes(ledger)
    return ledger


def _bounded_list(value: Any, maximum: int, where: str) -> list:
    if not isinstance(value, list) or len(value) > maximum:
        raise ContractError(f"{where} must be a bounded array")
    return value


def _validate_fact(fact: Any) -> None:
    _fields(fact, {"kind"}, {"key", "value", "environment_version",
                             "environment_revision", "observed_at",
                             "milestone_id", "criterion_id", "target", "artifact_id",
                             "state", "reason", "operation_step_id", "terminal_confirmed",
                             "target_receipt_id"},
            "receipt.fact")
    if fact["kind"] not in FACT_KINDS:
        raise ContractError("Unsupported fact kind")
    kind = fact["kind"]
    expected = {
        "resource": {"kind", "key", "value", "environment_version",
                     "environment_revision", "observed_at"},
        "observation": {"kind", "key", "value", "environment_version",
                        "environment_revision", "observed_at"},
        "milestone": {"kind", "milestone_id"},
        "evidence": {"kind", "criterion_id", "target", "artifact_id"},
        "uncertainty": {"kind", "key", "state"},
        "evidence_unavailable": {"kind", "criterion_id", "reason"},
        "operation_reconciled": {"kind", "operation_step_id", "terminal_confirmed"},
        "invalidation": {"kind", "target_receipt_id", "reason"},
    }[kind]
    if set(fact) != expected:
        raise ContractError(f"Malformed {kind} fact")
    if kind in {"resource", "observation"}:
        _string(fact["key"], "fact.key")
        _string(fact["environment_version"], "fact.environment_version")
        _integer(fact["environment_revision"], "fact.environment_revision")
        _time(fact["observed_at"], "fact.observed_at")
        if kind == "resource":
            _integer(fact["value"], "resource.value")
        elif not isinstance(fact["value"], (str, int, bool)):
            raise ContractError("Observation value must be scalar")
    elif kind == "milestone":
        _string(fact["milestone_id"], "fact.milestone_id")
    elif kind == "evidence":
        for name in ("criterion_id", "target", "artifact_id"):
            _string(fact[name], f"fact.{name}")
    elif kind == "uncertainty":
        _string(fact["key"], "fact.key")
        if fact["state"] not in {"open", "resolved"}:
            raise ContractError("Uncertainty state must be open or resolved")
    elif kind == "evidence_unavailable":
        _string(fact["criterion_id"], "fact.criterion_id")
        _string(fact["reason"], "fact.reason")
    elif kind == "invalidation":
        _string(fact["target_receipt_id"], "fact.target_receipt_id")
        _string(fact["reason"], "fact.reason")
    else:
        _string(fact["operation_step_id"], "fact.operation_step_id")
        if fact["terminal_confirmed"] is not True:
            raise ContractError("Operation reconciliation must confirm terminal state")


def _validate_pins(goal: dict, ledger: dict, goal_sha256: str, ledger_sha256: str) -> None:
    _hash(goal_sha256, "expected_goal_sha256")
    _hash(ledger_sha256, "expected_ledger_sha256")
    if digest(goal) != goal_sha256 or digest(ledger) != ledger_sha256:
        raise ContractError("Pinned goal or ledger digest mismatch")


def _latest_ledger_time(ledger: dict) -> datetime:
    run = ledger["run"]
    values = [run["started_at"], run["status_at"]]
    values.extend(item["finished_at"] for item in ledger["steps"])
    values.extend(item["recorded_at"] for item in ledger["receipts"])
    values.extend(item["recorded_at"] for item in ledger["artifacts"])
    values.extend(item["recorded_at"] for item in ledger["scheduler_firings"])
    return max(_time(value, "ledger.event_time") for value in values)


def reconstruct_snapshot(
    goal: dict, ledger: dict, *, expected_goal_sha256: str,
    expected_ledger_sha256: str, through_sequence: int, assessed_at: str,
) -> dict:
    """Rebuild only host-recorded facts visible by a Step sequence cutoff."""
    _validate_goal(goal)
    _validate_ledger(goal, ledger)
    _validate_pins(goal, ledger, expected_goal_sha256, expected_ledger_sha256)
    cutoff = _integer(through_sequence, "through_sequence")
    if cutoff > len(ledger["steps"]):
        raise ContractError("Checkpoint exceeds known Step sequence")
    at = _time(assessed_at, "assessed_at")
    if at < _time(ledger["run"]["started_at"], "run.started_at"):
        raise ContractError("Assessment predates Run")
    visible_steps = [step for step in ledger["steps"] if step["sequence"] <= cutoff]
    if any(at < _time(step["finished_at"], "step.finished_at") for step in visible_steps):
        raise ContractError("Assessment predates visible Step")
    step_by_id = {step["id"]: step for step in visible_steps}
    artifact_by_id = {item["id"]: item for item in ledger["artifacts"]
                      if item["step_id"] in step_by_id and
                      _time(item["recorded_at"], "artifact.recorded_at") <= at}
    visible_receipts = sorted(
        (item for item in ledger["receipts"] if item["step_id"] in step_by_id and
         _time(item["recorded_at"], "receipt.recorded_at") <= at),
        key=lambda item: (_time(item["recorded_at"], "receipt.recorded_at"),
                          step_by_id[item["step_id"]]["sequence"],
                          item["id"]),
    )
    visible_receipt_by_id = {item["id"]: item for item in visible_receipts}
    resources: dict[str, dict] = {}
    observations: dict[str, dict] = {}
    milestones: dict[str, str] = {}
    evidence: dict[str, str] = {}
    evidence_artifacts: dict[str, str] = {}
    uncertainties: dict[str, str] = {}
    uncertainty_receipts: dict[str, str] = {}
    unavailable: dict[str, str] = {}
    reconciled: set[str] = set()
    invalidated: dict[str, str] = {}
    environment_version: str | None = None
    environment_revision = -1
    version_by_revision: dict[int, str] = {}
    policy = goal["policy"]
    for receipt in visible_receipts:
        if receipt["status"] != "succeeded":
            continue
        step = step_by_id[receipt["step_id"]]
        if step["status"] != "succeeded":
            continue
        fact = receipt["fact"]
        kind = fact["kind"]
        if kind in {"resource", "observation"}:
            if _time(fact["observed_at"], "fact.observed_at") > _time(step["finished_at"], "step.finished_at"):
                raise ContractError("Observation postdates supporting Step")
            if kind == "resource" and fact["key"] not in policy["resource_keys"]:
                raise ContractError("Resource key outside frozen goal")
            if kind == "observation" and fact["key"] not in policy["critical_observations"]:
                raise ContractError("Observation key outside frozen goal")
            if kind == "observation" and fact["key"] in policy["resource_keys"]:
                raise ContractError("Resource keys require resource observations")
            revision = fact["environment_revision"]
            if revision < environment_revision:
                raise ContractError("Environment revision rolled back")
            prior_version = version_by_revision.get(revision)
            if prior_version is not None and prior_version != fact["environment_version"]:
                raise ContractError("Environment revision has conflicting identities")
            version_by_revision[revision] = fact["environment_version"]
            environment_revision = revision
            environment_version = fact["environment_version"]
            prior_record = (resources if kind == "resource" else observations).get(fact["key"])
            if prior_record and _time(fact["observed_at"], "fact.observed_at") < _time(
                prior_record["observed_at"], "prior_record.observed_at"
            ):
                raise ContractError("Observation time rolled back")
            record = {
                "value": fact["value"], "environment_version": environment_version,
                "environment_revision": environment_revision,
                "observed_at": fact["observed_at"], "step_id": step["id"],
                "receipt_id": receipt["id"],
            }
            (resources if kind == "resource" else observations)[fact["key"]] = record
        elif kind == "milestone":
            if step["kind"] != "verification":
                raise ContractError("Milestone must come from a verification Step")
            if fact["milestone_id"] not in policy["milestones"]:
                raise ContractError("Milestone outside frozen goal")
            milestones[fact["milestone_id"]] = receipt["id"]
        elif kind == "evidence":
            if step["kind"] != "verification":
                raise ContractError("Evidence must come from a verification Step")
            if fact["criterion_id"] not in policy["evidence_criteria"] or fact["target"] != goal["target"]:
                raise ContractError("Evidence is outside frozen target or criterion")
            artifact = artifact_by_id.get(fact["artifact_id"])
            if artifact is None or artifact["candidate_only"]:
                raise ContractError("Candidate or future Artifact cannot satisfy evidence criterion")
            if (step_by_id[artifact["step_id"]]["sequence"] > step["sequence"] or
                    _time(artifact["recorded_at"], "artifact.recorded_at") >
                    _time(receipt["recorded_at"], "receipt.recorded_at") or
                    artifact["kind"] != "verified_evidence"):
                raise ContractError("Evidence Artifact is later than verification or wrong kind")
            evidence[fact["criterion_id"]] = receipt["id"]
            evidence_artifacts[fact["criterion_id"]] = artifact["id"]
        elif kind == "uncertainty":
            if fact["key"] not in policy["evidence_criteria"]:
                raise ContractError("Uncertainty key outside frozen evidence criteria")
            if fact["state"] == "resolved" and (
                step["kind"] != "verification" or fact["key"] not in evidence
            ):
                raise ContractError("Uncertainty cannot resolve without goal evidence")
            uncertainties[fact["key"]] = fact["state"]
            uncertainty_receipts[fact["key"]] = receipt["id"]
        elif kind == "evidence_unavailable":
            if step["kind"] != "verification":
                raise ContractError("Evidence unavailability needs verification Step")
            if fact["criterion_id"] not in policy["evidence_criteria"]:
                raise ContractError("Unavailable evidence outside frozen criterion")
            unavailable[fact["criterion_id"]] = receipt["id"]
        elif kind == "operation_reconciled":
            if step["kind"] != "reconciliation":
                raise ContractError("Operation reconciliation needs a host reconciliation Step")
            operation = step_by_id.get(fact["operation_step_id"])
            if operation is None or not (
                operation["kind"] == "external_dispatch" or
                operation["status"] == "indeterminate" or
                any(item["step_id"] == operation["id"] and item["status"] == "indeterminate"
                    for item in visible_receipts)
            ):
                raise ContractError("Reconciliation references wrong operation")
            if (operation["sequence"] >= step["sequence"] or
                    _time(receipt["recorded_at"], "receipt.recorded_at") <=
                    _time(operation["finished_at"], "operation.finished_at")):
                raise ContractError("Reconciliation must follow dispatch")
            reconciled.add(operation["id"])
        elif kind == "invalidation":
            if step["kind"] != "verification":
                raise ContractError("Invalidation needs verification Step")
            target = visible_receipt_by_id.get(fact["target_receipt_id"])
            if (target is None or target["status"] != "succeeded" or
                    step_by_id[target["step_id"]]["status"] != "succeeded" or
                    target["fact"]["kind"] not in {
                "resource", "observation", "milestone", "evidence",
            }):
                raise ContractError("Invalidation references absent or ineligible Receipt")
            if (step_by_id[target["step_id"]]["sequence"] >= step["sequence"] or
                    _time(target["recorded_at"], "target.recorded_at") >=
                    _time(receipt["recorded_at"], "receipt.recorded_at")):
                raise ContractError("Invalidation must follow the target Receipt")
            invalidated[target["id"]] = receipt["id"]
            target_fact = target["fact"]
            if target_fact["kind"] == "resource":
                current = resources.get(target_fact["key"])
                if current and current["receipt_id"] == target["id"]:
                    current["invalidated_by"] = receipt["id"]
            elif target_fact["kind"] == "observation":
                current = observations.get(target_fact["key"])
                if current and current["receipt_id"] == target["id"]:
                    current["invalidated_by"] = receipt["id"]
            elif target_fact["kind"] == "milestone":
                if milestones.get(target_fact["milestone_id"]) == target["id"]:
                    del milestones[target_fact["milestone_id"]]
            elif evidence.get(target_fact["criterion_id"]) == target["id"]:
                del evidence[target_fact["criterion_id"]]
                del evidence_artifacts[target_fact["criterion_id"]]
    invalidated_goal_milestones = sorted({
        visible_receipt_by_id[receipt_id]["fact"]["milestone_id"]
        for receipt_id in invalidated
        if (visible_receipt_by_id[receipt_id]["fact"]["kind"] == "milestone" and
            visible_receipt_by_id[receipt_id]["fact"]["milestone_id"] not in milestones)
    })
    invalidated_goal_evidence_criteria = sorted({
        visible_receipt_by_id[receipt_id]["fact"]["criterion_id"]
        for receipt_id in invalidated
        if (visible_receipt_by_id[receipt_id]["fact"]["kind"] == "evidence" and
            visible_receipt_by_id[receipt_id]["fact"]["criterion_id"] not in evidence)
    })
    def with_freshness(items: dict[str, dict]) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for key, record in sorted(items.items()):
            age = (at - _time(record["observed_at"], "observed_at")).total_seconds()
            if age < 0:
                raise ContractError("Assessment predates observation")
            fresh = ("invalidated_by" not in record and age <= policy["freshness_seconds"] and
                     record["environment_version"] == environment_version and
                     record["environment_revision"] == environment_revision)
            result[key] = {**record, "freshness": "CURRENT" if fresh else "STALE"}
        return result
    resources = with_freshness(resources)
    observations = with_freshness(observations)
    visible_state = {**observations, **resources}
    missing_critical = sorted(set(policy["critical_observations"]) - set(visible_state))
    stale_critical = sorted(key for key in policy["critical_observations"]
                            if key in visible_state and visible_state[key]["freshness"] == "STALE")
    missing_resources = sorted(set(policy["resource_keys"]) - set(resources))
    stale_resources = sorted(key for key in policy["resource_keys"]
                             if key in resources and resources[key]["freshness"] == "STALE")
    indeterminate_receipt_steps = {item["step_id"] for item in visible_receipts
                                   if item["status"] == "indeterminate"}
    pending_operations = sorted(step["id"] for step in visible_steps
                                if (step["kind"] == "external_dispatch" or
                                    step["status"] == "indeterminate" or
                                    step["id"] in indeterminate_receipt_steps)
                                and step["id"] not in reconciled)
    firing_states: dict[str, str] = {}
    firing_latest_events: dict[str, str] = {}
    for event in sorted(ledger["scheduler_firings"],
                        key=lambda item: (item["sequence"], item["recorded_at"], item["id"])):
        if event["sequence"] <= cutoff and _time(event["recorded_at"], "event.recorded_at") <= at:
            firing_states[event["firing_id"]] = event["state"]
            firing_latest_events[event["firing_id"]] = event["id"]
    pending_firings = sorted(firing_id for firing_id, state in firing_states.items()
                             if state in {"scheduled", "running", "indeterminate"})
    pending_firing_events = {firing_id: firing_latest_events[firing_id]
                             for firing_id in pending_firings}
    elapsed = math.ceil((at - _time(ledger["run"]["started_at"], "run.started_at")).total_seconds())
    budget = goal["protocol"]["budget"]
    body = {
        "kind": "StateSnapshot.v0", "candidate_only": True,
        "goal_sha256": expected_goal_sha256, "ledger_sha256": expected_ledger_sha256,
        "workspace_id": ledger["workspace_id"], "protocol_id": goal["protocol"]["id"],
        "protocol_version": goal["protocol"]["version"], "run_id": ledger["run"]["id"],
        "run_status": (ledger["run"]["status"] if cutoff == len(ledger["steps"]) and
                       _time(ledger["run"]["status_at"], "run.status_at") <= at else "running"),
        "through_sequence": cutoff, "assessed_at": assessed_at,
        "historical_only": cutoff < len(ledger["steps"]) or at < _latest_ledger_time(ledger),
        "environment_version": environment_version,
        "environment_revision": environment_revision if environment_revision >= 0 else None,
        "resources": resources, "observations": observations,
        "missing_critical": missing_critical, "stale_critical": stale_critical,
        "missing_resources": missing_resources, "stale_resources": stale_resources,
        "recorded_milestones": dict(sorted(milestones.items())),
        "scoped_evidence_receipts": dict(sorted(evidence.items())),
        "scoped_evidence_artifacts": dict(sorted(evidence_artifacts.items())),
        "missing_evidence_criteria": sorted(set(policy["evidence_criteria"]) - set(evidence)),
        "open_uncertainties": sorted(
            key for key, state in uncertainties.items()
            if state == "open" or key not in evidence
        ),
        "uncertainty_receipts": dict(sorted(uncertainty_receipts.items())),
        "unavailable_evidence_receipts": dict(sorted(unavailable.items())),
        "invalidated_receipts": dict(sorted(invalidated.items())),
        "invalidated_goal_milestones": invalidated_goal_milestones,
        "invalidated_goal_evidence_criteria": invalidated_goal_evidence_criteria,
        "pending_operations": pending_operations, "pending_firings": pending_firings,
        "pending_firing_events": pending_firing_events,
        "budget": {
            "steps_used": cutoff,
            "steps_remaining": max(0, budget["max_steps"] - cutoff),
            "wall_seconds_used": elapsed,
            "wall_seconds_remaining": max(0, budget["max_wall_seconds"] - elapsed),
        },
        "authority_effects_of_replay": {
            "qualification_receipt_created": False,
            "current_use_binding_created": False,
            "authorization_grant_created": False,
            "dispatch_performed": False,
        },
    }
    return _seal(body)


def assess_progress(goal: dict, previous: dict, current: dict) -> dict:
    """Report non-monotonic M/R/E/U/B differences, not a progress score."""
    _validate_goal(goal)
    for snapshot in (previous, current):
        if snapshot.get("kind") != "StateSnapshot.v0" or snapshot.get("sha256") != digest(snapshot):
            raise ContractError("Unsealed or wrong StateSnapshot")
        if snapshot.get("goal_sha256") != digest(goal):
            raise ContractError("StateSnapshot bound to another goal")
    if (previous["run_id"] != current["run_id"] or
            previous["ledger_sha256"] != current["ledger_sha256"] or
            previous["through_sequence"] > current["through_sequence"] or
            _time(previous["assessed_at"], "previous.assessed_at") >
            _time(current["assessed_at"], "current.assessed_at")):
        raise ContractError("Snapshots are not an ordered replay of one Run")
    if (previous["through_sequence"] == current["through_sequence"] and
            _time(previous["assessed_at"], "previous.assessed_at") ==
            _time(current["assessed_at"], "current.assessed_at")):
        raise ContractError("Identical checkpoints provide no comparison")
    old_m = set(previous["recorded_milestones"])
    new_m = set(current["recorded_milestones"])
    old_e = set(previous["scoped_evidence_receipts"])
    new_e = set(current["scoped_evidence_receipts"])
    resource_delta: dict[str, int | None] = {}
    for key in goal["policy"]["resource_keys"]:
        old = previous["resources"].get(key)
        new = current["resources"].get(key)
        resource_delta[key] = (
            new["value"] - old["value"] if old and new and
            old["freshness"] == new["freshness"] == "CURRENT" else None
        )
    old_u = set(previous["open_uncertainties"])
    new_u = set(current["open_uncertainties"])
    body = {
        "kind": "ProgressAssessment.v0", "candidate_only": True,
        "goal_sha256": digest(goal),
        "previous_snapshot_sha256": previous["sha256"],
        "current_snapshot_sha256": current["sha256"],
        "vector": {
            "M": {"added": sorted(new_m - old_m), "lost": sorted(old_m - new_m),
                  "current": sorted(new_m)},
            "R": {"delta": resource_delta,
                  "confirmed_loss_keys": sorted(key for key, delta in resource_delta.items()
                                                if delta is not None and delta < 0)},
            "E": {"added_criteria": sorted(new_e - old_e),
                  "lost_criteria": sorted(old_e - new_e),
                  "current_criteria": sorted(new_e),
                  "candidate_artifact_counted": False},
            "U": {"opened": sorted(new_u - old_u), "resolved": sorted(old_u - new_u),
                  "current": sorted(new_u),
                  "missing_evidence_criteria": current["missing_evidence_criteria"],
                  "invalidated_goal_milestones": current["invalidated_goal_milestones"],
                  "invalidated_goal_evidence_criteria": current["invalidated_goal_evidence_criteria"],
                  "missing_or_stale_observations": sorted(
                      current["missing_critical"] + current["stale_critical"]),
                  "missing_or_stale_resources": sorted(
                      current["missing_resources"] + current["stale_resources"]),
                  "pending_operations": current["pending_operations"],
                  "pending_firings": current["pending_firings"]},
            "B": {"steps_consumed": current["budget"]["steps_used"] - previous["budget"]["steps_used"],
                  "steps_remaining": current["budget"]["steps_remaining"],
                  "wall_seconds_consumed": current["budget"]["wall_seconds_used"] - previous["budget"]["wall_seconds_used"],
                  "wall_seconds_remaining": current["budget"]["wall_seconds_remaining"]},
        },
    }
    return _seal(body)


def _propose_recovery(goal: dict, snapshot: dict, assessment: dict) -> dict:
    """Internal proposal over replayed inputs; dispatch remains outside this casepack."""
    _validate_goal(goal)
    if (snapshot.get("sha256") != digest(snapshot) or
            assessment.get("sha256") != digest(assessment) or
            snapshot.get("goal_sha256") != digest(goal) or
            assessment.get("current_snapshot_sha256") != snapshot["sha256"]):
        raise ContractError("Recovery inputs are not sealed and co-bound")
    vector = assessment["vector"]
    if (vector["U"]["pending_operations"] != snapshot["pending_operations"] or
            vector["U"]["pending_firings"] != snapshot["pending_firings"] or
            vector["U"]["current"] != snapshot["open_uncertainties"] or
            vector["U"]["missing_evidence_criteria"] != snapshot["missing_evidence_criteria"] or
            vector["U"]["invalidated_goal_milestones"] != snapshot["invalidated_goal_milestones"] or
            vector["U"]["invalidated_goal_evidence_criteria"] != snapshot["invalidated_goal_evidence_criteria"] or
            vector["U"]["missing_or_stale_observations"] != sorted(
                snapshot["missing_critical"] + snapshot["stale_critical"]
            ) or
            vector["U"]["missing_or_stale_resources"] != sorted(
                snapshot["missing_resources"] + snapshot["stale_resources"]
            )):
        raise ContractError("Progress vector conflicts with snapshot")
    if snapshot["historical_only"]:
        action, reason = "PAUSE", "historical_checkpoint_not_actionable"
    elif snapshot["pending_operations"] or snapshot["pending_firings"]:
        action, reason = "PAUSE", "indeterminate_or_pending_external_effect"
    elif snapshot["run_status"] == "indeterminate":
        action, reason = "PAUSE", "indeterminate_run"
    elif snapshot["run_status"] in {"cancelled", "limit_reached"}:
        action, reason = "STOP", "terminal_run_or_limit"
    elif snapshot["run_status"] in {"failed", "succeeded"}:
        action, reason = "REASSESS", "terminal_run_requires_review"
    elif snapshot["budget"]["steps_remaining"] == 0 or snapshot["budget"]["wall_seconds_remaining"] == 0:
        action, reason = "STOP", "frozen_budget_exhausted"
    elif (snapshot["missing_critical"] or snapshot["stale_critical"] or
          snapshot["missing_resources"] or snapshot["stale_resources"]):
        action, reason = "REOBSERVE", "critical_state_missing_or_stale"
    elif vector["M"]["lost"] or vector["E"]["lost_criteria"]:
        action, reason = "REASSESS", "goal_evidence_or_milestone_lost"
    elif (snapshot["invalidated_goal_milestones"] or
          snapshot["invalidated_goal_evidence_criteria"]):
        action, reason = "REASSESS", "goal_evidence_or_milestone_still_invalidated"
    elif vector["R"]["confirmed_loss_keys"]:
        action, reason = "REPLAN", "confirmed_resource_loss"
    elif (snapshot["missing_evidence_criteria"] and
          any(key in snapshot["unavailable_evidence_receipts"]
              for key in snapshot["missing_evidence_criteria"])):
        action, reason = "WAIT_FOR_EVIDENCE", "required_evidence_unavailable"
    elif (not vector["M"]["added"] and not vector["E"]["added_criteria"] and
          vector["B"]["steps_consumed"] >= goal["policy"]["stagnation_step_limit"]):
        action, reason = "REASSESS", "activity_without_goal_evidence_progress"
    else:
        action, reason = "CONTINUE", "no_recovery_trigger"
    assert action in RECOVERY_ACTIONS
    if action in goal["policy"]["forbidden_actions"]:
        action, reason = "PAUSE", "recovery_proposal_forbidden_by_frozen_goal"
    return _seal({
        "kind": "RecoveryDecision.v0", "state": "PROPOSED", "candidate_only": True,
        "goal_sha256": digest(goal), "snapshot_sha256": snapshot["sha256"],
        "assessment_sha256": assessment["sha256"], "action": action, "reason": reason,
        "historical_only": snapshot["historical_only"],
        "requires_host_state_refresh": True,
        "requires_existing_authorization": action not in {"STOP", "PAUSE", "WAIT_FOR_EVIDENCE"},
        "execution_authorized": False,
        "qualification_granted": False, "current_use_bound": False,
    })


def audit_compaction(goal: dict, ledger: dict, snapshot: dict, view: dict) -> dict:
    """Check a lossy context view without treating its summary as evidence."""
    _validate_goal(goal)
    _validate_ledger(goal, ledger)
    if (snapshot.get("sha256") != digest(snapshot) or
            snapshot.get("goal_sha256") != digest(goal) or
            snapshot.get("ledger_sha256") != digest(ledger) or
            snapshot.get("run_id") != ledger["run"]["id"]):
        raise ContractError("Unsealed or wrong compaction snapshot")
    rebuilt = reconstruct_snapshot(
        goal, ledger, expected_goal_sha256=digest(goal),
        expected_ledger_sha256=digest(ledger),
        through_sequence=snapshot["through_sequence"], assessed_at=snapshot["assessed_at"],
    )
    if rebuilt != snapshot:
        raise ContractError("Compaction snapshot does not match ledger replay")
    _fields(view, {"kind", "summary", "invariants", "retained_refs", "omitted_refs"},
            set(), "compaction_view")
    if view["kind"] != "CompactionView.v0":
        raise ContractError("Wrong compaction view")
    if not isinstance(view["summary"], str) or len(view["summary"]) > 100_000:
        raise ContractError("Compaction summary is not bounded text")
    maximum_refs = MAX_STEPS + MAX_RECEIPTS + MAX_ARTIFACTS + MAX_FIRINGS
    retained = _strings(view["retained_refs"], "compaction_view.retained_refs",
                        maximum=maximum_refs)
    omitted = _strings(view["omitted_refs"], "compaction_view.omitted_refs",
                       maximum=maximum_refs)
    all_refs = _visible_refs(ledger, snapshot["through_sequence"], snapshot["assessed_at"])
    if set(retained) & set(omitted) or set(retained) | set(omitted) != all_refs:
        raise ContractError("Compaction source partition is incomplete or invalid")
    expected_invariants = {
        "goal_sha256": digest(goal), "snapshot_sha256": snapshot["sha256"],
        "goal_constraints": {
            "target": goal["target"],
            "completion_predicate": goal["protocol"]["completion_predicate"],
            "stop_conditions": goal["protocol"]["stop_conditions"],
            "forbidden_actions": goal["policy"]["forbidden_actions"],
        },
        "historical_only": snapshot["historical_only"],
        "budget": snapshot["budget"],
        "pending_operations": snapshot["pending_operations"],
        "pending_firings": snapshot["pending_firings"],
        "missing_critical": snapshot["missing_critical"],
        "stale_critical": snapshot["stale_critical"],
        "missing_resources": snapshot["missing_resources"],
        "stale_resources": snapshot["stale_resources"],
        "open_uncertainties": snapshot["open_uncertainties"],
        "missing_evidence_criteria": snapshot["missing_evidence_criteria"],
        "invalidated_goal_milestones": snapshot["invalidated_goal_milestones"],
        "invalidated_goal_evidence_criteria": snapshot["invalidated_goal_evidence_criteria"],
        "qualification_granted": False, "current_use_bound": False,
        "execution_authorized": False,
    }
    reasons: list[str] = []
    if view["invariants"] != expected_invariants:
        reasons.append("invariant_mismatch")
    mandatory = {f"step:{step_id}" for step_id in snapshot["pending_operations"]}
    mandatory |= {f"firing:{event_id}" for event_id in snapshot["pending_firing_events"].values()}
    mandatory |= {f"receipt:{record['receipt_id']}" for record in snapshot["resources"].values()}
    mandatory |= {f"receipt:{record['receipt_id']}" for record in snapshot["observations"].values()}
    mandatory |= {f"step:{record['step_id']}" for record in snapshot["resources"].values()}
    mandatory |= {f"step:{record['step_id']}" for record in snapshot["observations"].values()}
    mandatory |= {f"receipt:{receipt_id}" for receipt_id in snapshot["recorded_milestones"].values()}
    mandatory |= {f"receipt:{receipt_id}" for receipt_id in snapshot["scoped_evidence_receipts"].values()}
    mandatory |= {f"artifact:{artifact_id}" for artifact_id in snapshot["scoped_evidence_artifacts"].values()}
    mandatory |= {f"receipt:{receipt_id}" for receipt_id in snapshot["unavailable_evidence_receipts"].values()}
    mandatory |= {f"receipt:{receipt_id}" for receipt_id in snapshot["uncertainty_receipts"].values()}
    mandatory |= {f"receipt:{receipt_id}" for receipt_id in snapshot["invalidated_receipts"]}
    mandatory |= {f"receipt:{receipt_id}" for receipt_id in snapshot["invalidated_receipts"].values()}
    if not mandatory <= set(retained):
        reasons.append("required_source_omitted")
    body = {
        "kind": "CompactionReceipt.v0", "candidate_only": True,
        "goal_sha256": digest(goal), "ledger_sha256": snapshot["ledger_sha256"],
        "snapshot_sha256": snapshot["sha256"], "view_sha256": digest(view),
        "result": "REJECTED" if reasons else "PASSED", "reasons": reasons,
        "retained_refs": sorted(retained), "omitted_refs": sorted(omitted),
        "summary_is_untrusted": True,
        "qualification_granted": False, "current_use_bound": False,
        "execution_authorized": False,
    }
    return _seal(body)


def _visible_refs(ledger: dict, cutoff: int, assessed_at: str) -> set[str]:
    steps = {item["id"] for item in ledger["steps"] if item["sequence"] <= cutoff}
    at = _time(assessed_at, "assessed_at")
    return (
        {f"step:{item}" for item in steps}
        | {f"receipt:{item['id']}" for item in ledger["receipts"]
           if item["step_id"] in steps and _time(item["recorded_at"], "receipt.recorded_at") <= at}
        | {f"artifact:{item['id']}" for item in ledger["artifacts"]
           if item["step_id"] in steps and _time(item["recorded_at"], "artifact.recorded_at") <= at}
        | {f"firing:{item['id']}" for item in ledger["scheduler_firings"]
           if item["sequence"] <= cutoff and _time(item["recorded_at"], "firing.recorded_at") <= at}
    )


def replay(
    goal: dict, ledger: dict, *, expected_goal_sha256: str,
    expected_ledger_sha256: str, expected_checkpoints_sha256: str,
    previous_sequence: int, current_sequence: int,
    previous_at: str, current_at: str, compaction_view: dict | None = None,
) -> dict:
    """Rebuild a four-artifact research bundle with no authority transition."""
    checkpoints = {
        "previous_sequence": previous_sequence, "current_sequence": current_sequence,
        "previous_at": previous_at, "current_at": current_at,
    }
    _hash(expected_checkpoints_sha256, "expected_checkpoints_sha256")
    if digest(checkpoints) != expected_checkpoints_sha256:
        raise ContractError("Pinned checkpoints digest mismatch")
    previous = reconstruct_snapshot(
        goal, ledger, expected_goal_sha256=expected_goal_sha256,
        expected_ledger_sha256=expected_ledger_sha256,
        through_sequence=previous_sequence, assessed_at=previous_at,
    )
    current = reconstruct_snapshot(
        goal, ledger, expected_goal_sha256=expected_goal_sha256,
        expected_ledger_sha256=expected_ledger_sha256,
        through_sequence=current_sequence, assessed_at=current_at,
    )
    assessment = assess_progress(goal, previous, current)
    if compaction_view is None:
        compaction_view = {
            "kind": "CompactionView.v0", "summary": "",
            "invariants": {
                "goal_sha256": expected_goal_sha256,
                "snapshot_sha256": current["sha256"],
                "goal_constraints": {
                    "target": goal["target"],
                    "completion_predicate": goal["protocol"]["completion_predicate"],
                    "stop_conditions": goal["protocol"]["stop_conditions"],
                    "forbidden_actions": goal["policy"]["forbidden_actions"],
                },
                "historical_only": current["historical_only"],
                "budget": current["budget"],
                "pending_operations": current["pending_operations"],
                "pending_firings": current["pending_firings"],
                "missing_critical": current["missing_critical"],
                "stale_critical": current["stale_critical"],
                "missing_resources": current["missing_resources"],
                "stale_resources": current["stale_resources"],
                "open_uncertainties": current["open_uncertainties"],
                "missing_evidence_criteria": current["missing_evidence_criteria"],
                "invalidated_goal_milestones": current["invalidated_goal_milestones"],
                "invalidated_goal_evidence_criteria": current["invalidated_goal_evidence_criteria"],
                "qualification_granted": False, "current_use_bound": False,
                "execution_authorized": False,
            },
            "retained_refs": sorted(_visible_refs(ledger, current_sequence, current_at)),
            "omitted_refs": [],
        }
    compaction = audit_compaction(goal, ledger, current, compaction_view)
    decision = _propose_recovery(goal, current, assessment)
    if compaction["result"] != "PASSED":
        decision = _seal({
            **{key: value for key, value in decision.items() if key != "sha256"},
            "action": "PAUSE", "reason": "compaction_invariants_rejected",
            "requires_existing_authorization": False,
            "compaction_receipt_sha256": compaction["sha256"],
        })
    return _seal({
        "contract": "LongHorizonRecoveryReplay.v0", "candidate_only": True,
        "goal_sha256": expected_goal_sha256,
        "ledger_sha256": expected_ledger_sha256,
        "checkpoints_sha256": expected_checkpoints_sha256,
        "previous_snapshot": previous, "state_snapshot": current,
        "progress_assessment": assessment, "recovery_decision": decision,
        "compaction_receipt": compaction,
        "qualification_granted": False, "current_use_bound": False,
        "execution_authorized": False,
    })
