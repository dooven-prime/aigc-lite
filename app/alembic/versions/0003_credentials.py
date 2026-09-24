"""Add the workspace encrypted credential store."""

from __future__ import annotations

from alembic import op

revision = "0003_credentials"
down_revision = "0002_mcp_probe_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS credentials (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            secret_value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revoked_at TEXT,
            UNIQUE(tenant_id, name)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS credentials_tenant_idx "
        "ON credentials(tenant_id, name)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS credentials")
