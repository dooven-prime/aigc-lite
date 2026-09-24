"""Baseline the existing aigc-lite relational schema."""

from __future__ import annotations

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member',
    created_at TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS users_tenant_idx ON users(tenant_id);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
    expires_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, title TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_tenant_idx ON sessions(tenant_id, updated_at);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_session_idx ON messages(tenant_id, session_id, id);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
    content TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS documents_tenant_idx ON documents(tenant_id, created_at);
CREATE TABLE IF NOT EXISTS document_chunks (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL, content TEXT NOT NULL, vector TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_tenant_idx ON document_chunks(tenant_id, document_id, chunk_index);
CREATE TABLE IF NOT EXISTS model_configs (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
    base_url TEXT NOT NULL, model TEXT NOT NULL, api_key TEXT NOT NULL DEFAULT '',
    input_price REAL NOT NULL DEFAULT 0, output_price REAL NOT NULL DEFAULT 0,
    is_default INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
    UNIQUE(tenant_id, name)
);
CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, provider_id TEXT NOT NULL,
    url TEXT NOT NULL, header_credentials TEXT NOT NULL DEFAULT '{}',
    risk TEXT NOT NULL DEFAULT 'low', required_scopes TEXT NOT NULL DEFAULT '[]',
    timeout_seconds REAL NOT NULL DEFAULT 30, enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, provider_id)
);
CREATE INDEX IF NOT EXISTS mcp_servers_tenant_idx ON mcp_servers(tenant_id, provider_id);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
    user_id TEXT, action TEXT NOT NULL, path TEXT NOT NULL, metadata TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_tenant_idx ON audit_logs(tenant_id, created_at);
CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
    model TEXT NOT NULL, prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL,
    cost REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_tenant_idx ON usage_records(tenant_id, created_at);
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, session_id TEXT NOT NULL,
    request_id TEXT NOT NULL, status TEXT NOT NULL, requested_model TEXT,
    selected_model TEXT NOT NULL, error_code TEXT, created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS runs_tenant_idx ON agent_runs(tenant_id, created_at);
CREATE TABLE IF NOT EXISTS run_steps (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
    sequence INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
    status TEXT NOT NULL, input_content TEXT NOT NULL DEFAULT '',
    output_content TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL, UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS steps_run_idx ON run_steps(tenant_id, run_id, sequence);
"""


def upgrade() -> None:
    connection = op.get_bind()
    for statement in (item.strip() for item in _SCHEMA.split(";")):
        if not statement:
            continue
        if connection.dialect.name == "postgresql":
            statement = statement.replace(
                "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"
            )
        connection.exec_driver_sql(statement)


def downgrade() -> None:
    for table in (
        "run_steps",
        "agent_runs",
        "usage_records",
        "audit_logs",
        "mcp_servers",
        "model_configs",
        "document_chunks",
        "documents",
        "messages",
        "sessions",
        "auth_sessions",
        "users",
        "tenants",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
