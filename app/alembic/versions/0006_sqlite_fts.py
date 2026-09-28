"""Add a synchronized SQLite FTS5 execution-memory index."""

from __future__ import annotations

from alembic import op
from sqlalchemy.exc import OperationalError

revision = "0006_sqlite_fts"
down_revision = "0005_artifacts_citations"
branch_labels = None
depends_on = None

_ENTRY_COLUMNS = (
    "entry_key, tenant_id, source_id, kind, title, content, created_at, "
    "session_id, run_id, step_id, artifact_id, source_kind"
)
_UPDATE_COLUMNS = (
    "tenant_id = excluded.tenant_id, source_id = excluded.source_id, "
    "kind = excluded.kind, title = excluded.title, content = excluded.content, "
    "created_at = excluded.created_at, session_id = excluded.session_id, "
    "run_id = excluded.run_id, step_id = excluded.step_id, "
    "artifact_id = excluded.artifact_id, source_kind = excluded.source_kind"
)


def _source_triggers(table: str, key: str, values: str) -> None:
    op.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS search_{table}_ai
        AFTER INSERT ON {table}
        BEGIN
            INSERT INTO search_entries({_ENTRY_COLUMNS}) VALUES ({values})
            ON CONFLICT(entry_key) DO UPDATE SET {_UPDATE_COLUMNS};
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS search_{table}_au
        AFTER UPDATE ON {table}
        BEGIN
            DELETE FROM search_entries WHERE entry_key = {key.replace('NEW.', 'OLD.')};
            INSERT INTO search_entries({_ENTRY_COLUMNS}) VALUES ({values})
            ON CONFLICT(entry_key) DO UPDATE SET {_UPDATE_COLUMNS};
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS search_{table}_ad
        AFTER DELETE ON {table}
        BEGIN
            DELETE FROM search_entries WHERE entry_key = {key.replace('NEW.', 'OLD.')};
        END
        """
    )


def _create_fts(connection) -> bool:
    for tokenizer in ("trigram", "unicode61"):
        try:
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS search_entries_fts USING fts5("
                "title, content, content='search_entries', content_rowid='row_id', "
                f"tokenize='{tokenizer}')"
            )
            return True
        except OperationalError:
            continue
    return False


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        return
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS search_entries (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_key TEXT NOT NULL UNIQUE,
            tenant_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            session_id TEXT,
            run_id TEXT,
            step_id TEXT,
            artifact_id TEXT,
            source_kind TEXT
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS search_entries_tenant_idx "
        "ON search_entries(tenant_id, created_at)"
    )
    fts_enabled = _create_fts(connection)
    if fts_enabled:
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS search_entries_ai
            AFTER INSERT ON search_entries
            BEGIN
                INSERT INTO search_entries_fts(rowid, title, content)
                VALUES (NEW.row_id, NEW.title, NEW.content);
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS search_entries_ad
            AFTER DELETE ON search_entries
            BEGIN
                INSERT INTO search_entries_fts(search_entries_fts, rowid, title, content)
                VALUES ('delete', OLD.row_id, OLD.title, OLD.content);
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS search_entries_au
            AFTER UPDATE ON search_entries
            BEGIN
                INSERT INTO search_entries_fts(search_entries_fts, rowid, title, content)
                VALUES ('delete', OLD.row_id, OLD.title, OLD.content);
                INSERT INTO search_entries_fts(rowid, title, content)
                VALUES (NEW.row_id, NEW.title, NEW.content);
            END
            """
        )

    _source_triggers(
        "messages",
        "'message:' || NEW.id",
        "'message:' || NEW.id, NEW.tenant_id, CAST(NEW.id AS TEXT), 'message', "
        "COALESCE((SELECT title FROM sessions WHERE id = NEW.session_id "
        "AND tenant_id = NEW.tenant_id), ''), NEW.content, NEW.created_at, "
        "NEW.session_id, NULL, NULL, NULL, NULL",
    )
    _source_triggers(
        "document_chunks",
        "'document:' || NEW.id",
        "'document:' || NEW.id, NEW.tenant_id, NEW.id, 'document', "
        "COALESCE((SELECT name FROM documents WHERE id = NEW.document_id "
        "AND tenant_id = NEW.tenant_id), ''), NEW.content, NEW.created_at, "
        "NULL, NULL, NULL, NULL, NULL",
    )
    _source_triggers(
        "run_steps",
        "'run_step:' || NEW.id",
        "'run_step:' || NEW.id, NEW.tenant_id, NEW.id, 'run_step', NEW.name, "
        "NEW.input_content || char(10) || NEW.output_content, NEW.created_at, "
        "NULL, NEW.run_id, NEW.id, NULL, NEW.kind",
    )
    _source_triggers(
        "artifacts",
        "'artifact:' || NEW.id",
        "'artifact:' || NEW.id, NEW.tenant_id, NEW.id, 'artifact', NEW.name, "
        "NEW.content_text || CASE WHEN NEW.uri IS NULL THEN '' "
        "ELSE char(10) || NEW.uri END, NEW.created_at, NULL, NEW.run_id, "
        "NEW.step_id, NEW.id, NEW.kind",
    )
    _source_triggers(
        "citations",
        "'citation:' || NEW.id",
        "'citation:' || NEW.id, NEW.tenant_id, NEW.id, 'citation', NEW.title, "
        "NEW.excerpt || CASE WHEN NEW.source_uri IS NULL THEN '' "
        "ELSE char(10) || NEW.source_uri END, NEW.created_at, NULL, NEW.run_id, "
        "NEW.step_id, NEW.artifact_id, NEW.source_kind",
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS search_sessions_title_au
        AFTER UPDATE OF title ON sessions
        BEGIN
            UPDATE search_entries SET title = NEW.title
            WHERE tenant_id = NEW.tenant_id AND kind = 'message'
            AND session_id = NEW.id;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS search_documents_name_au
        AFTER UPDATE OF name ON documents
        BEGIN
            UPDATE search_entries SET title = NEW.name
            WHERE tenant_id = NEW.tenant_id AND kind = 'document'
            AND source_id IN (
                SELECT id FROM document_chunks
                WHERE tenant_id = NEW.tenant_id AND document_id = NEW.id
            );
        END
        """
    )

    # Backfill every source that existed before this migration. Future writes
    # are maintained by the source triggers above.
    op.execute(
        f"""
        INSERT OR IGNORE INTO search_entries({_ENTRY_COLUMNS})
        SELECT 'message:' || m.id, m.tenant_id, CAST(m.id AS TEXT), 'message',
               s.title, m.content, m.created_at, m.session_id,
               NULL, NULL, NULL, NULL
        FROM messages m JOIN sessions s ON s.id = m.session_id
        """
    )
    op.execute(
        f"""
        INSERT OR IGNORE INTO search_entries({_ENTRY_COLUMNS})
        SELECT 'document:' || c.id, c.tenant_id, c.id, 'document',
               d.name, c.content, c.created_at, NULL,
               NULL, NULL, NULL, NULL
        FROM document_chunks c JOIN documents d ON d.id = c.document_id
        """
    )
    op.execute(
        f"""
        INSERT OR IGNORE INTO search_entries({_ENTRY_COLUMNS})
        SELECT 'run_step:' || id, tenant_id, id, 'run_step', name,
               input_content || char(10) || output_content, created_at,
               NULL, run_id, id, NULL, kind
        FROM run_steps
        """
    )
    op.execute(
        f"""
        INSERT OR IGNORE INTO search_entries({_ENTRY_COLUMNS})
        SELECT 'artifact:' || id, tenant_id, id, 'artifact', name,
               content_text || CASE WHEN uri IS NULL THEN '' ELSE char(10) || uri END,
               created_at, NULL, run_id, step_id, id, kind
        FROM artifacts
        """
    )
    op.execute(
        f"""
        INSERT OR IGNORE INTO search_entries({_ENTRY_COLUMNS})
        SELECT 'citation:' || id, tenant_id, id, 'citation', title,
               excerpt || CASE WHEN source_uri IS NULL THEN ''
                               ELSE char(10) || source_uri END,
               created_at, NULL, run_id, step_id, artifact_id, source_kind
        FROM citations
        """
    )
    if fts_enabled:
        op.execute(
            "INSERT INTO search_entries_fts(search_entries_fts) VALUES ('rebuild')"
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        return
    for trigger in (
        "search_documents_name_au",
        "search_sessions_title_au",
        "search_citations_ad",
        "search_citations_au",
        "search_citations_ai",
        "search_artifacts_ad",
        "search_artifacts_au",
        "search_artifacts_ai",
        "search_run_steps_ad",
        "search_run_steps_au",
        "search_run_steps_ai",
        "search_document_chunks_ad",
        "search_document_chunks_au",
        "search_document_chunks_ai",
        "search_messages_ad",
        "search_messages_au",
        "search_messages_ai",
        "search_entries_ad",
        "search_entries_au",
        "search_entries_ai",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute("DROP TABLE IF EXISTS search_entries_fts")
    op.execute("DROP TABLE IF EXISTS search_entries")
