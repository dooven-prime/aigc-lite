"""Store pinned mathematics catalogues without qualification authority."""

from __future__ import annotations

from alembic import op

revision = "0021_math_release_candidates"
down_revision = "0020_knowledge_admission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS math_release_imports (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            source_repository TEXT NOT NULL,
            source_commit TEXT NOT NULL,
            contents_artifact_id TEXT NOT NULL,
            contents_hash TEXT NOT NULL,
            formalization_artifact_id TEXT NOT NULL,
            formalization_hash TEXT NOT NULL,
            manifest_hash TEXT NOT NULL,
            preview_hash TEXT NOT NULL,
            manifest TEXT NOT NULL,
            family_count INTEGER NOT NULL,
            manuscript_count INTEGER NOT NULL,
            lean_linked_family_count INTEGER NOT NULL,
            admission_state TEXT NOT NULL,
            requested_by TEXT,
            created_at TEXT NOT NULL,
            CHECK(source_repository = 'openai/math'),
            CHECK(admission_state = 'candidate'),
            CHECK(family_count > 0),
            CHECK(manuscript_count > 0),
            UNIQUE(tenant_id, source_repository, source_commit)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS math_release_imports_tenant_idx "
        "ON math_release_imports(tenant_id, created_at)"
    )
    if connection.dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER IF NOT EXISTS immutable_math_release_imports_{action.lower()} "
                f"BEFORE {action} ON math_release_imports BEGIN "
                "SELECT RAISE(ABORT, 'math release import is immutable'); END"
            )
    elif connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            """
            CREATE OR REPLACE FUNCTION reject_math_release_import_mutation()
            RETURNS trigger AS $function$
            BEGIN
                RAISE EXCEPTION 'math release import is immutable';
            END;
            $function$ LANGUAGE plpgsql
            """
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER immutable_math_release_imports "
            "BEFORE UPDATE OR DELETE ON math_release_imports FOR EACH ROW "
            "EXECUTE FUNCTION reject_math_release_import_mutation()"
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        for action in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS immutable_math_release_imports_{action}")
    elif connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS immutable_math_release_imports ON math_release_imports"
        )
        connection.exec_driver_sql(
            "DROP FUNCTION IF EXISTS reject_math_release_import_mutation()"
        )
    op.execute("DROP TABLE IF EXISTS math_release_imports")
