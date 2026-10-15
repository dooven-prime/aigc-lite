"""Hostile, candidate-only checks for the optional long-horizon casepack."""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "casepacks/long-horizon-recovery-v0/src"),
)

from aigc_lite_casepacks.long_horizon_recovery_v0.replay import (  # noqa: E402
    ContractError,
    audit_compaction,
    canonical_bytes,
    digest,
    replay,
)

from app.core.artifacts import ArtifactDraft, ArtifactKind  # noqa: E402
from app.core.contracts import RequestContext  # noqa: E402
from app.core.evidence import (  # noqa: E402
    ExecutionReceiptDraft,
    ProtocolDraft,
    ProtocolStatus,
)
from app.repository import SQLiteRepository  # noqa: E402
from app.services.artifacts import ArtifactService  # noqa: E402
from app.services.evidence import EvidenceService  # noqa: E402

EPOCH = datetime(2026, 10, 14, tzinfo=UTC)


def _at(seconds: int) -> str:
    return (EPOCH + timedelta(seconds=seconds)).isoformat()


def _goal(
    *,
    critical: tuple[str, ...] = (),
    resources: tuple[str, ...] = (),
    evidence: tuple[str, ...] = (),
    stagnation: int = 3,
) -> dict:
    return {
        "contract": "LongHorizonGoal.v0",
        "protocol": {
            "id": "protocol-1",
            "workspace_id": "workspace-1",
            "version": 1,
            "status": "frozen",
            "purpose": "Reach the frozen target without changing authority.",
            "scope": "Only this run and these recorded observations.",
            "completion_predicate": "Milestone and evidence criteria recorded.",
            "stop_conditions": ["Budget exhausted"],
            "budget": {"max_steps": 100, "max_wall_seconds": 10_000},
        },
        "target": "target-1",
        "policy": {
            "milestones": ["target-reached"],
            "evidence_criteria": list(evidence),
            "resource_keys": list(resources),
            "critical_observations": list(critical),
            "freshness_seconds": 30,
            "stagnation_step_limit": stagnation,
            "forbidden_actions": ["change-goal", "grant-authority"],
        },
    }


def _ledger(goal: dict) -> dict:
    return {
        "contract": "LongHorizonLedger.v0",
        "workspace_id": "workspace-1",
        "run": {
            "id": "run-1",
            "workspace_id": "workspace-1",
            "protocol_id": "protocol-1",
            "protocol_version": goal["protocol"]["version"],
            "goal_sha256": digest(goal),
            "started_at": _at(0),
            "status": "running",
            "status_at": _at(0),
        },
        "steps": [],
        "receipts": [],
        "artifacts": [],
        "scheduler_firings": [],
    }


def _step(ledger: dict, *, kind: str = "research", status: str = "succeeded") -> dict:
    sequence = len(ledger["steps"]) + 1
    step = {
        "id": f"step-{sequence}",
        "run_id": ledger["run"]["id"],
        "sequence": sequence,
        "kind": kind,
        "status": status,
        "finished_at": _at(sequence * 10),
    }
    ledger["steps"].append(step)
    return step


def _fact(ledger: dict, step: dict, fact: dict, *, status: str = "succeeded") -> dict:
    receipt = {
        "id": f"receipt-{len(ledger['receipts']) + 1}",
        "run_id": ledger["run"]["id"],
        "step_id": step["id"],
        "status": status,
        "output_digest": digest(fact),
        "recorded_at": step["finished_at"],
        "fact": fact,
    }
    ledger["receipts"].append(receipt)
    return receipt


def _checkpoints_pin(previous_sequence: int, current_sequence: int,
                     previous_at: str, current_at: str) -> str:
    return digest({
        "previous_sequence": previous_sequence,
        "current_sequence": current_sequence,
        "previous_at": previous_at,
        "current_at": current_at,
    })


