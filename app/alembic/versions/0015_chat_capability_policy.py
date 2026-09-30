"""Bind Agent runs to the server-owned Chat capability decision."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_chat_capability_policy"
down_revision = "0014_review_workbench"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("agent_runs")
    }
    with op.batch_alter_table("agent_runs") as batch:
        if "capability_set_id" not in columns:
            batch.add_column(
                sa.Column(
                    "capability_set_id",
                    sa.Text(),
                    nullable=False,
                    server_default="runtime.none.v1",
                )
            )
        if "capability_policy_hash" not in columns:
            batch.add_column(
                sa.Column("capability_policy_hash", sa.Text(), nullable=True)
            )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("agent_runs")
    }
    with op.batch_alter_table("agent_runs") as batch:
        if "capability_policy_hash" in columns:
            batch.drop_column("capability_policy_hash")
        if "capability_set_id" in columns:
            batch.drop_column("capability_set_id")
