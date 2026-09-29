"""Bind workspace model endpoints to credential-store references."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_model_credential_binding"
down_revision = "0012_qualification_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("model_configs")
    }
    if "credential_reference" not in columns:
        with op.batch_alter_table("model_configs") as batch:
            batch.add_column(
                sa.Column(
                    "credential_reference",
                    sa.Text(),
                    nullable=False,
                    server_default="",
                )
            )
    # Legacy per-model ciphertext is deliberately not reused implicitly. An
    # administrator must bind an explicit workspace Credential Store record.
    op.execute("UPDATE model_configs SET api_key = ''")


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("model_configs")
    }
    if "credential_reference" in columns:
        with op.batch_alter_table("model_configs") as batch:
            batch.drop_column("credential_reference")