def _replay(goal: dict, ledger: dict, *, previous_at: str | None = None,
            current_at: str | None = None, compaction_view: dict | None = None) -> dict:
    current_sequence = len(ledger["steps"])
    previous_at = previous_at or _at(11)
    current_at = current_at or _at(current_sequence * 10 + 1)
    return replay(
        goal,
        ledger,
        expected_goal_sha256=digest(goal),
        expected_ledger_sha256=digest(ledger),
        expected_checkpoints_sha256=_checkpoints_pin(1, current_sequence, previous_at, current_at),
        previous_sequence=1,
        current_sequence=current_sequence,
        previous_at=previous_at,
        current_at=current_at,
        compaction_view=compaction_view,
    )


def test_confirmed_resource_loss_replans_without_rewriting_goal() -> None:
    goal = _goal(resources=("pearls",))
    ledger = _ledger(goal)
    first = _step(ledger, kind="observation")
    _fact(ledger, first, {"kind": "resource", "key": "pearls", "value": 3,
                          "environment_version": "world-1", "environment_revision": 1,
                          "observed_at": first["finished_at"]})
    second = _step(ledger, kind="observation")
    _fact(ledger, second, {"kind": "resource", "key": "pearls", "value": 0,
                           "environment_version": "world-2", "environment_revision": 2,
                           "observed_at": second["finished_at"]})

    result = _replay(goal, ledger)
    assert result == _replay(goal, ledger)
    assert result["progress_assessment"]["vector"]["R"] == {
        "delta": {"pearls": -3}, "confirmed_loss_keys": ["pearls"],
    }
    decision = result["recovery_decision"]
    assert (decision["action"], decision["state"]) == ("REPLAN", "PROPOSED")
    assert decision["goal_sha256"] == digest(goal)
    assert decision["requires_host_state_refresh"] is True
    assert decision["execution_authorized"] is False


@pytest.mark.parametrize("mode", ["missing", "stale"])
def test_missing_or_stale_critical_state_requires_reobservation(mode: str) -> None:
    goal = _goal(critical=("location",))
    ledger = _ledger(goal)
    first = _step(ledger, kind="observation")
    if mode == "stale":
        _fact(ledger, first, {"kind": "observation", "key": "location",
                              "value": "base", "environment_version": "world-1",
                              "environment_revision": 1,
                              "observed_at": first["finished_at"]})
    _step(ledger)

    result = _replay(goal, ledger, current_at=_at(120) if mode == "stale" else None)
    snapshot = result["state_snapshot"]
    assert (snapshot["missing_critical"] if mode == "missing"
            else snapshot["stale_critical"]) == ["location"]
    assert result["recovery_decision"]["action"] == "REOBSERVE"
    assert result["recovery_decision"]["execution_authorized"] is False


def test_observation_expires_without_any_new_step() -> None:
    goal = _goal(critical=("location",))
    ledger = _ledger(goal)
    observed = _step(ledger, kind="observation")
    _fact(ledger, observed, {
        "kind": "observation", "key": "location", "value": "base",
        "environment_version": "world-1", "environment_revision": 1,
        "observed_at": observed["finished_at"],
    })

    result = replay(
        goal, ledger, expected_goal_sha256=digest(goal),
        expected_ledger_sha256=digest(ledger),
        expected_checkpoints_sha256=_checkpoints_pin(1, 1, _at(11), _at(120)),
        previous_sequence=1,
        current_sequence=1, previous_at=_at(11), current_at=_at(120),
    )
    assert result["previous_snapshot"]["stale_critical"] == []
    assert result["state_snapshot"]["stale_critical"] == ["location"]
    assert result["progress_assessment"]["vector"]["B"]["steps_consumed"] == 0
    assert result["recovery_decision"]["action"] == "REOBSERVE"


def test_new_environment_revision_stales_old_observation_even_with_same_label() -> None:
    goal = _goal(critical=("location",), resources=("pearls",))
    ledger = _ledger(goal)
    first = _step(ledger, kind="observation")
    _fact(ledger, first, {
        "kind": "observation", "key": "location", "value": "base",
        "environment_version": "world-1", "environment_revision": 1,
        "observed_at": first["finished_at"],
    })
    second = _step(ledger, kind="observation")
    _fact(ledger, second, {
        "kind": "resource", "key": "pearls", "value": 3,
        "environment_version": "world-1", "environment_revision": 2,
        "observed_at": second["finished_at"],
    })

    result = _replay(goal, ledger)
    assert result["state_snapshot"]["stale_critical"] == ["location"]
    assert result["state_snapshot"]["observations"]["location"]["freshness"] == "STALE"
    assert result["recovery_decision"]["action"] == "REOBSERVE"


