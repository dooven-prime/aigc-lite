"""Bind each consumed grant call to one exact external dispatch."""

from __future__ import annotations

from alembic import op

revision = "0024_authorization_invocations"
down_revision = "0023_scheduled_firings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS authorization_invocations (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            grant_id TEXT NOT NULL,
            grant_call_number INTEGER NOT NULL,
            actor_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            native_name TEXT NOT NULL,
            public_name TEXT NOT NULL,
            source TEXT NOT NULL,
            binding_id TEXT NOT NULL,
            binding_hash TEXT NOT NULL,
            adapter_id TEXT NOT NULL,
            proposal_id TEXT NOT NULL,
            policy_hash TEXT NOT NULL,
            adapter_target_type TEXT NOT NULL,
            adapter_target_id TEXT NOT NULL,
            tool_spec_hash TEXT NOT NULL,
            arguments_digest TEXT NOT NULL,
            invocation_digest TEXT NOT NULL,
            consumed_at TEXT NOT NULL,
            claim_expires_at TEXT NOT NULL,
            dispatch_id TEXT,
            claimed_at TEXT,
            UNIQUE(tenant_id, grant_id, grant_call_number),
            UNIQUE(tenant_id, dispatch_id),
            CHECK(grant_call_number > 0),
            CHECK((dispatch_id IS NULL AND claimed_at IS NULL)
               OR (dispatch_id IS NOT NULL AND claimed_at IS NOT NULL))
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS authorization_invocations_grant_idx "
        "ON authorization_invocations(tenant_id, grant_id, consumed_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS authorization_invocations")
