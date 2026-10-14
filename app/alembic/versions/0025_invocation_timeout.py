"""Freeze the maximum adapter request window with each consumed invocation."""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0025_invocation_timeout"
down_revision = "0024_authorization_invocations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing, unclaimed 0024 handoffs have no frozen timeout. A zero maximum
    # makes them unclaimable rather than retroactively granting a new window.
    if "tool_timeout_seconds" not in {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("authorization_invocations")
    }:
        op.execute(
            "ALTER TABLE authorization_invocations "
            "ADD COLUMN tool_timeout_seconds DOUBLE PRECISION NOT NULL DEFAULT 0"
        )


def downgrade() -> None:
    op.drop_column("authorization_invocations", "tool_timeout_seconds")