@pytest.mark.parametrize("pending", ["dispatch", "scheduler"])
def test_indeterminate_external_effect_blocks_retries(pending: str) -> None:
    goal = _goal()
    ledger = _ledger(goal)
    _step(ledger)
    if pending == "dispatch":
        second = _step(ledger, kind="external_dispatch", status="indeterminate")
    else:
        second = _step(ledger, kind="scheduler")
        ledger["scheduler_firings"].append({
            "id": "firing-event-1", "firing_id": "firing-1",
            "run_id": "run-1", "sequence": 2,
            "state": "indeterminate", "recorded_at": second["finished_at"],
        })

    result = _replay(goal, ledger)
    snapshot = result["state_snapshot"]
    assert (snapshot["pending_operations"] if pending == "dispatch"
            else snapshot["pending_firings"]) == [second["id"] if pending == "dispatch"
                                            else "firing-1"]
    decision = result["recovery_decision"]
    assert decision["action"] == "PAUSE"
    assert decision["reason"] == "indeterminate_or_pending_external_effect"
    assert decision["requires_existing_authorization"] is False
    assert decision["execution_authorized"] is False
    assert len(ledger["steps"]) == 2  # replay did not synthesize a retry


def test_submitted_dispatch_stays_pending_until_explicit_reconciliation() -> None:
    goal = _goal()
    ledger = _ledger(goal)
    _step(ledger)
    submitted = _step(ledger, kind="external_dispatch", status="succeeded")
    pending = _replay(goal, ledger)
    assert pending["state_snapshot"]["pending_operations"] == [submitted["id"]]
    assert pending["recovery_decision"]["action"] == "PAUSE"

    reconciled = _step(ledger, kind="reconciliation")
    _fact(ledger, reconciled, {
        "kind": "operation_reconciled", "operation_step_id": submitted["id"],
        "terminal_confirmed": True,
    })
    after = _replay(goal, ledger)
    assert after["state_snapshot"]["pending_operations"] == []
    assert after["recovery_decision"]["execution_authorized"] is False


def test_future_scheduler_event_is_not_visible_before_its_recorded_time() -> None:
    goal = _goal()
    ledger = _ledger(goal)
    _step(ledger)
    _step(ledger, kind="scheduler")
    ledger["scheduler_firings"].append({
        "id": "event-future", "firing_id": "firing-1", "run_id": "run-1",
        "sequence": 2, "state": "indeterminate", "recorded_at": _at(120),
    })

    result = replay(
        goal, ledger, expected_goal_sha256=digest(goal),
        expected_ledger_sha256=digest(ledger),
        expected_checkpoints_sha256=_checkpoints_pin(2, 2, _at(21), _at(121)),
        previous_sequence=2,
        current_sequence=2, previous_at=_at(21), current_at=_at(121),
    )
    assert result["previous_snapshot"]["pending_firings"] == []
    assert result["state_snapshot"]["pending_firings"] == ["firing-1"]
    assert result["recovery_decision"]["action"] == "PAUSE"


def test_research_activity_and_mde_candidate_do_not_increment_goal_evidence() -> None:
    goal = _goal(evidence=("authenticated-parent-lineage",), stagnation=3)
    ledger = _ledger(goal)
    for _ in range(21):
        _step(ledger)
    ledger["artifacts"].append({
        "id": "mde-candidate-1", "run_id": "run-1", "step_id": "step-21",
        "kind": "DisambiguationCertificate.v0", "content_hash": "a" * 64,
        "candidate_only": True, "recorded_at": ledger["steps"][-1]["finished_at"],
    })

    result = _replay(goal, ledger)
    vector = result["progress_assessment"]["vector"]
    assert vector["E"] == {"added_criteria": [], "lost_criteria": [],
                           "current_criteria": [],
                           "candidate_artifact_counted": False}
    assert vector["B"]["steps_consumed"] == 20
    assert result["state_snapshot"]["missing_evidence_criteria"] == [
        "authenticated-parent-lineage"
    ]
    assert result["recovery_decision"]["action"] == "REASSESS"
    assert result["state_snapshot"]["authority_effects_of_replay"] == {
        "qualification_receipt_created": False,
        "current_use_binding_created": False,
        "authorization_grant_created": False,
        "dispatch_performed": False,
    }


