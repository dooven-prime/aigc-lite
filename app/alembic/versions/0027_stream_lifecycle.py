"""Persist one-shot streaming Run admission and conservative recovery."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_stream_lifecycle"
down_revision = "0026_model_usage_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("stream_state", sa.Text(), nullable=True))
        batch.add_column(sa.Column("stream_start_deadline_at", sa.Text(), nullable=True))
        batch.add_column(sa.Column("stream_claimed_at", sa.Text(), nullable=True))
        batch.add_column(sa.Column("stream_lease_until", sa.Text(), nullable=True))
        batch.add_column(sa.Column("stream_cancel_requested_at", sa.Text(), nullable=True))
        batch.create_check_constraint(
            "agent_runs_stream_state_valid",
            "stream_state IS NULL OR stream_state IN ('pending', 'claimed', 'closed')",
        )
    op.create_index(
        "agent_runs_stream_pending_idx",
        "agent_runs",
        ["stream_state", "stream_start_deadline_at"],
    )
    op.create_index(
        "agent_runs_stream_claimed_idx",
        "agent_runs",
        ["stream_state", "stream_lease_until"],
    )


def downgrade() -> None:
    op.drop_index("agent_runs_stream_claimed_idx", table_name="agent_runs")
    op.drop_index("agent_runs_stream_pending_idx", table_name="agent_runs")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("agent_runs_stream_state_valid", type_="check")
        batch.drop_column("stream_cancel_requested_at")
        batch.drop_column("stream_lease_until")
        batch.drop_column("stream_claimed_at")
        batch.drop_column("stream_start_deadline_at")
        batch.drop_column("stream_state")
