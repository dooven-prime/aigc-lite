"""Index frozen proof selections and reject cross-receipt invalidated bindings.

Revision ID: 0029_shared_attempt_invalidation
Revises: 0028_native_goal_binding
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from alembic import op
from sqlalchemy import text

revision = "0029_shared_attempt_invalidation"
down_revision = "0028_native_goal_binding"
branch_labels = None
depends_on = None


def _selected_kernel_attempt_ids_v1(
    criteria: list[dict], evidence_closure: dict, profile_id: str
) -> frozenset[str]:
    """Frozen migration copy of the conservative v1 proof projection.

    Alembic backfills must not change when application selection code evolves.
    Historical theorem verifier versions selected opposite closure ends
    without a profile-version change, so ambiguous certificate reusers are
    all indexed as possible selected Attempts.
    """

    selected_artifacts = {
        artifact_id
        for criterion in criteria
        if isinstance(criterion, dict) and criterion.get("code") == "statement_identity"
        for artifact_id in criterion.get("evidence_node_ids", [])
        if isinstance(artifact_id, str)
    }
    if not selected_artifacts or profile_id not in {
        "math.formal.v1", "math.formal.project.v2"
    }:
        return frozenset()
    principal_prefix = (
        "system:verifier:math-project:"
        if profile_id == "math.formal.project.v2"
        else "system:verifier:"
    )
    matches = [
        node["node_id"]
        for node in evidence_closure.get("nodes", [])
        if isinstance(node, dict)
        and node.get("node_type") == "verification_attempt"
        and isinstance(node.get("node_id"), str)
        and isinstance(node.get("payload"), dict)
        and not node["payload"].get("invalidated_by")
        and node["payload"].get("validation_modality") == "kernel_check"
        and node["payload"].get("outcome") == "passed"
        and isinstance(node["payload"].get("verifier_lineage"), dict)
        and node["payload"]["verifier_lineage"].get("runtime_derived") is True
        and str(
            node["payload"]["verifier_lineage"].get("principal_id") or ""
        ).startswith(principal_prefix)
        and not node["payload"]["verifier_lineage"].get("model_route")
        and (profile_id != "math.formal.project.v2" or node["payload"].get("run_id"))
        and selected_artifacts.intersection(node["payload"].get("artifact_ids") or [])
    ]
    if not matches:
        return frozenset()
    frozen_kernel_ids = {
        node_id
        for criterion in criteria
        if isinstance(criterion, dict) and criterion.get("code") == "kernel_check"
        for node_id in criterion.get("evidence_node_ids", [])
        if isinstance(node_id, str)
    }
    if len(frozen_kernel_ids) == 1 and next(iter(frozen_kernel_ids)) in matches:
        return frozenset(frozen_kernel_ids)
    return frozenset(matches)


def _dependent_binding_ids(binding_rows: list[dict], direct_ids: set[str]) -> set[str]:
    affected = set(direct_ids)
    affected_keys = {
        (
            row["tenant_id"], row["claim_revision_id"], row["profile_id"],
            row["use_scope"], row["qualification_receipt_id"],
        )
        for row in binding_rows if row["id"] in affected
    }
    while True:
        added = False
        for row in binding_rows:
            if row["id"] in affected:
                continue
            closure = json.loads(row["evidence_closure"])
            for node in closure.get("nodes", []):
                if node.get("node_type") != "dependency_binding":
                    continue
                frozen = node.get("payload") or {}
                if (
                    row["tenant_id"], node.get("node_id"), frozen.get("profile_id"),
                    frozen.get("use_scope"), frozen.get("qualification_receipt_id"),
                ) in affected_keys:
                    affected.add(row["id"])
                    affected_keys.add(
                        (
                            row["tenant_id"], row["claim_revision_id"],
                            row["profile_id"], row["use_scope"],
                            row["qualification_receipt_id"],
                        )
                    )
                    added = True
                    break
        if not added:
            break
    return affected - direct_ids


def _sqlite_binding_trigger(action: str, *, shared_attempts: bool) -> str:
    attempt_clause = (
        "OR (d.target_scope = 'proof_attempt' AND EXISTS ("
        "SELECT 1 FROM qualification_receipt_attempt_selections s "
        "WHERE s.tenant_id = NEW.tenant_id "
        "AND s.qualification_receipt_id = NEW.qualification_receipt_id "
        "AND s.attempt_id = d.target_attempt_id))"
        if shared_attempts else ""
    )
    return f"""
        CREATE TRIGGER reject_invalidated_binding_{action.lower()}
        BEFORE {action} ON current_use_bindings
        WHEN NEW.state = 'current' AND EXISTS (
            SELECT 1 FROM invalidation_decisions d
            WHERE d.tenant_id = NEW.tenant_id
              AND d.target_claim_revision_id = NEW.claim_revision_id
              AND (
                d.target_scope = 'claim_revision'
                OR d.target_receipt_id = NEW.qualification_receipt_id
                {attempt_clause}
              )
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalidated qualification cannot be current');
        END
    """


def _postgres_binding_function(*, shared_attempts: bool) -> str:
    attempt_clause = (
        "OR (d.target_scope = 'proof_attempt' AND EXISTS ("
        "SELECT 1 FROM qualification_receipt_attempt_selections s "
        "WHERE s.tenant_id = NEW.tenant_id "
        "AND s.qualification_receipt_id = NEW.qualification_receipt_id "
        "AND s.attempt_id = d.target_attempt_id))"
        if shared_attempts else ""
    )
    return f"""
        CREATE OR REPLACE FUNCTION reject_invalidated_binding() RETURNS trigger AS $function$
        BEGIN
            IF NEW.state = 'current' AND EXISTS (
                SELECT 1 FROM invalidation_decisions d
                WHERE d.tenant_id = NEW.tenant_id
                  AND d.target_claim_revision_id = NEW.claim_revision_id
                  AND (
                    d.target_scope = 'claim_revision'
                    OR d.target_receipt_id = NEW.qualification_receipt_id
                    {attempt_clause}
                  )
            ) THEN
                RAISE EXCEPTION 'invalidated qualification cannot be current';
            END IF;
            RETURN NEW;
        END;
        $function$ LANGUAGE plpgsql
    """


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE qualification_receipt_attempt_selections (
            tenant_id TEXT NOT NULL,
            qualification_receipt_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            selection_version TEXT NOT NULL,
            PRIMARY KEY (tenant_id, qualification_receipt_id, attempt_id),
            CHECK(selection_version IN (
                'statement-identity-kernel-attempt.v1',
                'historical-possible-kernel-attempts.v1'
            ))
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX qualification_attempt_selection_lookup_idx "
        "ON qualification_receipt_attempt_selections(tenant_id, attempt_id, qualification_receipt_id)"
    )
    # Historical receipts remain unchanged. Their immutable criteria and
    # evaluation closures are projected into a separate, versioned index.
    rows = connection.exec_driver_sql(
        "SELECT r.tenant_id, r.id, r.profile_id, r.criteria, e.evidence_closure "
        "FROM qualification_receipts r JOIN qualification_evaluations e "
        "ON e.tenant_id = r.tenant_id AND e.id = r.evaluation_id"
    ).all()
    for tenant_id, receipt_id, profile_id, criteria_json, closure_json in rows:
        try:
            criteria = json.loads(criteria_json)
            closure = json.loads(closure_json)
            if not isinstance(criteria, list) or not isinstance(closure, dict):
                raise ValueError("invalid frozen selection shape")
            attempts = _selected_kernel_attempt_ids_v1(criteria, closure, profile_id)
        except (TypeError, ValueError, KeyError) as exc:
            raise RuntimeError(
                f"Cannot backfill proof selection for receipt {receipt_id}"
            ) from exc
        for attempt_id in sorted(attempts):
            connection.execute(
                text(
                    "INSERT INTO qualification_receipt_attempt_selections "
                    "(tenant_id, qualification_receipt_id, attempt_id, selection_version) "
                    "VALUES (:tenant_id, :receipt_id, :attempt_id, :selection_version)"
                ),
                {
                    "tenant_id": tenant_id,
                    "receipt_id": receipt_id,
                    "attempt_id": attempt_id,
                    "selection_version": (
                        "historical-possible-kernel-attempts.v1"
                        if len(attempts) > 1
                        else "statement-identity-kernel-attempt.v1"
                    ),
                },
            )
    # Earlier releases only transitioned the receipt explicitly named by a
    # proof-attempt decision. Reconcile a second receipt that selected the same
    # attempt before installing the stronger write guard. Decisions and issued
    # receipts remain immutable; only their current-use projection changes.
    affected = connection.execute(
        text(
            "SELECT b.tenant_id, b.id AS binding_id, d.id AS decision_id, "
            "d.effect_state FROM current_use_bindings b "
            "JOIN qualification_receipt_attempt_selections s "
            "ON s.tenant_id = b.tenant_id "
            "AND s.qualification_receipt_id = b.qualification_receipt_id "
            "JOIN invalidation_decisions d ON d.tenant_id = b.tenant_id "
            "AND d.target_claim_revision_id = b.claim_revision_id "
            "AND d.target_scope = 'proof_attempt' AND d.target_attempt_id = s.attempt_id "
            "WHERE b.state = 'current' ORDER BY b.tenant_id, b.id, d.decided_at, d.id"
        )
    ).mappings().all()
    current_rows = [
        dict(item) for item in connection.execute(
            text(
                "SELECT b.tenant_id, b.id, b.claim_revision_id, b.profile_id, b.use_scope, "
                "b.qualification_receipt_id, "
                "e.evidence_closure FROM current_use_bindings b "
                "JOIN qualification_receipts r ON r.tenant_id = b.tenant_id "
                "AND r.id = b.qualification_receipt_id "
                "JOIN qualification_evaluations e ON e.tenant_id = r.tenant_id "
                "AND e.id = r.evaluation_id WHERE b.state = 'current'"
            )
        ).mappings().all()
    ]
    direct_ids = {item["binding_id"] for item in affected}
    reconciled = set()
    for item in affected:
        if item["binding_id"] in reconciled:
            continue
        downstream = _dependent_binding_ids(current_rows, {item["binding_id"]})
        for binding_id in (item["binding_id"], *sorted(downstream - direct_ids)):
            if binding_id in reconciled:
                continue
            connection.execute(
                text(
                    "UPDATE current_use_bindings SET state = :state, stale_reason = :reason, "
                    "bound_by = 'system:migration-0029-shared-attempt', "
                    "updated_at = :updated_at WHERE tenant_id = :tenant_id "
                    "AND id = :binding_id AND state = 'current'"
                ),
                {
                    "state": item["effect_state"] if binding_id == item["binding_id"] else "stale",
                    "reason": f"invalidation_decision:{item['decision_id']}",
                    "updated_at": datetime.now(UTC).isoformat(),
                    "tenant_id": item["tenant_id"],
                    "binding_id": binding_id,
                },
            )
            reconciled.add(binding_id)
    if connection.dialect.name == "sqlite":
        for action in ("INSERT", "UPDATE"):
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS reject_invalidated_binding_{action.lower()}"
            )
            connection.exec_driver_sql(
                _sqlite_binding_trigger(action, shared_attempts=True)
            )
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER immutable_proof_selection_{action.lower()} "
                f"BEFORE {action} ON qualification_receipt_attempt_selections BEGIN "
                "SELECT RAISE(ABORT, 'proof selection is immutable'); END"
            )
    elif connection.dialect.name == "postgresql":
        connection.exec_driver_sql(_postgres_binding_function(shared_attempts=True))
        connection.exec_driver_sql(
            "CREATE OR REPLACE FUNCTION reject_proof_selection_mutation() RETURNS trigger "
            "AS $function$ BEGIN RAISE EXCEPTION 'proof selection is immutable'; "
            "END; $function$ LANGUAGE plpgsql"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER immutable_proof_selection BEFORE UPDATE OR DELETE "
            "ON qualification_receipt_attempt_selections FOR EACH ROW "
            "EXECUTE FUNCTION reject_proof_selection_mutation()"
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        for action in ("INSERT", "UPDATE"):
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS reject_invalidated_binding_{action.lower()}"
            )
            connection.exec_driver_sql(
                _sqlite_binding_trigger(action, shared_attempts=False)
            )
    elif connection.dialect.name == "postgresql":
        connection.exec_driver_sql(_postgres_binding_function(shared_attempts=False))
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS immutable_proof_selection "
            "ON qualification_receipt_attempt_selections"
        )
        connection.exec_driver_sql("DROP FUNCTION IF EXISTS reject_proof_selection_mutation()")
    connection.exec_driver_sql("DROP TABLE qualification_receipt_attempt_selections")
