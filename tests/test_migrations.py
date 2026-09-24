import sqlite3

from app.repository import SQLiteRepository

_LEGACY_MCP_SCHEMA = """
CREATE TABLE mcp_servers (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, provider_id TEXT NOT NULL,
    url TEXT NOT NULL, header_credentials TEXT NOT NULL DEFAULT '{}',
    risk TEXT NOT NULL DEFAULT 'low', required_scopes TEXT NOT NULL DEFAULT '[]',
    timeout_seconds REAL NOT NULL DEFAULT 30, enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, provider_id)
);
"""


def test_alembic_adopts_existing_database_and_preserves_records(tmp_path) -> None:
    path = tmp_path / "existing.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(_LEGACY_MCP_SCHEMA)
        connection.execute(
            "INSERT INTO mcp_servers(id, tenant_id, provider_id, url, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("server-1", "workspace-a", "research", "https://mcp.example/mcp", "now", "now"),
        )

    repository = SQLiteRepository(path)
    repository.init()

    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(mcp_servers)")
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert {
        "health_status",
        "last_tested_at",
        "last_error_code",
        "last_latency_ms",
        "last_tool_count",
    } <= columns
    assert revision == "0002_mcp_probe_state"
    assert repository.get_mcp_server("workspace-a", "server-1")["provider_id"] == "research"
