"""Bind each model request to an observable, idempotent usage receipt."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_model_usage_receipts"
down_revision = "0025_invocation_timeout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical zero defaults did not distinguish free pricing from rates
    # omitted by the caller. They remain unconfirmed after this migration.
    op.add_column(
        "model_configs",
        sa.Column("pricing_confirmed", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "model_usage_receipts",
        sa.Column("attempt_id", sa.Text(), primary_key=True),
        sa.Column("model_call_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("principal_id", sa.Text()),
        sa.Column("api_key_id", sa.Text()),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("step_id", sa.Text(), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("provider_config_id", sa.Text()),
        sa.Column("usage_status", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.BigInteger()),
        sa.Column("completion_tokens", sa.BigInteger()),
        sa.Column("pricing_snapshot", sa.Text(), nullable=False),
        sa.Column("estimated_cost", sa.Text()),
        sa.Column("termination", sa.Text()),
        sa.Column("finish_reason", sa.Text()),
        sa.Column("provider_request_id", sa.Text()),
        sa.Column("output_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("finalization_hash", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("finalized_at", sa.Text()),
        sa.CheckConstraint(
            "usage_status IN ('IN_FLIGHT', 'REPORTED', 'UNKNOWN', 'INVALID')",
            name="model_usage_status_valid",
        ),
        sa.CheckConstraint("prompt_tokens IS NULL OR prompt_tokens >= 0", name="model_prompt_tokens_nonnegative"),
        sa.CheckConstraint("completion_tokens IS NULL OR completion_tokens >= 0", name="model_completion_tokens_nonnegative"),
        sa.UniqueConstraint("tenant_id", "run_id", "iteration", "attempt_id", name="model_usage_run_attempt_unique"),
    )
    op.create_index("model_usage_tenant_created_idx", "model_usage_receipts", ["tenant_id", "created_at"])
    op.create_index("model_usage_run_idx", "model_usage_receipts", ["tenant_id", "run_id", "iteration"])
    op.create_index("model_usage_principal_idx", "model_usage_receipts", ["tenant_id", "principal_id", "created_at"])


def downgrade() -> None:
    op.drop_index("model_usage_principal_idx", table_name="model_usage_receipts")
    op.drop_index("model_usage_run_idx", table_name="model_usage_receipts")
    op.drop_index("model_usage_tenant_created_idx", table_name="model_usage_receipts")
    op.drop_table("model_usage_receipts")
    op.drop_column("model_configs", "pricing_confirmed")
