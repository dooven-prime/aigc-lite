"""Bind complete native Goal identity to a Run at creation time."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_native_goal_binding"
down_revision = "0027_stream_lifecycle"
branch_labels = None
depends_on = None

_GOAL_FIELDS = (
    "goal_contract",
    "goal_protocol_id",
    "goal_protocol_version",
    "goal_protocol_sha256",
    "goal_sha256",
    "goal_json",
)
_ALL_OR_NONE = (
    "(" + " AND ".join(f"{field} IS NULL" for field in _GOAL_FIELDS) + ") OR ("
    + " AND ".join(f"{field} IS NOT NULL" for field in _GOAL_FIELDS) + ")"
)


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("goal_contract", sa.Text(), nullable=True))
        batch.add_column(sa.Column("goal_protocol_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("goal_protocol_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("goal_protocol_sha256", sa.Text(), nullable=True))
        batch.add_column(sa.Column("goal_sha256", sa.Text(), nullable=True))
        batch.add_column(sa.Column("goal_json", sa.Text(), nullable=True))
        batch.create_check_constraint("agent_runs_goal_all_or_none", _ALL_OR_NONE)

    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        differences = " OR ".join(
            f"NEW.{field} IS NOT OLD.{field}" for field in _GOAL_FIELDS
        )
        op.execute(
            "CREATE TRIGGER agent_runs_goal_immutable_update "
            "BEFORE UPDATE ON agent_runs "
            f"WHEN {differences} "
            "BEGIN SELECT RAISE(ABORT, 'Run Goal binding is immutable'); END"
        )
    elif connection.dialect.name == "postgresql":
        differences = " OR ".join(
            f"NEW.{field} IS DISTINCT FROM OLD.{field}" for field in _GOAL_FIELDS
        )
        connection.exec_driver_sql(
            "CREATE FUNCTION reject_agent_run_goal_mutation() RETURNS trigger AS $function$ "
            f"BEGIN IF {differences} THEN "
            "RAISE EXCEPTION 'Run Goal binding is immutable'; "
            "END IF; RETURN NEW; END; $function$ LANGUAGE plpgsql"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER agent_runs_goal_immutable_update "
            "BEFORE UPDATE ON agent_runs FOR EACH ROW "
            "EXECUTE FUNCTION reject_agent_run_goal_mutation()"
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS agent_runs_goal_immutable_update")
    elif connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS agent_runs_goal_immutable_update ON agent_runs"
        )
        connection.exec_driver_sql(
            "DROP FUNCTION IF EXISTS reject_agent_run_goal_mutation()"
        )
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("agent_runs_goal_all_or_none", type_="check")
        for field in reversed(_GOAL_FIELDS):
            batch.drop_column(field)
