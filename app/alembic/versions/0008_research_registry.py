"""Add versioned research registries and searchable claim revisions."""

from __future__ import annotations

from alembic import op

revision = "0008_research_registry"
down_revision = "0007_evidence_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS research_cases (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            protocol_id TEXT NOT NULL,
            receipt_id TEXT NOT NULL,
            profile TEXT NOT NULL,
            name TEXT NOT NULL,
            registry_id TEXT NOT NULL,
            registry_version TEXT NOT NULL,
            authority TEXT NOT NULL,
            as_of_date TEXT,
            status TEXT NOT NULL,
            source_artifact_id TEXT NOT NULL,
            source_ledger_artifact_id TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            CHECK(status IN ('draft', 'active', 'frozen', 'withdrawn')),
            UNIQUE(tenant_id, protocol_id)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS research_cases_tenant_idx "
        "ON research_cases(tenant_id, created_at)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS research_sources (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            research_case_id TEXT NOT NULL,
            source_key TEXT NOT NULL,
            locator TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(tenant_id, research_case_id, source_key, locator, content_hash)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS research_sources_case_idx "
        "ON research_sources(tenant_id, research_case_id, source_key)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS research_claim_revisions (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            research_case_id TEXT NOT NULL,
            claim_key TEXT NOT NULL,
            revision_number INTEGER NOT NULL DEFAULT 1,
            statement TEXT NOT NULL,
            claim_type TEXT NOT NULL,
            scope TEXT NOT NULL,
            method_revision TEXT NOT NULL,
            lifecycle_status TEXT NOT NULL,
            status_axes TEXT NOT NULL DEFAULT '{}',
            closure_status TEXT NOT NULL,
            blockers TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            CHECK(revision_number > 0),
            CHECK(closure_status IN ('closed', 'blocked')),
            UNIQUE(tenant_id, research_case_id, claim_key, revision_number)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS research_claims_case_idx "
        "ON research_claim_revisions(tenant_id, research_case_id, claim_type, closure_status)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS research_claim_sources (
            tenant_id TEXT NOT NULL,
            claim_revision_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(tenant_id, claim_revision_id, source_id)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS research_claim_sources_source_idx "
        "ON research_claim_sources(tenant_id, source_id)"
    )

    if connection.dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS search_research_claims_ai
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
                    NEW.lifecycle_status || char(10) || NEW.status_axes || char(10) ||
                    NEW.closure_status || char(10) || NEW.blockers,
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
            """
            CREATE TRIGGER IF NOT EXISTS search_research_claims_au
            AFTER UPDATE ON research_claim_revisions
            BEGIN
                DELETE FROM search_entries
                WHERE entry_key = 'research_claim:' || OLD.id;
                INSERT INTO search_entries(
                    entry_key, tenant_id, source_id, kind, title, content,
                    created_at, session_id, run_id, step_id, artifact_id, source_kind
                ) VALUES (
                    'research_claim:' || NEW.id, NEW.tenant_id, NEW.id,
                    'research_claim', NEW.claim_key,
                    NEW.statement || char(10) || NEW.scope || char(10) ||
                    NEW.claim_type || char(10) || NEW.method_revision || char(10) ||
                    NEW.lifecycle_status || char(10) || NEW.status_axes || char(10) ||
                    NEW.closure_status || char(10) || NEW.blockers,
                    NEW.created_at, NULL, NULL, NULL, NULL, NEW.claim_type
                );
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS search_research_claims_ad
            AFTER DELETE ON research_claim_revisions
            BEGIN
                DELETE FROM search_entries
                WHERE entry_key = 'research_claim:' || OLD.id;
            END
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS search_research_claims_ad")
        op.execute("DROP TRIGGER IF EXISTS search_research_claims_au")
        op.execute("DROP TRIGGER IF EXISTS search_research_claims_ai")
    op.execute("DROP TABLE IF EXISTS research_claim_sources")
    op.execute("DROP TABLE IF EXISTS research_claim_revisions")
    op.execute("DROP TABLE IF EXISTS research_sources")
    op.execute("DROP TABLE IF EXISTS research_cases")
