"""Add immutable review runs and advisory finding ledger."""

from __future__ import annotations

from alembic import op

revision = "0014_review_workbench"
down_revision = "0013_model_credential_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS review_runs (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            profile_version INTEGER NOT NULL,
            profile_hash TEXT NOT NULL,
            subject_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            subject_digest TEXT NOT NULL,
            input_snapshot TEXT NOT NULL,
            reviewer_origin TEXT NOT NULL,
            reviewer_lineage TEXT NOT NULL,
            status TEXT NOT NULL,
            finding_count INTEGER NOT NULL,
            severity_counts TEXT NOT NULL,
            requested_by TEXT,
            created_at TEXT NOT NULL,
            CHECK(profile_version > 0),
            CHECK(status IN ('completed')),
            CHECK(reviewer_origin IN ('deterministic_rule', 'agent_suggestion', 'human_review')),
            CHECK(finding_count >= 0)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS review_runs_subject_idx "
        "ON review_runs(tenant_id, subject_type, subject_id, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS review_runs_profile_idx "
        "ON review_runs(tenant_id, profile_id, created_at)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS review_findings (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            review_run_id TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            profile_version INTEGER NOT NULL,
            subject_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            rule_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            origin TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            suggestion TEXT NOT NULL,
            evidence_refs TEXT NOT NULL,
            status TEXT NOT NULL,
            finding_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(profile_version > 0),
            CHECK(ordinal > 0),
            CHECK(severity IN ('P0', 'P1', 'P2', 'P3', 'INFO')),
            CHECK(origin IN ('deterministic_rule', 'agent_suggestion', 'human_review')),
            CHECK(status IN ('open', 'accepted', 'dismissed', 'resolved')),
            UNIQUE(tenant_id, review_run_id, ordinal),
            UNIQUE(tenant_id, review_run_id, finding_hash)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS review_findings_subject_idx "
        "ON review_findings(tenant_id, subject_type, subject_id, status, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS review_findings_run_idx "
        "ON review_findings(tenant_id, review_run_id, severity, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS review_findings")
    op.execute("DROP TABLE IF EXISTS review_runs")
