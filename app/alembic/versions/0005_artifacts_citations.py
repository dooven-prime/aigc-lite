"""Add workspace-scoped Artifact and Citation records."""

from __future__ import annotations

from alembic import op

revision = "0005_artifacts_citations"
down_revision = "0004_scheduled_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            series_id TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            run_id TEXT,
            step_id TEXT,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            media_type TEXT NOT NULL,
            content_text TEXT NOT NULL DEFAULT '',
            uri TEXT,
            content_hash TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            CHECK(kind IN ('text', 'markdown', 'json', 'file', 'link')),
            CHECK(version > 0),
            CHECK(size_bytes >= 0),
            UNIQUE(tenant_id, series_id, version)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS artifacts_tenant_idx "
        "ON artifacts(tenant_id, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS artifacts_run_idx "
        "ON artifacts(tenant_id, run_id, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS artifacts_step_idx "
        "ON artifacts(tenant_id, step_id)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS citations (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            run_id TEXT,
            step_id TEXT,
            artifact_id TEXT,
            source_kind TEXT NOT NULL,
            source_id TEXT,
            source_uri TEXT,
            title TEXT NOT NULL,
            locator TEXT NOT NULL DEFAULT '{}',
            excerpt TEXT NOT NULL DEFAULT '',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            CHECK(source_kind IN ('document', 'url', 'tool', 'artifact'))
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS citations_tenant_idx "
        "ON citations(tenant_id, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS citations_run_idx "
        "ON citations(tenant_id, run_id, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS citations_artifact_idx "
        "ON citations(tenant_id, artifact_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS citations")
    op.execute("DROP TABLE IF EXISTS artifacts")
