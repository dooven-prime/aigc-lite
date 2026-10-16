"""Bind sessions to credential versions and share pre-auth throttling state.

Revision ID: 0030_auth_lifecycle
Revises: 0029_shared_attempt_invalidation
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_auth_lifecycle"
down_revision = "0029_shared_attempt_invalidation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0")
        )
    with op.batch_alter_table("auth_sessions") as batch:
        batch.add_column(
            sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0")
        )
        batch.create_index("auth_sessions_user_idx", ["user_id"])
        batch.create_index("auth_sessions_expiry_idx", ["expires_at"])
    op.create_table(
        "pre_auth_rate_limits",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("window_start", sa.BigInteger(), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "kind", "key_hash", "window_start", "window_seconds",
            name="pre_auth_rate_limits_pk",
        ),
        sa.CheckConstraint("attempts >= 1", name="pre_auth_rate_limits_attempts"),
    )
    op.create_index(
        "pre_auth_rate_limits_expiry_idx", "pre_auth_rate_limits", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("pre_auth_rate_limits_expiry_idx", table_name="pre_auth_rate_limits")
    op.drop_table("pre_auth_rate_limits")
    with op.batch_alter_table("auth_sessions") as batch:
        batch.drop_index("auth_sessions_expiry_idx")
        batch.drop_index("auth_sessions_user_idx")
        batch.drop_column("auth_version")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("auth_version")
