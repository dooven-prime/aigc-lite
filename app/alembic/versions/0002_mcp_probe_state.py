"""Add the latest MCP probe result to each workspace server."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_mcp_probe_state"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("mcp_servers")
    }
    additions = (
        sa.Column("health_status", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("last_tested_at", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("last_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_tool_count", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("mcp_servers") as batch:
        for column in additions:
            if column.name not in existing:
                batch.add_column(column)


def downgrade() -> None:
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("mcp_servers")
    }
    with op.batch_alter_table("mcp_servers") as batch:
        for name in (
            "last_tool_count",
            "last_latency_ms",
            "last_error_code",
            "last_tested_at",
            "health_status",
        ):
            if name in existing:
                batch.drop_column(name)
