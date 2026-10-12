"""Fence each scheduled occurrence with a durable, single-owner firing record."""

from __future__ import annotations

from alembic import op

revision = "0023_scheduled_firings"
down_revision = "0022_verified_invalidations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS scheduled_task_firings (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            due_at TEXT NOT NULL,
            lease_owner TEXT NOT NULL,
            lease_expires_at TEXT NOT NULL,
            state TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            finished_at TEXT,
            error_kind TEXT,
            UNIQUE(tenant_id, task_id, due_at),
            CHECK(state IN ('running', 'succeeded', 'failed', 'indeterminate'))
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS scheduled_task_firings_recovery_idx "
        "ON scheduled_task_firings(state, lease_expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scheduled_task_firings")
