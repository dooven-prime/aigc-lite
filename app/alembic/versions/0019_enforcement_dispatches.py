"""Add persistent external enforcement dispatch and reconciliation state."""

from __future__ import annotations

from alembic import op

revision = "0019_enforcement_dispatches"
down_revision = "0018_enforcer_signatures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS enforcement_dispatches (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            binding_id TEXT,
            adapter_id TEXT NOT NULL,
            issuer_id TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            step_id TEXT,
            proposal_id TEXT NOT NULL,
            original_dispatch_id TEXT,
            request_hash TEXT NOT NULL,
            execution_envelope_hash TEXT NOT NULL,
            tool_spec_hash TEXT NOT NULL,
            arguments_digest TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            state TEXT NOT NULL,
            receipt_id TEXT,
            workload_id TEXT,
            last_error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(target_type IN ('tool_execution_backend', 'tool_provider', 'robot_controller')),
            CHECK(state IN ('dispatching', 'terminal', 'indeterminate', 'reconciled'))
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS enforcement_dispatches_state_idx "
        "ON enforcement_dispatches(tenant_id, adapter_id, proposal_id, state, updated_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS enforcement_dispatches_original_idx "
        "ON enforcement_dispatches(tenant_id, original_dispatch_id, updated_at)"
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS enforcement_dispatches_active_adapter_idx "
        "ON enforcement_dispatches(tenant_id, adapter_id) "
        "WHERE original_dispatch_id IS NULL "
        "AND state IN ('dispatching', 'indeterminate')"
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS enforcement_dispatches_active_reconcile_idx "
        "ON enforcement_dispatches(tenant_id, original_dispatch_id) "
        "WHERE original_dispatch_id IS NOT NULL "
        "AND state IN ('dispatching', 'indeterminate')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS enforcement_dispatches")
