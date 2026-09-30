"""Add immutable conversation import batches and candidate search records."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_conversation_imports"
down_revision = "0015_chat_capability_policy"
branch_labels = None
depends_on = None


_IMMUTABLE_TABLES = (
    "conversation_import_batches",
    "conversation_import_conversations",
    "conversation_import_messages",
)


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS conversation_import_batches (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            importer_id TEXT NOT NULL,
            importer_version INTEGER NOT NULL,
            source_name TEXT NOT NULL,
            source_artifact_id TEXT NOT NULL,
            source_content_hash TEXT NOT NULL,
            preview_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            admission_state TEXT NOT NULL,
            conversation_count INTEGER NOT NULL,
            message_count INTEGER NOT NULL,
            warning_count INTEGER NOT NULL,
            warnings TEXT NOT NULL,
            requested_by TEXT,
            created_at TEXT NOT NULL,
            CHECK(importer_version > 0),
            CHECK(status IN ('committed')),
            CHECK(admission_state IN ('candidate')),
            CHECK(conversation_count >= 0),
            CHECK(message_count >= 0),
            CHECK(warning_count >= 0),
            UNIQUE(tenant_id, importer_id, source_content_hash),
            UNIQUE(tenant_id, source_artifact_id)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS conversation_import_batches_tenant_idx "
        "ON conversation_import_batches(tenant_id, created_at)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS conversation_import_conversations (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            importer_id TEXT NOT NULL,
            external_id TEXT NOT NULL,
            title TEXT NOT NULL,
            original_created_at TEXT,
            normalized_created_at TEXT,
            original_updated_at TEXT,
            normalized_updated_at TEXT,
            current_message_external_id TEXT,
            metadata TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(ordinal >= 0),
            UNIQUE(tenant_id, batch_id, external_id)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS conversation_import_conversations_batch_idx "
        "ON conversation_import_conversations(tenant_id, batch_id, ordinal)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS conversation_import_messages (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            importer_id TEXT NOT NULL,
            external_id TEXT NOT NULL,
            parent_external_id TEXT,
            role TEXT NOT NULL,
            content_type TEXT NOT NULL,
            content TEXT NOT NULL,
            original_created_at TEXT,
            normalized_created_at TEXT,
            model TEXT,
            citations TEXT NOT NULL,
            attachments TEXT NOT NULL,
            metadata TEXT NOT NULL,
            record_hash TEXT NOT NULL,
            is_canonical INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(is_canonical IN (0, 1)),
            CHECK(ordinal >= 0),
            UNIQUE(tenant_id, batch_id, conversation_id, external_id)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS conversation_import_messages_conversation_idx "
        "ON conversation_import_messages(tenant_id, conversation_id, ordinal)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS conversation_import_messages_external_idx "
        "ON conversation_import_messages(tenant_id, importer_id, external_id)"
    )

    if connection.dialect.name == "sqlite":
        _upgrade_sqlite_search(connection)
        for table in _IMMUTABLE_TABLES:
            op.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS immutable_{table}_update
                BEFORE UPDATE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'conversation import records are immutable');
                END
                """
            )
            op.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS immutable_{table}_delete
                BEFORE DELETE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'conversation import records are immutable');
                END
                """
            )
    elif connection.dialect.name == "postgresql":
        _upgrade_postgres_immutability(connection)


def _upgrade_sqlite_search(connection) -> None:
    columns = {
        column["name"] for column in sa.inspect(connection).get_columns("search_entries")
    }
    if "import_batch_id" not in columns:
        op.add_column("search_entries", sa.Column("import_batch_id", sa.Text()))
    if "conversation_id" not in columns:
        op.add_column("search_entries", sa.Column("conversation_id", sa.Text()))
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS search_conversation_import_messages_ai
        AFTER INSERT ON conversation_import_messages
        BEGIN
            INSERT INTO search_entries(
                entry_key, tenant_id, source_id, kind, title, content, created_at,
                session_id, run_id, step_id, artifact_id, source_kind,
                import_batch_id, conversation_id
            ) VALUES (
                'conversation_message:' || NEW.id,
                NEW.tenant_id,
                NEW.id,
                'conversation_message',
                COALESCE((
                    SELECT title FROM conversation_import_conversations
                    WHERE id = NEW.conversation_id AND tenant_id = NEW.tenant_id
                ), ''),
                NEW.content,
                COALESCE(NEW.normalized_created_at, NEW.created_at),
                NULL,
                NULL,
                NULL,
                (SELECT source_artifact_id FROM conversation_import_batches
                 WHERE id = NEW.batch_id AND tenant_id = NEW.tenant_id),
                NEW.importer_id,
                NEW.batch_id,
                NEW.conversation_id
            );
        END
        """
    )


def _upgrade_postgres_immutability(connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE OR REPLACE FUNCTION reject_conversation_import_mutation()
        RETURNS trigger AS $function$
        BEGIN
            RAISE EXCEPTION 'conversation import records are immutable';
        END;
        $function$ LANGUAGE plpgsql
        """
    )
    for table in _IMMUTABLE_TABLES:
        connection.exec_driver_sql(
            f"DROP TRIGGER IF EXISTS immutable_{table} ON {table}"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_conversation_import_mutation()"
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        op.execute(
            "DELETE FROM search_entries WHERE kind = 'conversation_message'"
        )
        op.execute("DROP TRIGGER IF EXISTS search_conversation_import_messages_ai")
        for table in reversed(_IMMUTABLE_TABLES):
            op.execute(f"DROP TRIGGER IF EXISTS immutable_{table}_delete")
            op.execute(f"DROP TRIGGER IF EXISTS immutable_{table}_update")
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("search_entries")
        }
        with op.batch_alter_table("search_entries") as batch:
            if "conversation_id" in columns:
                batch.drop_column("conversation_id")
            if "import_batch_id" in columns:
                batch.drop_column("import_batch_id")
    elif connection.dialect.name == "postgresql":
        for table in reversed(_IMMUTABLE_TABLES):
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS immutable_{table} ON {table}"
            )
        connection.exec_driver_sql(
            "DROP FUNCTION IF EXISTS reject_conversation_import_mutation()"
        )
    op.execute("DROP TABLE IF EXISTS conversation_import_messages")
    op.execute("DROP TABLE IF EXISTS conversation_import_conversations")
    op.execute("DROP TABLE IF EXISTS conversation_import_batches")
