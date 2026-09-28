"""Add persistent schedule definitions for internal and proactive triggers."""

from __future__ import annotations

from alembic import op

revision = "0004_scheduled_tasks"
down_revision = "0003_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            target TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            trigger_kind TEXT NOT NULL,
            status TEXT NOT NULL,
            next_run_at TEXT,
            interval_seconds REAL,
            last_run_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(trigger_kind IN ('once', 'interval')),
            CHECK(status IN ('scheduled', 'paused', 'completed', 'cancelled')),
            CHECK(interval_seconds IS NULL OR interval_seconds > 0)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS scheduled_tasks_tenant_idx "
        "ON scheduled_tasks(tenant_id, status, next_run_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS scheduled_tasks_due_idx "
        "ON scheduled_tasks(status, next_run_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scheduled_tasks")
