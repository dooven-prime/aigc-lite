"""Adversarial replay checks for temporal, epistemic, and control boundaries."""

from __future__ import annotations

from copy import deepcopy

import pytest
from test_long_horizon_recovery_casepack import (
    ContractError,
    _at,
    _checkpoints_pin,
    _fact,
    _goal,
    _ledger,
    _step,
    audit_compaction,
    digest,
    replay,
)


def _replay(
    goal: dict,
    ledger: dict,
    *,
    previous_sequence: int,
    current_sequence: int,
    previous_at: str,
    current_at: str,
) -> dict:
    return replay(
        goal,
        ledger,
        expected_goal_sha256=digest(goal),
        expected_ledger_sha256=digest(ledger),
        expected_checkpoints_sha256=_checkpoints_pin(
            previous_sequence, current_sequence, previous_at, current_at,
        ),
        previous_sequence=previous_sequence,
        current_sequence=current_sequence,
        previous_at=previous_at,
        current_at=current_at,
    )


@pytest.mark.parametrize("changed", ["version", "goal_hash"])
def test_run_cannot_inherit_another_frozen_protocol_identity(changed: str) -> None:
    goal = _goal()
    ledger = _ledger(goal)
    _step(ledger)
    _step(ledger)
    if changed == "version":
        ledger["run"]["protocol_version"] += 1
    else:
        ledger["run"]["goal_sha256"] = "a" * 64

    with pytest.raises(ContractError, match="Run Protocol identity mismatch"):
        _replay(
            goal,
            ledger,
            previous_sequence=1,
            current_sequence=2,
            previous_at=_at(11),
            current_at=_at(21),
        )


def test_delayed_old_environment_revision_cannot_restore_old_resource() -> None:
    goal = _goal(resources=("pearls",))
    ledger = _ledger(goal)
    first = _step(ledger, kind="observation")
    _fact(
        ledger,
        first,
        {
            "kind": "resource",
            "key": "pearls",
            "value": 3,
            "environment_version": "world-1",
            "environment_revision": 1,
            "observed_at": first["finished_at"],
        },
    )
    second = _step(ledger, kind="observation")
    _fact(
        ledger,
        second,
        {
            "kind": "resource",
            "key": "pearls",
            "value": 0,
            "environment_version": "world-2",
            "environment_revision": 2,
            "observed_at": second["finished_at"],
        },
    )
    delayed = _step(ledger, kind="observation")
    _fact(
        ledger,
        delayed,
        {
            "kind": "resource",
            "key": "pearls",
            "value": 3,
            "environment_version": "world-1",
            "environment_revision": 1,
            "observed_at": first["finished_at"],
        },
    )

    with pytest.raises(ContractError, match="Environment revision rolled back"):
        _replay(
            goal,
            ledger,
            previous_sequence=1,
            current_sequence=3,
            previous_at=_at(11),
            current_at=_at(31),
        )


@pytest.mark.parametrize("mode", ["expired", "invalidated"])
def test_stale_resource_cannot_leave_recovery_at_continue(mode: str) -> None:
    goal = _goal(resources=("pearls",))
    ledger = _ledger(goal)
    observed = _step(ledger, kind="observation")
    receipt = _fact(ledger, observed, {
        "kind": "resource", "key": "pearls", "value": 3,
        "environment_version": "world-1", "environment_revision": 1,
        "observed_at": observed["finished_at"],
    })
    if mode == "invalidated":
        invalidation = _step(ledger, kind="verification")
        _fact(ledger, invalidation, {
            "kind": "invalidation", "target_receipt_id": receipt["id"],
            "reason": "source_revoked",
        })
    result = _replay(
        goal, ledger, previous_sequence=1,
        current_sequence=len(ledger["steps"]),
        previous_at=_at(11),
        current_at=_at(120) if mode == "expired" else _at(21),
    )
    assert result["state_snapshot"]["stale_resources"] == ["pearls"]
    assert result["recovery_decision"]["action"] == "REOBSERVE"