def test_wrong_goal_or_ledger_pin_fails_closed() -> None:
    goal = _goal()
    ledger = _ledger(goal)
    _step(ledger)
    _step(ledger)
    pinned_goal, pinned_ledger = digest(goal), digest(ledger)

    modified_goal = deepcopy(goal)
    modified_goal["target"] = "weaker-target"
    matching_modified_ledger = deepcopy(ledger)
    matching_modified_ledger["run"]["goal_sha256"] = digest(modified_goal)
    with pytest.raises(ContractError, match="Pinned goal or ledger digest mismatch"):
        replay(modified_goal, matching_modified_ledger, expected_goal_sha256=pinned_goal,
               expected_ledger_sha256=pinned_ledger,
               expected_checkpoints_sha256=_checkpoints_pin(1, 2, _at(11), _at(21)),
               previous_sequence=1,
               current_sequence=2, previous_at=_at(11), current_at=_at(21))

    modified_ledger = deepcopy(ledger)
    modified_ledger["steps"][1]["status"] = "failed"
    with pytest.raises(ContractError, match="Pinned goal or ledger digest mismatch"):
        replay(goal, modified_ledger, expected_goal_sha256=pinned_goal,
               expected_ledger_sha256=pinned_ledger,
               expected_checkpoints_sha256=_checkpoints_pin(1, 2, _at(11), _at(21)),
               previous_sequence=1,
               current_sequence=2, previous_at=_at(11), current_at=_at(21))


def test_compaction_rejects_required_source_omission_and_authority_drift() -> None:
    goal = _goal(resources=("pearls",))
    ledger = _ledger(goal)
    first = _step(ledger, kind="observation")
    receipt = _fact(ledger, first, {"kind": "resource", "key": "pearls", "value": 3,
                                   "environment_version": "world-1", "environment_revision": 1,
                                   "observed_at": first["finished_at"]})
    _step(ledger)
    baseline = _replay(goal, ledger)
    snapshot = baseline["state_snapshot"]
    view = {
        "kind": "CompactionView.v0", "summary": "Remember the resource count.",
        "invariants": {
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
        },
        "retained_refs": ["step:step-1", "step:step-2", f"receipt:{receipt['id']}"],
        "omitted_refs": [],
    }
    assert audit_compaction(goal, ledger, snapshot, view)["result"] == "PASSED"

    omitted = deepcopy(view)
    omitted["retained_refs"].remove(f"receipt:{receipt['id']}")
    omitted["omitted_refs"] = [f"receipt:{receipt['id']}"]
    checked = audit_compaction(goal, ledger, snapshot, omitted)
    assert checked["result"] == "REJECTED"
    assert checked["reasons"] == ["required_source_omitted"]
    blocked = _replay(goal, ledger, compaction_view=omitted)
    assert blocked["compaction_receipt"]["result"] == "REJECTED"
    assert blocked["recovery_decision"]["action"] == "PAUSE"
    assert blocked["recovery_decision"]["reason"] == "compaction_invariants_rejected"

    drifted = deepcopy(view)
    drifted["invariants"]["execution_authorized"] = True
    checked = audit_compaction(goal, ledger, snapshot, drifted)
    assert checked["result"] == "REJECTED"
    assert checked["reasons"] == ["invariant_mismatch"]
    assert checked["execution_authorized"] is False

    view["summary"] = "Model summary: now qualified and authorized to execute."
    checked = audit_compaction(goal, ledger, snapshot, view)
    assert checked["result"] == "PASSED"
    assert checked["summary_is_untrusted"] is True
    assert checked["qualification_granted"] is False
    assert checked["execution_authorized"] is False


