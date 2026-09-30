"""Built-in deterministic review profile for execution-ledger integrity."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..core.contracts import RunStatus, StepKind, StepStatus
from ..core.review import ReviewFindingDraft, ReviewProfile, ReviewRule, ReviewSeverity

PROFILE_ID = "execution.integrity.v1"
PROFILE_VERSION = 1
SUBJECT_TYPE = "agent_run"

EXECUTION_INTEGRITY_PROFILE = ReviewProfile(
    profile_id=PROFILE_ID,
    version=PROFILE_VERSION,
    subject_type=SUBJECT_TYPE,
    description=(
        "Checks persisted Run/Step terminal state, ordering, failure provenance, "
        "tool provenance, and whether tool output exists only inside the step ledger."
    ),
    rules=(
        ReviewRule(
            "execution.terminal_completion",
            ReviewSeverity.P1,
            "Run status and completed_at must agree.",
        ),
        ReviewRule(
            "execution.step_sequence",
            ReviewSeverity.P1,
            "Persisted step sequence must be contiguous and start at one.",
        ),
        ReviewRule(
            "execution.status_alignment",
            ReviewSeverity.P1,
            "Run and Step terminal states must form a consistent execution history.",
        ),
        ReviewRule(
            "execution.error_provenance",
            ReviewSeverity.P1,
            "Failed terminal states must carry stable error provenance.",
        ),
        ReviewRule(
            "execution.tool_provenance",
            ReviewSeverity.P1,
            "Tool Steps must bind source, provider, risk, and local cancellation mode.",
        ),
        ReviewRule(
            "execution.output_materialization",
            ReviewSeverity.P3,
            "Successful tool output should be materialized when later reuse matters.",
        ),
    ),
)


def _ref(kind: str, identifier: str) -> dict[str, str]:
    return {"type": kind, "id": identifier}


class ExecutionIntegrityReviewer:
    """Pure evaluator over a privacy-bounded execution snapshot."""

    profile = EXECUTION_INTEGRITY_PROFILE

    def evaluate(self, snapshot: dict[str, Any]) -> tuple[ReviewFindingDraft, ...]:
        findings: list[ReviewFindingDraft] = []
        run = snapshot["run"]
        steps = snapshot["steps"]
        run_ref = (_ref("agent_run", run["id"]),)
        status = run["status"]
        completed_at = run.get("completed_at")

        if (status == RunStatus.RUNNING.value and completed_at) or (
            status != RunStatus.RUNNING.value and not completed_at
        ):
            findings.append(
                ReviewFindingDraft(
                    rule_id="execution.terminal_completion",
                    severity=ReviewSeverity.P1,
                    title="Run status and completion timestamp disagree",
                    summary=(
                        f"Run status is {status!r} while completed_at is "
                        f"{'present' if completed_at else 'missing'}."
                    ),
                    suggestion="Repair the writer and create a fresh execution record; do not rewrite this review.",
                    evidence_refs=run_ref,
                )
            )

        sequences = [int(step["sequence"]) for step in steps]
        expected = list(range(1, len(sequences) + 1))
        if sequences != expected:
            findings.append(
                ReviewFindingDraft(
                    rule_id="execution.step_sequence",
                    severity=ReviewSeverity.P1,
                    title="Step sequence is not contiguous",
                    summary=f"Observed sequence {sequences}; expected {expected}.",
                    suggestion="Preserve a single monotonic sequence allocator for every Run writer.",
                    evidence_refs=run_ref,
                )
            )

        step_statuses = Counter(step["status"] for step in steps)
        inconsistent = False
        alignment_reason = ""
        if status == RunStatus.SUCCEEDED.value:
            inconsistent = any(
                step["status"] != StepStatus.SUCCEEDED.value for step in steps
            )
            alignment_reason = "A succeeded Run contains a non-succeeded Step."
        elif status in {RunStatus.FAILED.value, RunStatus.LIMIT_REACHED.value}:
            inconsistent = not any(
                step["status"] == StepStatus.FAILED.value for step in steps
            )
            alignment_reason = "A failed or limit-reached Run has no failed terminal Step."
        elif status == RunStatus.CANCELLED.value:
            inconsistent = not any(
                step["status"] == StepStatus.CANCELLED.value for step in steps
            )
            alignment_reason = "A cancelled Run has no cancelled terminal Step."
        if inconsistent:
            findings.append(
                ReviewFindingDraft(
                    rule_id="execution.status_alignment",
                    severity=ReviewSeverity.P1,
                    title="Run and Step statuses are inconsistent",
                    summary=f"{alignment_reason} Step status counts: {dict(step_statuses)}.",
                    suggestion="Make the terminal Run update and terminal Step append one coherent transition.",
                    evidence_refs=run_ref,
                )
            )

        terminal_error_statuses = {
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
            RunStatus.LIMIT_REACHED.value,
        }
        if (status in terminal_error_statuses and not run.get("error_code")) or (
            status == RunStatus.SUCCEEDED.value and run.get("error_code")
        ):
            findings.append(
                ReviewFindingDraft(
                    rule_id="execution.error_provenance",
                    severity=ReviewSeverity.P1,
                    title="Run error provenance is incomplete",
                    summary=(
                        "A non-success terminal Run lacks error_code."
                        if not run.get("error_code")
                        else "A succeeded Run still carries an error_code."
                    ),
                    suggestion="Persist a stable error code only on the matching non-success terminal state.",
                    evidence_refs=run_ref,
                )
            )

        artifacts_by_step = Counter(
            item["step_id"] for item in snapshot["artifacts"] if item.get("step_id")
        )
        citations_by_step = Counter(
            item["step_id"] for item in snapshot["citations"] if item.get("step_id")
        )
        for step in steps:
            metadata = step.get("metadata") or {}
            step_ref = (_ref("agent_run", run["id"]), _ref("run_step", step["id"]))
            if step["status"] in {
                StepStatus.FAILED.value,
                StepStatus.CANCELLED.value,
            } and not metadata.get("error_code"):
                findings.append(
                    ReviewFindingDraft(
                        rule_id="execution.error_provenance",
                        severity=ReviewSeverity.P2,
                        title="Terminal Step lacks a stable error code",
                        summary=f"Step {step['id']} is {step['status']} without metadata.error_code.",
                        suggestion="Record the transport-neutral error code on the failing Step.",
                        evidence_refs=step_ref,
                    )
                )
            if step["kind"] != StepKind.TOOL.value:
                continue
            required = {"source", "provider_id", "risk"}
            missing = sorted(required - metadata.keys())
            if metadata.get("source") == "local" and not metadata.get("cancellation_mode"):
                missing.append("cancellation_mode")
            if missing:
                findings.append(
                    ReviewFindingDraft(
                        rule_id="execution.tool_provenance",
                        severity=ReviewSeverity.P1,
                        title="Tool Step provenance is incomplete",
                        summary=f"Step {step['id']} is missing: {', '.join(missing)}.",
                        suggestion="Persist the frozen ToolSpec execution projection with the Step.",
                        evidence_refs=step_ref,
                    )
                )
            if (
                step["status"] == StepStatus.SUCCEEDED.value
                and step.get("has_output")
                and artifacts_by_step[step["id"]] == 0
                and citations_by_step[step["id"]] == 0
            ):
                findings.append(
                    ReviewFindingDraft(
                        rule_id="execution.output_materialization",
                        severity=ReviewSeverity.P3,
                        title="Tool output exists only in the Step ledger",
                        summary=(
                            f"Step {step['id']} has output but no linked Artifact or Citation. "
                            "This is advisory and does not make the Run invalid."
                        ),
                        suggestion="Materialize reusable output as an Artifact and link its sources.",
                        evidence_refs=step_ref,
                    )
                )
        return tuple(findings)