def test_compaction_cannot_drop_the_only_open_uncertainty_source() -> None:
    goal = _goal(evidence=("parent",))
    ledger = _ledger(goal)
    observed = _step(ledger, kind="research")
    receipt = _fact(ledger, observed, {
        "kind": "uncertainty", "key": "parent", "state": "open",
    })
    _step(ledger)
    baseline = _replay(
        goal, ledger, previous_sequence=1, current_sequence=2,
        previous_at=_at(11), current_at=_at(21),
    )
    snapshot = baseline["state_snapshot"]
    view = {
        "kind": "CompactionView.v0", "summary": "Parent source unresolved.",
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
        "retained_refs": baseline["compaction_receipt"]["retained_refs"],
        "omitted_refs": [],
    }
    assert audit_compaction(goal, ledger, snapshot, view)["result"] == "PASSED"

    dropped = deepcopy(view)
    dropped["retained_refs"].remove(f"receipt:{receipt['id']}")
    dropped["omitted_refs"] = [f"receipt:{receipt['id']}"]
    checked = audit_compaction(goal, ledger, snapshot, dropped)
    assert checked["result"] == "REJECTED"
    assert checked["reasons"] == ["required_source_omitted"]

    concealed = deepcopy(view)
    concealed["invariants"]["open_uncertainties"] = []
    checked = audit_compaction(goal, ledger, snapshot, concealed)
    assert checked["result"] == "REJECTED"
    assert checked["reasons"] == ["invariant_mismatch"]


def test_old_checkpoint_of_pinned_ledger_is_historical_and_paused() -> None:
    goal = _goal(resources=("pearls",))
    ledger = _ledger(goal)
    first = _step(ledger, kind="observation")
    _fact(ledger, first, {
        "kind": "resource", "key": "pearls", "value": 3,
        "environment_version": "world-1", "environment_revision": 1,
        "observed_at": first["finished_at"],
    })
    second = _step(ledger, kind="observation")
    _fact(ledger, second, {
        "kind": "resource", "key": "pearls", "value": 0,
        "environment_version": "world-2", "environment_revision": 2,
        "observed_at": second["finished_at"],
    })
    old = _replay(
        goal, ledger, previous_sequence=0, current_sequence=1,
        previous_at=_at(1), current_at=_at(11),
    )
    latest = _replay(
        goal, ledger, previous_sequence=1, current_sequence=2,
        previous_at=_at(11), current_at=_at(21),
    )
    assert old["ledger_sha256"] == latest["ledger_sha256"]
    assert old["state_snapshot"]["historical_only"] is True
    assert old["recovery_decision"]["action"] == "PAUSE"
    assert old["recovery_decision"]["reason"] == "historical_checkpoint_not_actionable"
    assert latest["state_snapshot"]["historical_only"] is False
    assert latest["recovery_decision"]["action"] == "REPLAN"


def test_resealed_snapshot_cannot_hide_pending_dispatch_from_internal_proposal() -> None:
    from aigc_lite_casepacks.long_horizon_recovery_v0.replay import _propose_recovery

    goal = _goal()
    ledger = _ledger(goal)
    _step(ledger)
    _step(ledger, kind="external_dispatch", status="indeterminate")
    baseline = _replay(
        goal, ledger, previous_sequence=1, current_sequence=2,
        previous_at=_at(11), current_at=_at(21),
    )
    assert baseline["recovery_decision"]["action"] == "PAUSE"
    snapshot = deepcopy(baseline["state_snapshot"])
    snapshot["pending_operations"] = []
    snapshot["sha256"] = digest(snapshot)
    assessment = deepcopy(baseline["progress_assessment"])
    assessment["current_snapshot_sha256"] = snapshot["sha256"]
    assessment["sha256"] = digest(assessment)

    with pytest.raises(ContractError, match="Progress vector conflicts"):
        _propose_recovery(goal, snapshot, assessment)


