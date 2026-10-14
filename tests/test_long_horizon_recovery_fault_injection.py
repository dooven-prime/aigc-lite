"""Five deterministic failure injections over a frozen, candidate-only goal.

These are replay tests, not autonomous recovery or host authorization tests.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from test_long_horizon_recovery_casepack import (
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


def _replay_window(
    goal: dict,
    ledger: dict,
    previous: int,
    current: int,
    *,
    compaction_view: dict | None = None,
) -> dict:
    previous_at = _at(previous * 10 + 1)
    current_at = _at(current * 10 + 1)
    pinned = {
        "expected_goal_sha256": digest(goal),
        "expected_ledger_sha256": digest(ledger),
        "expected_checkpoints_sha256": _checkpoints_pin(
            previous, current, previous_at, current_at,
        ),
    }
    arguments = {
        "previous_sequence": previous,
        "current_sequence": current,
        "previous_at": previous_at,
        "current_at": current_at,
        "compaction_view": compaction_view,
    }
    first = replay(goal, ledger, **pinned, **arguments)
    second = replay(goal, ledger, **pinned, **arguments)
    assert first == second
    assert first["sha256"] == second["sha256"] == digest(first)
    return first


def _assert_proposal_only(bundle: dict) -> None:
    decision = bundle["recovery_decision"]
    assert bundle["candidate_only"] is True
    assert decision["state"] == "PROPOSED"
    assert decision["candidate_only"] is True
    assert decision["execution_authorized"] is False
    assert decision["qualification_granted"] is False
    assert decision["current_use_bound"] is False
    assert bundle["qualification_granted"] is False
    assert bundle["current_use_bound"] is False
    assert bundle["execution_authorized"] is False
    assert bundle["state_snapshot"]["authority_effects_of_replay"] == {
        "qualification_receipt_created": False,
        "current_use_binding_created": False,
        "authorization_grant_created": False,
        "dispatch_performed": False,
    }


def test_resource_loss_replans_then_repeated_safety_activity_reassesses() -> None:
    goal = _goal(resources=("pearls",), stagnation=3)
    goal["policy"]["freshness_seconds"] = 1_000
    ledger = _ledger(goal)
    initial = _step(ledger, kind="observation")
    _fact(ledger, initial, {
        "kind": "resource", "key": "pearls", "value": 3,
        "environment_version": "world-1", "environment_revision": 1,
        "observed_at": initial["finished_at"],
    })
    loss = _step(ledger, kind="observation")
    _fact(ledger, loss, {
        "kind": "resource", "key": "pearls", "value": 0,
        "environment_version": "world-2", "environment_revision": 2,
        "observed_at": loss["finished_at"],
    })

    replanning = _replay_window(goal, ledger, 1, 2)
    assert replanning["progress_assessment"]["vector"]["R"]["delta"] == {
        "pearls": -3,
    }
    assert replanning["recovery_decision"]["action"] == "REPLAN"
    assert replanning["recovery_decision"]["reason"] == "confirmed_resource_loss"
    _assert_proposal_only(replanning)

    # Candidate notes describe repeated safety routines, not a restored resource,
    # verified milestone, or relevant evidence. They cannot count as goal progress.
    for index in range(4):
        step = _step(ledger, kind="tool")
        ledger["artifacts"].append({
            "id": f"safety-note-{index + 1}",
            "run_id": ledger["run"]["id"],
            "step_id": step["id"],
            "kind": "resource-protection-note",
            "content_hash": digest({"routine": "protect-resources", "iteration": index}),
            "candidate_only": True,
            "recorded_at": step["finished_at"],
        })

    reassessment = _replay_window(goal, ledger, 2, 6)
    vector = reassessment["progress_assessment"]["vector"]
    assert vector["R"]["delta"] == {"pearls": 0}
    assert vector["M"]["added"] == []
    assert vector["E"]["added_criteria"] == []
    assert vector["B"]["steps_consumed"] == 4
    assert reassessment["state_snapshot"]["resources"]["pearls"]["freshness"] == "CURRENT"
    assert reassessment["recovery_decision"]["action"] == "REASSESS"
    assert reassessment["recovery_decision"]["reason"] == "activity_without_goal_evidence_progress"
    assert reassessment["goal_sha256"] == replanning["goal_sha256"]
    _assert_proposal_only(reassessment)


def test_bio_research_activity_does_not_fill_missing_parent_lineage() -> None:
    goal = _goal(evidence=("authenticated-parent-lineage",), stagnation=4)
    ledger = _ledger(goal)
    for index in range(12):
        step = _step(ledger, kind="research")
        if index in {3, 7, 11}:
            ledger["artifacts"].append({
                "id": f"candidate-summary-{index + 1}",
                "run_id": ledger["run"]["id"],
                "step_id": step["id"],
                "kind": "research-summary",
                "content_hash": digest({"candidate-summary": index + 1}),
                "candidate_only": True,
                "recorded_at": step["finished_at"],
            })

    result = _replay_window(goal, ledger, 1, 12)
    vector = result["progress_assessment"]["vector"]
    assert vector["E"] == {
        "added_criteria": [],
        "lost_criteria": [],
        "current_criteria": [],
        "candidate_artifact_counted": False,
    }
    assert vector["B"]["steps_consumed"] == 11
    assert result["state_snapshot"]["missing_evidence_criteria"] == [
        "authenticated-parent-lineage",
    ]
    assert result["state_snapshot"]["scoped_evidence_receipts"] == {}
    assert result["recovery_decision"]["action"] == "REASSESS"
    assert result["recovery_decision"]["reason"] == "activity_without_goal_evidence_progress"
    _assert_proposal_only(result)


def test_host_exported_citation_source_invalidation_cannot_continue() -> None:
    goal = _goal(evidence=("citation-source-binding",), stagnation=10)
    ledger = _ledger(goal)
    verified = _step(ledger, kind="verification")
    ledger["artifacts"].append({
        "id": "source-binding-artifact",
        "run_id": ledger["run"]["id"],
        "step_id": verified["id"],
        "kind": "verified_evidence",
        "content_hash": digest({"source": "frozen-citation-source", "revision": 1}),
        "candidate_only": False,
        "recorded_at": verified["finished_at"],
    })
    source_receipt = _fact(ledger, verified, {
        "kind": "evidence",
        "criterion_id": "citation-source-binding",
        "target": goal["target"],
        "artifact_id": "source-binding-artifact",
    })
    before = _replay_window(goal, ledger, 0, 1)
    assert before["state_snapshot"]["scoped_evidence_receipts"] == {
        "citation-source-binding": source_receipt["id"],
    }
    _assert_proposal_only(before)

    # The host exports an invalidation fact; this casepack does not independently
    # authenticate publication history or create an invalidation decision.
    invalidating = _step(ledger, kind="verification")
    invalidation = _fact(ledger, invalidating, {
        "kind": "invalidation",
        "target_receipt_id": source_receipt["id"],
        "reason": "source-binding-revoked-by-host",
    })
    result = _replay_window(goal, ledger, 1, 2)
    assert result["progress_assessment"]["vector"]["E"]["lost_criteria"] == [
        "citation-source-binding",
    ]
    assert result["state_snapshot"]["scoped_evidence_receipts"] == {}
    assert result["state_snapshot"]["invalidated_receipts"] == {
        source_receipt["id"]: invalidation["id"],
    }
    assert result["recovery_decision"]["action"] == "REASSESS"
    assert result["recovery_decision"]["reason"] == "goal_evidence_or_milestone_lost"
    _assert_proposal_only(result)

    # A later ordinary research Step does not repair the source binding. The
    # one-checkpoint E.lost delta is empty, but invalidation remains blocking.
    _step(ledger, kind="research")
    unresolved = _replay_window(goal, ledger, 2, 3)
    assert unresolved["progress_assessment"]["vector"]["E"]["lost_criteria"] == []
    assert unresolved["state_snapshot"]["invalidated_goal_evidence_criteria"] == [
        "citation-source-binding",
    ]
    assert unresolved["recovery_decision"]["action"] == "REASSESS"
    assert unresolved["recovery_decision"]["reason"] == (
        "goal_evidence_or_milestone_still_invalidated"
    )
    _assert_proposal_only(unresolved)

    # Only a new, host-projected verification of the same frozen criterion
    # clears this replay marker. It does not authenticate the source itself.
    repaired = _step(ledger, kind="verification")
    ledger["artifacts"].append({
        "id": "source-binding-artifact-repaired",
        "run_id": ledger["run"]["id"],
        "step_id": repaired["id"],
        "kind": "verified_evidence",
        "content_hash": digest({"source": "frozen-citation-source", "revision": 2}),
        "candidate_only": False,
        "recorded_at": repaired["finished_at"],
    })
    new_receipt = _fact(ledger, repaired, {
        "kind": "evidence",
        "criterion_id": "citation-source-binding",
        "target": goal["target"],
        "artifact_id": "source-binding-artifact-repaired",
    })
    renewed = _replay_window(goal, ledger, 3, 4)
    assert renewed["state_snapshot"]["invalidated_goal_evidence_criteria"] == []
    assert renewed["state_snapshot"]["scoped_evidence_receipts"] == {
        "citation-source-binding": new_receipt["id"],
    }
    assert renewed["progress_assessment"]["vector"]["E"]["added_criteria"] == [
        "citation-source-binding",
    ]
    assert renewed["recovery_decision"]["action"] == "CONTINUE"
    _assert_proposal_only(renewed)


def test_lost_verified_milestone_alone_cannot_continue() -> None:
    goal = _goal(stagnation=10)
    ledger = _ledger(goal)
    verified = _step(ledger, kind="verification")
    milestone = _fact(ledger, verified, {
        "kind": "milestone", "milestone_id": "target-reached",
    })
    before = _replay_window(goal, ledger, 0, 1)
    assert before["state_snapshot"]["recorded_milestones"] == {
        "target-reached": milestone["id"],
    }

    invalidating = _step(ledger, kind="verification")
    _fact(ledger, invalidating, {
        "kind": "invalidation",
        "target_receipt_id": milestone["id"],
        "reason": "host-retracted-milestone",
    })
    result = _replay_window(goal, ledger, 1, 2)
    assert result["progress_assessment"]["vector"]["M"]["lost"] == [
        "target-reached",
    ]
    assert result["progress_assessment"]["vector"]["E"]["lost_criteria"] == []
    assert result["recovery_decision"]["action"] == "REASSESS"
    assert result["recovery_decision"]["reason"] == "goal_evidence_or_milestone_lost"
    _assert_proposal_only(result)

    _step(ledger, kind="research")
    unresolved = _replay_window(goal, ledger, 2, 3)
    assert unresolved["progress_assessment"]["vector"]["M"]["lost"] == []
    assert unresolved["state_snapshot"]["invalidated_goal_milestones"] == [
        "target-reached",
    ]
    assert unresolved["recovery_decision"]["action"] == "REASSESS"
    assert unresolved["recovery_decision"]["reason"] == (
        "goal_evidence_or_milestone_still_invalidated"
    )
    _assert_proposal_only(unresolved)


def test_invalidating_old_receipt_does_not_revoke_current_replacement() -> None:
    goal = _goal(evidence=("citation-source-binding",), stagnation=10)
    ledger = _ledger(goal)
    first = _step(ledger, kind="verification")
    ledger["artifacts"].append({
        "id": "old-source-artifact",
        "run_id": ledger["run"]["id"],
        "step_id": first["id"],
        "kind": "verified_evidence",
        "content_hash": digest({"source": "old"}),
        "candidate_only": False,
        "recorded_at": first["finished_at"],
    })
    old_receipt = _fact(ledger, first, {
        "kind": "evidence", "criterion_id": "citation-source-binding",
        "target": goal["target"], "artifact_id": "old-source-artifact",
    })
    second = _step(ledger, kind="verification")
    ledger["artifacts"].append({
        "id": "new-source-artifact",
        "run_id": ledger["run"]["id"],
        "step_id": second["id"],
        "kind": "verified_evidence",
        "content_hash": digest({"source": "new"}),
        "candidate_only": False,
        "recorded_at": second["finished_at"],
    })
    current_receipt = _fact(ledger, second, {
        "kind": "evidence", "criterion_id": "citation-source-binding",
        "target": goal["target"], "artifact_id": "new-source-artifact",
    })
    third = _step(ledger, kind="verification")
    _fact(ledger, third, {
        "kind": "invalidation",
        "target_receipt_id": old_receipt["id"],
        "reason": "old-source-withdrawn",
    })
    result = _replay_window(goal, ledger, 2, 3)
    assert result["state_snapshot"]["scoped_evidence_receipts"] == {
        "citation-source-binding": current_receipt["id"],
    }
    assert result["state_snapshot"]["invalidated_goal_evidence_criteria"] == []
    assert result["progress_assessment"]["vector"]["E"]["lost_criteria"] == []
    assert result["recovery_decision"]["action"] == "CONTINUE"
    _assert_proposal_only(result)


@pytest.mark.parametrize(
    ("omission", "reason"),
    [
        ("acceptance_pin", "invariant_mismatch"),
        ("pending_marker", "invariant_mismatch"),
        ("pending_source", "required_source_omitted"),
        ("observation_source", "required_source_omitted"),
    ],
)
def test_compaction_omission_rejected_and_cannot_resume(
    omission: str,
    reason: str,
) -> None:
    goal = _goal(resources=("pearls",))
    ledger = _ledger(goal)
    observed = _step(ledger, kind="observation")
    resource_receipt = _fact(ledger, observed, {
        "kind": "resource", "key": "pearls", "value": 3,
        "environment_version": "world-1", "environment_revision": 1,
        "observed_at": observed["finished_at"],
    })
    pending = _step(ledger, kind="external_dispatch", status="indeterminate")
    baseline = _replay_window(goal, ledger, 1, 2)
    snapshot = baseline["state_snapshot"]
    assert snapshot["pending_operations"] == [pending["id"]]
    assert baseline["compaction_receipt"]["result"] == "PASSED"
    assert baseline["recovery_decision"]["action"] == "PAUSE"
    assert baseline["recovery_decision"]["reason"] == (
        "indeterminate_or_pending_external_effect"
    )
    _assert_proposal_only(baseline)
    assert [step["id"] for step in ledger["steps"] if step["kind"] == "external_dispatch"] == [
        pending["id"],
    ]
    view = {
        "kind": "CompactionView.v0",
        "summary": "A lossy summary is not a source of authority.",
        "invariants": {
            "goal_sha256": digest(goal),
            "snapshot_sha256": snapshot["sha256"],
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
            "qualification_granted": False,
            "current_use_bound": False,
            "execution_authorized": False,
        },
        "retained_refs": baseline["compaction_receipt"]["retained_refs"],
        "omitted_refs": [],
    }
    assert audit_compaction(goal, ledger, snapshot, view)["result"] == "PASSED"
    assert _replay_window(goal, ledger, 1, 2, compaction_view=view)[
        "compaction_receipt"
    ]["result"] == "PASSED"
    view = deepcopy(view)
    if omission == "acceptance_pin":
        # Preserve the goal hash; a lossy summary must also retain the explicit
        # acceptance condition rather than relying on an opaque hash alone.
        del view["invariants"]["goal_constraints"]["completion_predicate"]
    elif omission == "pending_marker":
        view["invariants"]["pending_operations"] = []
    else:
        omitted_ref = (
            f"step:{pending['id']}" if omission == "pending_source"
            else f"receipt:{resource_receipt['id']}"
        )
        view["retained_refs"].remove(omitted_ref)
        view["omitted_refs"].append(omitted_ref)

    result = _replay_window(goal, ledger, 1, 2, compaction_view=view)
    assert result["compaction_receipt"]["result"] == "REJECTED"
    assert result["compaction_receipt"]["reasons"] == [reason]
    assert result["recovery_decision"]["action"] == "PAUSE"
    assert result["recovery_decision"]["reason"] == "compaction_invariants_rejected"
    _assert_proposal_only(result)
    assert [step["id"] for step in ledger["steps"] if step["kind"] == "external_dispatch"] == [
        pending["id"],
    ]
