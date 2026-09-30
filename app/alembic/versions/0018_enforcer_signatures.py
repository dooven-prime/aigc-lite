"""Persist server-derived verification of external enforcement signatures."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_enforcer_signatures"
down_revision = "0017_execution_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("enforcement_receipts")
    }
    additions = (
        sa.Column(
            "signature_verified",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("enforcement_request_id", sa.Text(), nullable=True),
        sa.Column("enforcement_request_hash", sa.Text(), nullable=True),
        sa.Column("signature_algorithm", sa.Text(), nullable=True),
        sa.Column("signing_key_id", sa.Text(), nullable=True),
        sa.Column("signed_payload_hash", sa.Text(), nullable=True),
        sa.Column("signature_verified_at", sa.Text(), nullable=True),
        sa.Column("signature_verifier_id", sa.Text(), nullable=True),
        sa.Column(
            "signed_payload",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "signature_verification",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column("enforcement_receipts", column)
    op.create_index(
        "enforcement_receipts_signature_idx",
        "enforcement_receipts",
        ["tenant_id", "signature_verified", "signing_key_id", "issued_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "enforcement_receipts_signature_idx",
        table_name="enforcement_receipts",
        if_exists=True,
    )
    for name in (
        "signature_verification",
        "signed_payload",
        "signature_verifier_id",
        "signature_verified_at",
        "signed_payload_hash",
        "signing_key_id",
        "signature_algorithm",
        "enforcement_request_hash",
        "enforcement_request_id",
        "signature_verified",
    ):
        op.drop_column("enforcement_receipts", name)