def test_research_step_cannot_reconcile_indeterminate_dispatch() -> None:
    goal = _goal()
    ledger = _ledger(goal)
    _step(ledger)
    dispatch = _step(ledger, kind="external_dispatch", status="indeterminate")
    research = _step(ledger, kind="research")
    _fact(
        ledger,
        research,
        {
            "kind": "operation_reconciled",
            "operation_step_id": dispatch["id"],
            "terminal_confirmed": True,
        },
    )

    with pytest.raises(
        ContractError, match="Operation reconciliation needs a host reconciliation Step"
    ):
        _replay(
            goal,
            ledger,
            previous_sequence=1,
            current_sequence=3,
            previous_at=_at(11),
            current_at=_at(31),
        )


def test_early_evidence_receipt_cannot_use_later_artifact() -> None:
    goal = _goal(evidence=("independent-parent",))
    ledger = _ledger(goal)
    early = _step(ledger, kind="verification")
    _fact(
        ledger,
        early,
        {
            "kind": "evidence",
            "criterion_id": "independent-parent",
            "target": goal["target"],
            "artifact_id": "future-artifact",
        },
    )
    later = _step(ledger, kind="verification")
    ledger["artifacts"].append(
        {
            "id": "future-artifact",
            "run_id": ledger["run"]["id"],
            "step_id": later["id"],
            "kind": "verified_evidence",
            "content_hash": "b" * 64,
            "candidate_only": False,
            "recorded_at": later["finished_at"],
        }
    )

    with pytest.raises(ContractError, match="Evidence Artifact is later than verification"):
        _replay(
            goal,
            ledger,
            previous_sequence=0,
            current_sequence=2,
            previous_at=_at(0),
            current_at=_at(21),
        )


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
def test_terminal_run_does_not_propose_continue(terminal_status: str) -> None:
    goal = _goal()
    ledger = _ledger(goal)
    _step(ledger)
    _step(ledger)
    ledger["run"]["status"] = terminal_status
    ledger["run"]["status_at"] = _at(21)

    result = _replay(
        goal,
        ledger,
        previous_sequence=1,
        current_sequence=2,
        previous_at=_at(11),
        current_at=_at(22),
    )
    assert result["previous_snapshot"]["run_status"] == "running"
    assert result["state_snapshot"]["run_status"] == terminal_status
    assert result["recovery_decision"]["action"] != "CONTINUE"
    assert result["recovery_decision"]["execution_authorized"] is False


def test_later_scheduler_terminal_event_does_not_rewrite_earlier_pending() -> None:
    goal = _goal()
    ledger = _ledger(goal)
    _step(ledger)
    scheduled = _step(ledger, kind="scheduler")
    ledger["scheduler_firings"].append(
        {
            "id": "event-scheduled",
            "firing_id": "firing-1",
            "run_id": ledger["run"]["id"],
            "sequence": scheduled["sequence"],
            "state": "scheduled",
            "recorded_at": scheduled["finished_at"],
        }
    )
    terminal = _step(ledger, kind="scheduler")
    ledger["scheduler_firings"].append(
        {
            "id": "event-terminal",
            "firing_id": "firing-1",
            "run_id": ledger["run"]["id"],
            "sequence": terminal["sequence"],
            "state": "succeeded",
            "recorded_at": terminal["finished_at"],
        }
    )

    result = _replay(
        goal,
        ledger,
        previous_sequence=2,
        current_sequence=3,
        previous_at=_at(21),
        current_at=_at(31),
    )
    assert result["previous_snapshot"]["pending_firings"] == ["firing-1"]
    assert result["previous_snapshot"]["pending_firing_events"] == {
        "firing-1": "event-scheduled"
    }
    assert result["state_snapshot"]["pending_firings"] == []


