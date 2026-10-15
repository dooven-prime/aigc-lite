"""Read one Run's native ledger facts without assigning research authority.

The caller supplies a query bound to one database read transaction.  Rows are
kept in their native shapes: in particular, a recorded receipt is not thereby
a trusted verification, and claim-related rows are not attributed to this Run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..core.errors import LedgerProjectionTooLargeError

Rows = Callable[[str, dict[str, Any]], list[dict[str, Any]]]
MAX_LEDGER_SNAPSHOT_ROWS = 20_000
MAX_LEDGER_SNAPSHOT_BYTES = 16_000_000


def collect_bounded_ledger_rows(
    source: Any, budget: dict[str, int],
) -> list[dict[str, Any]]:
    """Materialize a database cursor only while the shared snapshot fits v0."""
    result: list[dict[str, Any]] = []
    for source_row in source:
        row = dict(source_row)
        budget["rows"] += 1
        budget["bytes"] += sum(
            len(str(key).encode("utf-8")) + (
                len(value) if isinstance(value, bytes)
                else len(str(value).encode("utf-8")) if value is not None else 0
            )
            for key, value in row.items()
        )
        if (budget["rows"] > MAX_LEDGER_SNAPSHOT_ROWS or
                budget["bytes"] > MAX_LEDGER_SNAPSHOT_BYTES):
            raise LedgerProjectionTooLargeError()
        result.append(row)
    return result


def read_run_ledger_rows(
    rows: Rows, tenant_id: str, run_id: str
) -> dict[str, Any] | None:
    """Collect all directly linked rows from a single consistent SQL snapshot."""

    identity = {"tenant_id": tenant_id, "run_id": run_id}
    runs = rows(
        "SELECT * FROM agent_runs WHERE tenant_id = :tenant_id AND id = :run_id",
        identity,
    )
    if not runs:
        return None

    direct = {
        "steps": ("run_steps", "sequence, id"),
        "model_usage_receipts": (
            "model_usage_receipts", "iteration, created_at, attempt_id"
        ),
        "artifacts": ("artifacts", "created_at, id"),
        "execution_receipts": ("execution_receipts", "created_at, id"),
        "enforcement_dispatches": ("enforcement_dispatches", "created_at, id"),
        "authorization_invocations": (
            "authorization_invocations", "consumed_at, id"
        ),
        "research_verification_attempts": (
            "research_verification_attempts", "created_at, id"
        ),
    }
    goal_fields = (
        "goal_contract", "goal_protocol_id", "goal_protocol_version",
        "goal_protocol_sha256", "goal_sha256", "goal_json",
    )
    goal_requested = any(runs[0].get(field) is not None for field in goal_fields)
    goal_protocol_id = runs[0].get("goal_protocol_id")
    # A migration adds nullable Goal columns to historical Run rows.  Do not
    # let those NULLs silently change the byte identity of a v0 projection.
    legacy_run = (
        runs[0] if goal_requested
        else {key: value for key, value in runs[0].items() if key not in goal_fields}
    )
    snapshot: dict[str, Any] = {
        "tenant_id": tenant_id,
        "run_id": run_id,
        "run": legacy_run,
    }
    snapshot["goal_protocols"] = (
        rows(
            "SELECT * FROM evidence_protocols WHERE tenant_id = :tenant_id "
            "AND id = :goal_protocol_id",
            {"tenant_id": tenant_id, "goal_protocol_id": goal_protocol_id},
        ) if goal_protocol_id is not None else []
    )
    for key, (table, ordering) in direct.items():
        snapshot[key] = rows(
            f"SELECT * FROM {table} WHERE tenant_id = :tenant_id "
            f"AND run_id = :run_id ORDER BY {ordering}",
            identity,
        )

    claim_ids = sorted({
        attempt["claim_revision_id"]
        for attempt in snapshot["research_verification_attempts"]
    })
    snapshot["invalidation_decisions"] = rows(
        "SELECT d.* FROM invalidation_decisions d "
        "WHERE d.tenant_id = :tenant_id AND EXISTS ("
        "SELECT 1 FROM research_verification_attempts a "
        "WHERE a.tenant_id = d.tenant_id AND a.run_id = :run_id "
        "AND a.claim_revision_id = d.target_claim_revision_id) "
        "ORDER BY d.decided_at, d.id",
        identity,
    )
    snapshot["current_use_bindings"] = rows(
        "SELECT b.* FROM current_use_bindings b "
        "WHERE b.tenant_id = :tenant_id AND EXISTS ("
        "SELECT 1 FROM research_verification_attempts a "
        "WHERE a.tenant_id = b.tenant_id AND a.run_id = :run_id "
        "AND a.claim_revision_id = b.claim_revision_id) "
        "ORDER BY b.claim_revision_id, b.profile_id, b.use_scope, b.id",
        identity,
    )
    snapshot["authorization_grants"] = rows(
        "SELECT g.* FROM authorization_grants g "
        "WHERE g.tenant_id = :tenant_id AND EXISTS ("
        "SELECT 1 FROM authorization_invocations i "
        "WHERE i.tenant_id = g.tenant_id AND i.run_id = :run_id "
        "AND i.grant_id = g.id) ORDER BY g.created_at, g.id",
        identity,
    )
    snapshot["knowledge_admission_receipts"] = rows(
        "SELECT k.* FROM knowledge_admission_receipts k "
        "WHERE k.tenant_id = :tenant_id AND EXISTS ("
        "SELECT 1 FROM current_use_bindings b "
        "JOIN research_verification_attempts a "
        "ON a.tenant_id = b.tenant_id "
        "AND a.claim_revision_id = b.claim_revision_id "
        "WHERE b.tenant_id = k.tenant_id "
        "AND b.knowledge_admission_receipt_id = k.id "
        "AND a.run_id = :run_id) ORDER BY k.issued_at, k.id",
        identity,
    )

    # Firing rows have task_id, not run_id.  A scheduler-style request_id is
    # caller-visible and cannot serve as a durable foreign key to a Run.
    snapshot["scheduled_task_firings"] = []
    snapshot["association_basis"] = {
        "direct_run_id": list(direct),
        "claim_related_via_recorded_attempt": claim_ids,
        "grant_related_via_consumed_invocation": sorted({
            item["grant_id"] for item in snapshot["authorization_invocations"]
        }),
        "admission_related_via_current_binding": sorted({
            item["knowledge_admission_receipt_id"]
            for item in snapshot["current_use_bindings"]
            if item.get("knowledge_admission_receipt_id") is not None
        }),
    }
    if goal_requested:
        snapshot["association_basis"]["goal_protocol_id"] = goal_protocol_id
    snapshot["omissions"] = [{
        "source": "scheduled_task_firings",
        "reason": "no_durable_run_id_association",
    }]
    if not goal_requested:
        snapshot["omissions"].append({
            "source": "run_goal_binding",
            "reason": "agent_runs_do_not_store_protocol_or_goal_identity",
        })
    elif len(snapshot["goal_protocols"]) != 1:
        snapshot["omissions"].append({
            "source": "run_goal_binding",
            "reason": "referenced_goal_protocol_missing",
        })
    if not claim_ids:
        snapshot["omissions"].append({
            "source": "claim_related_authority",
            "reason": "no_research_verification_attempt_for_run",
        })
    missing_grants = set(snapshot["association_basis"]["grant_related_via_consumed_invocation"]) - {
        item["id"] for item in snapshot["authorization_grants"]
    }
    if missing_grants:
        snapshot["omissions"].append({
            "source": "authorization_grants",
            "reason": "referenced_grant_row_missing",
            "ids": sorted(missing_grants),
        })
    missing_admissions = set(snapshot["association_basis"]["admission_related_via_current_binding"]) - {
        item["id"] for item in snapshot["knowledge_admission_receipts"]
    }
    if missing_admissions:
        snapshot["omissions"].append({
            "source": "knowledge_admission_receipts",
            "reason": "referenced_admission_row_missing",
            "ids": sorted(missing_admissions),
        })
    return snapshot
