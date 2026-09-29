"""Add structured independence and replayable promotion snapshots."""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import Column, Text, text

revision = "0011_assurance_bundle"
down_revision = "0010_verification_runner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_verification_attempts",
        Column("independence", Text(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "research_promotion_evaluations",
        Column("input_snapshot", Text(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "research_verification_executions",
        Column("input_snapshot", Text(), nullable=False, server_default="{}"),
    )
    # Historical booleans are retained as declarations, not silently upgraded
    # into structured proof of independence.
    op.get_bind().execute(
        text(
            "UPDATE research_verification_attempts SET independence = :assessment "
            "WHERE independent = 1"
        ),
        {
            "assessment": json.dumps(
                {
                    "contract_version": "verification.independence.v1",
                    "legacy_declaration": True,
                    "basis": [],
                    "limitations": [
                        "legacy boolean declaration; independence relationships were not captured"
                    ],
                },
                separators=(",", ":"),
            )
        },
    )


def downgrade() -> None:
    op.drop_column("research_verification_executions", "input_snapshot")
    op.drop_column("research_promotion_evaluations", "input_snapshot")
    op.drop_column("research_verification_attempts", "independence")