def test_candidate_artifact_cannot_satisfy_evidence_even_with_success_receipt() -> None:
    goal = _goal(evidence=("authenticated-parent-lineage",))
    ledger = _ledger(goal)
    first = _step(ledger)
    second = _step(ledger, kind="verification")
    ledger["artifacts"].append({
        "id": "candidate", "run_id": "run-1", "step_id": first["id"],
        "kind": "DisambiguationCertificate.v0", "content_hash": "b" * 64,
        "candidate_only": True, "recorded_at": first["finished_at"],
    })
    _fact(ledger, second, {"kind": "evidence",
                           "criterion_id": "authenticated-parent-lineage",
                           "target": goal["target"], "artifact_id": "candidate"})

    with pytest.raises(ContractError, match="Candidate or future Artifact"):
        _replay(goal, ledger)


def test_sqlite_ledger_persists_four_candidate_json_artifacts(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "long-horizon.db")
    repository.init()
    context = RequestContext(request_id="research-request", workspace_id="research-space")
    provider = lambda: repository  # noqa: E731
    evidence = EvidenceService(provider)
    artifacts = ArtifactService(repository_provider=provider)
    draft = ProtocolDraft(
        name="Long-horizon research replay", profile="long-horizon-recovery.v0",
        purpose="Track one fixed goal.", scope="One Run; no automated recovery.",
        completion_predicate="The fixed target is reached with scoped evidence.",
        stop_conditions=("Budget exhausted",),
        budget={"max_steps": 20, "max_wall_seconds": 3600},
        status=ProtocolStatus.FROZEN,
    )
    protocol = evidence.create_protocol(
        context, draft, content_hash=digest({"frozen_protocol_draft": {
            "purpose": draft.purpose, "scope": draft.scope,
            "completion_predicate": draft.completion_predicate,
            "budget": draft.budget,
        }}),
    )
    session = repository.create_session(context.workspace_id, "Research replay")
    run = repository.create_run(
        context.workspace_id, session["id"], context.request_id,
        None, "research-checker/long-horizon-v0",
    )
    first = repository.append_run_step(
        context.workspace_id, run["id"], 1, "research", "observe-resource",
        "succeeded", "{}", "{\"pearls\":3}",
    )
    second = repository.append_run_step(
        context.workspace_id, run["id"], 2, "research", "record-activity",
        "succeeded", "{}", "{}",
    )
    observation_fact = {
        "kind": "resource", "key": "pearls", "value": 3,
        "environment_version": "world-1", "environment_revision": 1,
        "observed_at": first["created_at"],
    }
    source = artifacts.create_artifact(
        context,
        ArtifactDraft(name="resource-observation.json", kind=ArtifactKind.JSON,
                      media_type="application/json",
                      content_text=canonical_bytes(observation_fact).decode(),
                      metadata={"role": "candidate_input"}),
        run_id=run["id"], step_id=first["id"],
    )
    input_receipt = evidence.create_receipt(
        context, protocol["id"],
        ExecutionReceiptDraft(
            status="succeeded", input_digest=source["content_hash"],
            output_digest=digest(observation_fact), run_id=run["id"],
            artifact_ids=(source["id"],),
            metadata={"step_id": first["id"], "fact": observation_fact,
                      "candidate_only": True},
        ),
    )
    repository.finish_run(context.workspace_id, run["id"], "succeeded")

    # Export only the host facts that existed before the casepack generated outputs.
    stored_protocol = evidence.get_protocol(context, protocol["id"])
    stored_run = repository.get_run(context.workspace_id, run["id"])
    assert stored_run is not None
    goal = {
        "contract": "LongHorizonGoal.v0",
        "protocol": {
            "id": stored_protocol["id"], "workspace_id": context.workspace_id,
            "version": stored_protocol["version"], "status": stored_protocol["status"],
            "purpose": stored_protocol["purpose"], "scope": stored_protocol["scope"],
            "completion_predicate": stored_protocol["completion_predicate"],
            "stop_conditions": stored_protocol["stop_conditions"],
            "budget": stored_protocol["budget"],
        },
        "target": "resource recovery without authority change",
        "policy": {
            "milestones": ["recovered"], "evidence_criteria": [],
            "resource_keys": ["pearls"], "critical_observations": [],
            "freshness_seconds": 60, "stagnation_step_limit": 1,
            "forbidden_actions": ["change-goal", "grant-authority"],
        },
    }
    ledger = {
        "contract": "LongHorizonLedger.v0", "workspace_id": context.workspace_id,
        "run": {
            "id": stored_run["id"], "workspace_id": context.workspace_id,
            "protocol_id": stored_protocol["id"],
            "protocol_version": stored_protocol["version"],
            "goal_sha256": digest(goal),
            "started_at": stored_run["created_at"], "status": stored_run["status"],
            "status_at": stored_run["completed_at"],
        },
        "steps": [{
            "id": step["id"], "run_id": step["run_id"],
            "sequence": step["sequence"], "kind": step["kind"],
            "status": step["status"], "finished_at": step["created_at"],
        } for step in stored_run["steps"]],
        "receipts": [{
            "id": input_receipt["id"], "run_id": input_receipt["run_id"],
            "step_id": input_receipt["metadata"]["step_id"],
            "status": input_receipt["status"],
            "output_digest": input_receipt["output_digest"],
            "recorded_at": input_receipt["created_at"],
            "fact": input_receipt["metadata"]["fact"],
        }],
        "artifacts": [{
            "id": source["id"], "run_id": source["run_id"],
            "step_id": source["step_id"], "kind": source["kind"],
            "content_hash": source["content_hash"], "candidate_only": True,
            "recorded_at": source["created_at"],
        }],
        "scheduler_firings": [],
    }
    first_at = datetime.fromisoformat(first["created_at"])
    second_at = datetime.fromisoformat(second["created_at"])
    previous_at = (first_at + timedelta(seconds=1)).isoformat()
    current_at = (second_at + timedelta(seconds=2)).isoformat()
    result = replay(
        goal, ledger, expected_goal_sha256=digest(goal),
        expected_ledger_sha256=digest(ledger),
        expected_checkpoints_sha256=_checkpoints_pin(1, 2, previous_at, current_at),
        previous_sequence=1,
        current_sequence=2,
        previous_at=previous_at,
        current_at=current_at,
    )
    assert result["recovery_decision"]["state"] == "PROPOSED"
    assert result["recovery_decision"]["action"] == "REASSESS"
    assert result["recovery_decision"]["execution_authorized"] is False
    names = (
        "state_snapshot", "progress_assessment", "recovery_decision",
        "compaction_receipt",
    )
    outputs = []
    for name in names:
        body = canonical_bytes(result[name]).decode()
        stored = artifacts.create_artifact(
            context,
            ArtifactDraft(name=f"{name}.json", kind=ArtifactKind.JSON,
                          media_type="application/json", content_text=body,
                          metadata={"role": name, "candidate_only": True}),
            run_id=run["id"], step_id=second["id"],
        )
        # ArtifactService reserializes/redacts JSON before its own byte hash.
        assert stored["content_hash"] == hashlib.sha256(
            stored["content_text"].encode()
        ).hexdigest()
        assert json.loads(artifacts.get(context, stored["id"])["content_text"]) == result[name]
        outputs.append(stored)
    result_receipt = evidence.create_receipt(
        context, protocol["id"],
        ExecutionReceiptDraft(
            status="candidate_result_recorded", input_digest=digest(ledger),
            output_digest=result["sha256"], run_id=run["id"],
            artifact_ids=tuple(item["id"] for item in outputs),
            metadata={"candidate_only": True, "step_id": second["id"],
                      "goal_sha256": digest(goal), "ledger_sha256": digest(ledger)},
        ),
    )
    retrieved = evidence.get_protocol(context, protocol["id"])
    assert {item["id"] for item in retrieved["receipts"]} == {
        input_receipt["id"], result_receipt["id"],
    }
    assert set(result_receipt["artifact_ids"]) == {item["id"] for item in outputs}
    assert {item["id"] for item in repository.get_run(context.workspace_id, run["id"])["artifacts"]} >= {
        item["id"] for item in outputs
    }
    assert result_receipt["metadata"]["candidate_only"] is True
    assert result["qualification_granted"] is False
    assert result["current_use_bound"] is False
    assert result["execution_authorized"] is False