def test_verification_and_later_invalidation_remove_milestone_and_evidence() -> None:
    goal = _goal(evidence=("verified-source",))
    ledger = _ledger(goal)
    first = _step(ledger)
    _fact(ledger, first, {"kind": "uncertainty", "key": "verified-source", "state": "open"})
    verified = _step(ledger, kind="verification")
    ledger["artifacts"].append(
        {
            "id": "verified-artifact",
            "run_id": ledger["run"]["id"],
            "step_id": verified["id"],
            "kind": "verified_evidence",
            "content_hash": "c" * 64,
            "candidate_only": False,
            "recorded_at": verified["finished_at"],
        }
    )
    milestone = _fact(
        ledger,
        verified,
        {"kind": "milestone", "milestone_id": "target-reached"},
    )
    evidence = _fact(
        ledger,
        verified,
        {
            "kind": "evidence",
            "criterion_id": "verified-source",
            "target": goal["target"],
            "artifact_id": "verified-artifact",
        },
    )
    _fact(ledger, verified, {
        "kind": "uncertainty", "key": "verified-source", "state": "resolved",
    })
    invalidating = _step(ledger, kind="verification")
    for receipt in (milestone, evidence):
        _fact(
            ledger,
            invalidating,
            {
                "kind": "invalidation",
                "target_receipt_id": receipt["id"],
                "reason": "Later check invalidated the earlier result.",
            },
        )

    result = _replay(
        goal,
        ledger,
        previous_sequence=2,
        current_sequence=3,
        previous_at=_at(21),
        current_at=_at(31),
    )
    assert result["previous_snapshot"]["recorded_milestones"] == {
        "target-reached": milestone["id"]
    }
    assert result["previous_snapshot"]["scoped_evidence_receipts"] == {
        "verified-source": evidence["id"]
    }
    assert result["progress_assessment"]["vector"]["M"]["lost"] == [
        "target-reached"
    ]
    assert result["progress_assessment"]["vector"]["E"]["lost_criteria"] == [
        "verified-source"
    ]
    assert result["state_snapshot"]["recorded_milestones"] == {}
    assert result["state_snapshot"]["missing_evidence_criteria"] == [
        "verified-source"
    ]
    assert result["state_snapshot"]["open_uncertainties"] == ["verified-source"]


def test_compaction_rejects_self_sealed_snapshot_forged_from_same_ledger() -> None:
    goal = _goal()
    ledger = _ledger(goal)
    _step(ledger)
    _step(ledger)
    baseline = _replay(
        goal,
        ledger,
        previous_sequence=1,
        current_sequence=2,
        previous_at=_at(11),
        current_at=_at(21),
    )
    forged = deepcopy(baseline["state_snapshot"])
    forged["budget"]["steps_remaining"] += 1
    forged["sha256"] = digest(forged)
    view = {
        "kind": "CompactionView.v0",
        "summary": "Forged budget reports more steps available.",
        "invariants": {
            "goal_sha256": digest(goal),
            "snapshot_sha256": forged["sha256"],
            "goal_constraints": {
                "target": goal["target"],
                "completion_predicate": goal["protocol"]["completion_predicate"],
                "stop_conditions": goal["protocol"]["stop_conditions"],
                "forbidden_actions": goal["policy"]["forbidden_actions"],
            },
            "historical_only": forged["historical_only"],
            "budget": forged["budget"],
            "pending_operations": forged["pending_operations"],
            "pending_firings": forged["pending_firings"],
            "missing_critical": forged["missing_critical"],
            "stale_critical": forged["stale_critical"],
            "missing_resources": forged["missing_resources"],
            "stale_resources": forged["stale_resources"],
            "open_uncertainties": forged["open_uncertainties"],
            "missing_evidence_criteria": forged["missing_evidence_criteria"],
            "invalidated_goal_milestones": forged["invalidated_goal_milestones"],
            "invalidated_goal_evidence_criteria": forged["invalidated_goal_evidence_criteria"],
            "qualification_granted": False,
            "current_use_bound": False,
            "execution_authorized": False,
        },
        "retained_refs": baseline["compaction_receipt"]["retained_refs"],
        "omitted_refs": [],
    }

    with pytest.raises(ContractError, match="Compaction snapshot does not match ledger"):
        audit_compaction(goal, ledger, forged, view)
