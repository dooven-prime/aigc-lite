"""Add versioned verification plans and durable executions."""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, Text

revision = "0010_verification_runner"
down_revision = "0009_research_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS research_verification_plans (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            research_case_id TEXT NOT NULL,
            claim_revision_id TEXT NOT NULL,
            plan_key TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL,
            executor TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            method TEXT NOT NULL,
            scope TEXT NOT NULL,
            prompt TEXT NOT NULL,
            system_prompt TEXT NOT NULL,
            model TEXT,
            result_contract_version TEXT NOT NULL,
            auto_promote INTEGER NOT NULL DEFAULT 1,
            content_digest TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_by TEXT,
            created_at TEXT NOT NULL,
            CHECK(version > 0),
            CHECK(status IN ('active', 'retired')),
            CHECK(executor IN ('agent')),
            CHECK(kind IN ('source_audit', 'reproduction', 'calculation', 'experiment', 'review')),
            CHECK(auto_promote IN (0, 1)),
            UNIQUE(tenant_id, claim_revision_id, plan_key, version)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS research_verification_plans_claim_idx "
        "ON research_verification_plans(tenant_id, claim_revision_id, plan_key, version)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS research_verification_executions (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            research_case_id TEXT NOT NULL,
            claim_revision_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            plan_version INTEGER NOT NULL,
            status TEXT NOT NULL,
            request_id TEXT NOT NULL,
            run_id TEXT,
            scheduled_task_id TEXT,
            attempt_id TEXT,
            artifact_id TEXT,
            promotion_evaluation_id TEXT,
            outcome TEXT,
            input_digest TEXT NOT NULL,
            output_digest TEXT,
            error_code TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_by TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            CHECK(status IN ('running', 'succeeded', 'failed', 'cancelled', 'invalid_output')),
            CHECK(outcome IS NULL OR outcome IN ('passed', 'failed', 'inconclusive', 'error')),
            UNIQUE(tenant_id, request_id)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS research_verification_executions_claim_idx "
        "ON research_verification_executions(tenant_id, claim_revision_id, started_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS research_verification_executions_plan_idx "
        "ON research_verification_executions(tenant_id, plan_id, started_at)"
    )
    op.add_column(
        "research_verification_attempts",
        Column("plan_id", Text(), nullable=True),
    )
    op.add_column(
        "research_verification_attempts",
        Column("verification_execution_id", Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_verification_attempts", "verification_execution_id")
    op.drop_column("research_verification_attempts", "plan_id")
    op.execute("DROP TABLE IF EXISTS research_verification_executions")
    op.execute("DROP TABLE IF EXISTS research_verification_plans")
