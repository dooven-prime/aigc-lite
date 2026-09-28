"""Add evidence-control records and the first decision profile projection."""

from __future__ import annotations

from alembic import op

revision = "0007_evidence_decisions"
down_revision = "0006_sqlite_fts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS evidence_protocols (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            profile TEXT NOT NULL,
            name TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL,
            purpose TEXT NOT NULL,
            scope TEXT NOT NULL,
            completion_predicate TEXT NOT NULL,
            stop_conditions TEXT NOT NULL DEFAULT '[]',
            budget TEXT NOT NULL DEFAULT '{}',
            source_uri TEXT,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            frozen_at TEXT,
            CHECK(version > 0),
            CHECK(status IN ('draft', 'registered', 'active', 'completed',
                             'failed', 'unresolved', 'frozen')),
            UNIQUE(tenant_id, profile, content_hash)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS evidence_protocols_tenant_idx "
        "ON evidence_protocols(tenant_id, created_at)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS evidence_claims (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            protocol_id TEXT NOT NULL,
            run_id TEXT,
            statement TEXT NOT NULL,
            resolution TEXT NOT NULL,
            scope TEXT NOT NULL,
            evidence_refs TEXT NOT NULL DEFAULT '[]',
            prohibited_upgrades TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            CHECK(resolution IN ('supported', 'insufficient', 'undetermined', 'rejected'))
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS evidence_claims_protocol_idx "
        "ON evidence_claims(tenant_id, protocol_id, created_at)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS execution_receipts (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            protocol_id TEXT NOT NULL,
            run_id TEXT,
            status TEXT NOT NULL,
            input_digest TEXT NOT NULL,
            output_digest TEXT NOT NULL,
            runtime TEXT NOT NULL DEFAULT '{}',
            budget TEXT NOT NULL DEFAULT '{}',
            artifact_ids TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS execution_receipts_protocol_idx "
        "ON execution_receipts(tenant_id, protocol_id, created_at)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS evidence_reviews (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            receipt_id TEXT NOT NULL,
            reviewer_kind TEXT NOT NULL,
            reviewer_id TEXT,
            independent INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            finding TEXT NOT NULL,
            evidence_refs TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            CHECK(reviewer_kind IN ('human', 'agent', 'automated')),
            CHECK(status IN ('pending', 'accepted', 'changes_requested', 'rejected'))
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS evidence_reviews_receipt_idx "
        "ON evidence_reviews(tenant_id, receipt_id, created_at)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS freeze_manifests (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            protocol_id TEXT NOT NULL,
            name TEXT NOT NULL,
            version INTEGER NOT NULL,
            members TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(version > 0),
            UNIQUE(tenant_id, protocol_id, version)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS decision_cases (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            protocol_id TEXT NOT NULL,
            receipt_id TEXT NOT NULL,
            source_case_id TEXT NOT NULL,
            family TEXT NOT NULL,
            state_text TEXT NOT NULL,
            questions TEXT NOT NULL,
            answers TEXT NOT NULL,
            gold_answers TEXT NOT NULL DEFAULT '{}',
            has_gold INTEGER NOT NULL DEFAULT 0,
            resolution TEXT NOT NULL,
            confidence REAL NOT NULL,
            entropy REAL NOT NULL,
            threshold REAL NOT NULL,
            run_id TEXT,
            step_id TEXT,
            primary_artifact_id TEXT,
            created_at TEXT NOT NULL,
            CHECK(resolution IN ('decided', 'manual_review', 'undetermined', 'rejected')),
            CHECK(confidence >= 0 AND confidence <= 1),
            CHECK(entropy >= 0 AND entropy <= 1),
            CHECK(threshold >= 0 AND threshold <= 1),
            UNIQUE(tenant_id, protocol_id, source_case_id)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS decision_cases_tenant_idx "
        "ON decision_cases(tenant_id, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS decision_cases_protocol_idx "
        "ON decision_cases(tenant_id, protocol_id, family, resolution)"
    )

    if connection.dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS search_decision_cases_ai
            AFTER INSERT ON decision_cases
            BEGIN
                INSERT INTO search_entries(
                    entry_key, tenant_id, source_id, kind, title, content,
                    created_at, session_id, run_id, step_id, artifact_id, source_kind
                ) VALUES (
                    'decision_case:' || NEW.id, NEW.tenant_id, NEW.id,
                    'decision_case', NEW.source_case_id,
                    NEW.state_text || char(10) || NEW.questions || char(10) || NEW.answers,
                    NEW.created_at, NULL, NEW.run_id, NEW.step_id,
                    NEW.primary_artifact_id, NEW.family
                ) ON CONFLICT(entry_key) DO UPDATE SET
                    tenant_id = excluded.tenant_id,
                    source_id = excluded.source_id,
                    kind = excluded.kind,
                    title = excluded.title,
                    content = excluded.content,
                    created_at = excluded.created_at,
                    run_id = excluded.run_id,
                    step_id = excluded.step_id,
                    artifact_id = excluded.artifact_id,
                    source_kind = excluded.source_kind;
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS search_decision_cases_au
            AFTER UPDATE ON decision_cases
            BEGIN
                DELETE FROM search_entries WHERE entry_key = 'decision_case:' || OLD.id;
                INSERT INTO search_entries(
                    entry_key, tenant_id, source_id, kind, title, content,
                    created_at, session_id, run_id, step_id, artifact_id, source_kind
                ) VALUES (
                    'decision_case:' || NEW.id, NEW.tenant_id, NEW.id,
                    'decision_case', NEW.source_case_id,
                    NEW.state_text || char(10) || NEW.questions || char(10) || NEW.answers,
                    NEW.created_at, NULL, NEW.run_id, NEW.step_id,
                    NEW.primary_artifact_id, NEW.family
                );
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS search_decision_cases_ad
            AFTER DELETE ON decision_cases
            BEGIN
                DELETE FROM search_entries WHERE entry_key = 'decision_case:' || OLD.id;
            END
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS search_decision_cases_ad")
        op.execute("DROP TRIGGER IF EXISTS search_decision_cases_au")
        op.execute("DROP TRIGGER IF EXISTS search_decision_cases_ai")
    op.execute("DROP TABLE IF EXISTS decision_cases")
    op.execute("DROP TABLE IF EXISTS freeze_manifests")
    op.execute("DROP TABLE IF EXISTS evidence_reviews")
    op.execute("DROP TABLE IF EXISTS execution_receipts")
    op.execute("DROP TABLE IF EXISTS evidence_claims")
    op.execute("DROP TABLE IF EXISTS evidence_protocols")
