"""Add typed claim relations, verification attempts, and promotion gates."""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, Text

revision = "0009_research_verification"
down_revision = "0008_research_registry"
branch_labels = None
depends_on = None


def _replace_sqlite_search_triggers(*, include_promotion: bool) -> None:
    for name in (
        "search_research_claims_ad",
        "search_research_claims_au",
        "search_research_claims_ai",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    promotion = "NEW.promotion_stage || char(10) ||" if include_promotion else ""
    op.execute(
        f"""
        CREATE TRIGGER search_research_claims_ai
        AFTER INSERT ON research_claim_revisions
        BEGIN
            INSERT INTO search_entries(
                entry_key, tenant_id, source_id, kind, title, content,
                created_at, session_id, run_id, step_id, artifact_id, source_kind
            ) VALUES (
                'research_claim:' || NEW.id, NEW.tenant_id, NEW.id,
                'research_claim', NEW.claim_key,
                NEW.statement || char(10) || NEW.scope || char(10) ||
                NEW.claim_type || char(10) || NEW.method_revision || char(10) ||
                NEW.lifecycle_status || char(10) || {promotion}
                NEW.status_axes || char(10) || NEW.closure_status || char(10) || NEW.blockers,
                NEW.created_at, NULL, NULL, NULL, NULL, NEW.claim_type
            ) ON CONFLICT(entry_key) DO UPDATE SET
                tenant_id = excluded.tenant_id,
                source_id = excluded.source_id,
                kind = excluded.kind,
                title = excluded.title,
                content = excluded.content,
                created_at = excluded.created_at,
                source_kind = excluded.source_kind;
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER search_research_claims_au
        AFTER UPDATE ON research_claim_revisions
        BEGIN
            DELETE FROM search_entries WHERE entry_key = 'research_claim:' || OLD.id;
            INSERT INTO search_entries(
                entry_key, tenant_id, source_id, kind, title, content,
                created_at, session_id, run_id, step_id, artifact_id, source_kind
            ) VALUES (
                'research_claim:' || NEW.id, NEW.tenant_id, NEW.id,
                'research_claim', NEW.claim_key,
                NEW.statement || char(10) || NEW.scope || char(10) ||
                NEW.claim_type || char(10) || NEW.method_revision || char(10) ||
                NEW.lifecycle_status || char(10) || {promotion}
                NEW.status_axes || char(10) || NEW.closure_status || char(10) || NEW.blockers,
                NEW.created_at, NULL, NULL, NULL, NULL, NEW.claim_type
            );
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER search_research_claims_ad
        AFTER DELETE ON research_claim_revisions
        BEGIN
            DELETE FROM search_entries WHERE entry_key = 'research_claim:' || OLD.id;
        END
        """
    )


def upgrade() -> None:
    connection = op.get_bind()
    op.add_column(
        "research_claim_revisions",
        Column("promotion_stage", Text(), nullable=False, server_default="registered"),
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS research_claims_promotion_idx "
        "ON research_claim_revisions(tenant_id, research_case_id, promotion_stage)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS research_claim_relations (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            research_case_id TEXT NOT NULL,
            source_claim_id TEXT NOT NULL,
            target_claim_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            status TEXT NOT NULL,
            rationale TEXT NOT NULL,
            evidence_refs TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_by TEXT,
            withdrawal_reason TEXT,
            withdrawn_by TEXT,
            withdrawn_at TEXT,
            created_at TEXT NOT NULL,
            CHECK(source_claim_id <> target_claim_id),
            CHECK(relation_type IN ('supports', 'refutes', 'depends_on', 'qualifies')),
            CHECK(status IN ('active', 'withdrawn')),
            UNIQUE(tenant_id, research_case_id, source_claim_id, target_claim_id, relation_type)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS research_relations_source_idx "
        "ON research_claim_relations(tenant_id, source_claim_id, status)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS research_relations_target_idx "
        "ON research_claim_relations(tenant_id, target_claim_id, status)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS research_verification_attempts (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            research_case_id TEXT NOT NULL,
            claim_revision_id TEXT NOT NULL,
            receipt_id TEXT NOT NULL,
            run_id TEXT,
            kind TEXT NOT NULL,
            outcome TEXT NOT NULL,
            method TEXT NOT NULL,
            scope TEXT NOT NULL,
            independent INTEGER NOT NULL DEFAULT 0,
            input_digest TEXT NOT NULL,
            output_digest TEXT NOT NULL,
            artifact_ids TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_by TEXT,
            created_at TEXT NOT NULL,
            CHECK(kind IN ('source_audit', 'reproduction', 'calculation', 'experiment', 'review')),
            CHECK(outcome IN ('passed', 'failed', 'inconclusive', 'error')),
            CHECK(independent IN (0, 1))
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS research_attempts_claim_idx "
        "ON research_verification_attempts(tenant_id, claim_revision_id, created_at)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS research_promotion_evaluations (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            research_case_id TEXT NOT NULL,
            claim_revision_id TEXT NOT NULL,
            from_stage TEXT NOT NULL,
            target_stage TEXT NOT NULL,
            decision TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            input_digest TEXT NOT NULL,
            evaluation_digest TEXT NOT NULL,
            criteria TEXT NOT NULL DEFAULT '[]',
            blockers TEXT NOT NULL DEFAULT '[]',
            attempt_ids TEXT NOT NULL DEFAULT '[]',
            relation_ids TEXT NOT NULL DEFAULT '[]',
            created_by TEXT,
            created_at TEXT NOT NULL,
            CHECK(from_stage IN ('registered', 'evidence_ready', 'review_ready', 'release_ready', 'withdrawn')),
            CHECK(target_stage IN ('evidence_ready', 'review_ready', 'release_ready')),
            CHECK(decision IN ('passed', 'blocked'))
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS research_promotions_claim_idx "
        "ON research_promotion_evaluations(tenant_id, claim_revision_id, created_at)"
    )
    if connection.dialect.name == "sqlite":
        _replace_sqlite_search_triggers(include_promotion=True)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        for name in (
            "search_research_claims_ad",
            "search_research_claims_au",
            "search_research_claims_ai",
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.execute("DROP TABLE IF EXISTS research_promotion_evaluations")
    op.execute("DROP TABLE IF EXISTS research_verification_attempts")
    op.execute("DROP TABLE IF EXISTS research_claim_relations")
    op.drop_index("research_claims_promotion_idx", table_name="research_claim_revisions")
    op.drop_column("research_claim_revisions", "promotion_stage")
    if connection.dialect.name == "sqlite":
        _replace_sqlite_search_triggers(include_promotion=False)
