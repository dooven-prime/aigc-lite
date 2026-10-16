"""Persist bounded, workspace-scoped streaming event replay.

Revision ID: 0031_stream_replay
Revises: 0030_auth_lifecycle
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031_stream_replay"
down_revision = "0030_auth_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing Runs cannot acquire a complete event history retroactively.
    # A Run created after this migration opts in at creation time.
    op.add_column(
        "agent_runs",
        sa.Column("stream_replay_available", sa.Integer(), nullable=False, server_default="0"),
    )
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE stream_events (
            tenant_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            event_type TEXT NOT NULL CHECK (event_type IN ('data', 'terminal')),
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, run_id, sequence)
        )
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE stream_events")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("stream_replay_available")
