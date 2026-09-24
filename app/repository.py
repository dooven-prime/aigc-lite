"""Persistence ports and relational repository implementations.

The application depends on the small Repository protocol. SQLite is the
default for local deployments; PostgreSQL is selected by a SQLAlchemy URL.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Repository(Protocol):
    def init(self) -> None: ...

    def create_session(self, tenant_id: str, title: str) -> dict: ...

    def list_sessions(self, tenant_id: str) -> list[dict]: ...

    def get_session(self, tenant_id: str, session_id: str) -> dict | None: ...

    def add_message(self, tenant_id: str, session_id: str, role: str, content: str) -> None: ...

    def create_document(self, tenant_id: str, name: str, content: str, chunks: list[dict]) -> dict: ...

    def search_chunks(self, tenant_id: str, query: str, vector: list[float], limit: int) -> list[dict]: ...

    def create_tenant(self, name: str, tenant_id: str | None = None) -> dict: ...

    def list_tenants(self) -> list[dict]: ...

    def get_tenant(self, tenant_id: str) -> dict | None: ...

    def create_user(self, tenant_id: str, email: str, name: str, password_hash: str, role: str = "member") -> dict: ...

    def get_user_by_email(self, email: str) -> dict | None: ...

    def get_user(self, user_id: str) -> dict | None: ...

    def list_users(self, tenant_id: str) -> list[dict]: ...

    def count_users(self) -> int: ...

    def save_auth_session(self, token_hash: str, user_id: str, tenant_id: str, expires_at: str) -> None: ...

    def get_auth_session(self, token_hash: str) -> dict | None: ...

    def save_model_config(self, tenant_id: str, values: dict[str, Any]) -> dict: ...

    def list_model_configs(self, tenant_id: str) -> list[dict]: ...

    def get_model_config(self, tenant_id: str, name: str | None = None) -> dict | None: ...

    def save_mcp_server(self, tenant_id: str, values: dict[str, Any]) -> dict: ...

    def list_mcp_servers(self, tenant_id: str) -> list[dict]: ...

    def get_mcp_server(self, tenant_id: str, server_id: str) -> dict | None: ...

    def record_mcp_probe(
        self, tenant_id: str, server_id: str, result: dict[str, Any]
    ) -> dict: ...

    def delete_mcp_server(self, tenant_id: str, server_id: str) -> bool: ...

    def list_audit(self, tenant_id: str, limit: int = 100) -> list[dict]: ...

    def write_audit(self, tenant_id: str, action: str, path: str, metadata: dict, user_id: str | None = None) -> None: ...

    def usage(self, tenant_id: str, model: str, prompt_tokens: int, completion_tokens: int, cost: float) -> None: ...

    def usage_summary(self, tenant_id: str) -> dict: ...

    def create_run(
        self,
        tenant_id: str,
        session_id: str,
        request_id: str,
        requested_model: str | None,
        selected_model: str,
    ) -> dict: ...

    def append_run_step(
        self,
        tenant_id: str,
        run_id: str,
        sequence: int,
        kind: str,
        name: str,
        status: str,
        input_content: str,
        output_content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict: ...

    def finish_run(
        self, tenant_id: str, run_id: str, status: str, error_code: str | None = None
    ) -> None: ...

    def get_run(self, tenant_id: str, run_id: str) -> dict | None: ...

    def list_runs(self, tenant_id: str, limit: int = 50) -> list[dict]: ...

    def search_memory(self, tenant_id: str, query: str, limit: int = 20) -> list[dict]: ...

def _decode_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _decode_mcp_server(row: Any) -> dict[str, Any]:
    value = dict(row)
    try:
        headers = json.loads(value.get("header_credentials") or "{}")
    except (TypeError, json.JSONDecodeError):
        headers = {}
    try:
        scopes = json.loads(value.get("required_scopes") or "[]")
    except (TypeError, json.JSONDecodeError):
        scopes = []
    value["header_credentials"] = headers if isinstance(headers, dict) else {}
    value["required_scopes"] = scopes if isinstance(scopes, list) else []
    value["enabled"] = bool(value.get("enabled"))
    value["timeout_seconds"] = float(value.get("timeout_seconds") or 30)
    value["health_status"] = value.get("health_status") or "unknown"
    value.setdefault("last_tested_at", None)
    value.setdefault("last_error_code", None)
    value.setdefault("last_latency_ms", None)
    value.setdefault("last_tool_count", None)
    value["last_latency_ms"] = (
        int(value["last_latency_ms"])
        if value.get("last_latency_ms") is not None
        else None
    )
    value["last_tool_count"] = (
        int(value["last_tool_count"])
        if value.get("last_tool_count") is not None
        else None
    )
    return value


def _memory_results(rows: list[dict], query: str, limit: int) -> list[dict]:
    """Rank a bounded lexical candidate set without a search-server dependency."""
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return []
    ranked = []
    for row in rows:
        content = str(row.get("content") or "")
        title = str(row.get("title") or "")
        haystack = f"{title}\n{content}".casefold()
        score = sum(haystack.count(term) for term in terms)
        if not score:
            continue
        ranked.append(
            {
                "id": str(row["id"]),
                "kind": row["kind"],
                "title": title,
                "content": content[:4000],
                "score": float(score),
                "created_at": row["created_at"],
                "session_id": row.get("session_id"),
                "run_id": row.get("run_id"),
            }
        )
    ranked.sort(key=lambda item: (item["score"], item["created_at"]), reverse=True)
    return ranked[: max(1, min(limit, 50))]


class SQLiteRepository:
    """SQLite repository using short-lived connections for safe web requests."""

    def __init__(self, database_path: str | Path = "data/aigc-lite.db", connect_factory=None):
        self.database_path = Path(database_path)
        self._connect_factory = connect_factory

    def _connect(self) -> sqlite3.Connection:
        if self._connect_factory:
            return self._connect_factory()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self) -> None:
        from sqlalchemy import create_engine
        from sqlalchemy.engine import URL
        from sqlalchemy.pool import NullPool

        from .migrations import upgrade_database

        if self._connect_factory:
            engine = create_engine(
                "sqlite://", creator=self._connect_factory, poolclass=NullPool
            )
        else:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(
                URL.create("sqlite", database=str(self.database_path.resolve())),
                poolclass=NullPool,
            )
        try:
            with engine.connect() as connection:
                upgrade_database(str(engine.url), connection=connection)
        finally:
            engine.dispose()

    @staticmethod
    def _session(row: sqlite3.Row | dict, messages: list[dict] | None = None) -> dict:
        value = dict(row)
        value["messages"] = messages or []
        return value

    def create_session(self, tenant_id: str, title: str = "New conversation") -> dict:
        session_id = str(uuid.uuid4())
        now = utc_now()
        with self._connect() as db:
            db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                (session_id, tenant_id, title, now, now),
            )
        return {
            "id": session_id,
            "tenant_id": tenant_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }

    def list_sessions(self, tenant_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM sessions WHERE tenant_id = ? ORDER BY updated_at DESC",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_session(self, tenant_id: str, session_id: str) -> dict | None:
        with self._connect() as db:
            session = db.execute(
                "SELECT * FROM sessions WHERE tenant_id = ? AND id = ?",
                (tenant_id, session_id),
            ).fetchone()
            if session is None:
                return None
            messages = db.execute(
                "SELECT role, content, created_at FROM messages "
                "WHERE tenant_id = ? AND session_id = ? ORDER BY id",
                (tenant_id, session_id),
            ).fetchall()
        return self._session(session, [dict(row) for row in messages])

    def add_message(self, tenant_id: str, session_id: str, role: str, content: str) -> None:
        now = utc_now()
        with self._connect() as db:
            owned = db.execute(
                "SELECT 1 FROM sessions WHERE tenant_id = ? AND id = ?",
                (tenant_id, session_id),
            ).fetchone()
            if owned is None:
                raise KeyError(session_id)
            db.execute(
                "INSERT INTO messages(session_id, tenant_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, tenant_id, role, content, now),
            )
            db.execute(
                "UPDATE sessions SET updated_at = ? WHERE tenant_id = ? AND id = ?",
                (now, tenant_id, session_id),
            )

    def create_document(self, tenant_id: str, name: str, content: str, chunks: list[dict]) -> dict:
        document = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "name": name,
            "content": content,
            "created_at": utc_now(),
        }
        with self._connect() as db:
            db.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?)", tuple(document.values()))
            db.executemany(
                "INSERT INTO document_chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        str(uuid.uuid4()), document["id"], tenant_id, item["index"],
                        item["content"], json.dumps(item["vector"]), document["created_at"],
                    )
                    for item in chunks
                ],
            )
        return {key: value for key, value in document.items() if key != "content"}

    def search_chunks(self, tenant_id: str, query: str, vector: list[float], limit: int = 5) -> list[dict]:
        terms = [term.lower() for term in query.split() if term.strip()]
        with self._connect() as db:
            rows = db.execute(
                "SELECT c.id, c.content, c.vector, d.name FROM document_chunks c "
                "JOIN documents d ON d.id = c.document_id "
                "WHERE c.tenant_id = ?",
                (tenant_id,),
            ).fetchall()
        from .vectors import cosine_similarity

        ranked = []
        for row in rows:
            text = row["content"].lower()
            lexical = sum(text.count(term) for term in terms)
            similarity = cosine_similarity(vector, json.loads(row["vector"]))
            score = lexical + similarity
            if score > 0:
                ranked.append({
                    "id": row["id"], "name": row["name"], "content": row["content"],
                    "score": score,
                })
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[: max(1, min(limit, 20))]

    def create_tenant(self, name: str, tenant_id: str | None = None) -> dict:
        value = {"id": tenant_id or str(uuid.uuid4()), "name": name, "created_at": utc_now()}
        with self._connect() as db:
            db.execute(
                "INSERT INTO tenants(id, name, created_at, is_active) VALUES (?, ?, ?, 1)",
                tuple(value.values()),
            )
        return value

    def list_tenants(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT id, name, created_at, is_active FROM tenants ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    def get_tenant(self, tenant_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT id, name, created_at, is_active FROM tenants WHERE id = ?",
                (tenant_id,),
            ).fetchone()
        return dict(row) if row else None

    def create_user(self, tenant_id: str, email: str, name: str, password_hash: str, role: str = "member") -> dict:
        value = {
            "id": str(uuid.uuid4()), "tenant_id": tenant_id, "email": email.lower(),
            "name": name, "password_hash": password_hash, "role": role, "created_at": utc_now(),
        }
        with self._connect() as db:
            db.execute(
                "INSERT INTO users(id, tenant_id, email, name, password_hash, role, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", tuple(value.values()),
            )
        return {key: item for key, item in value.items() if key != "password_hash"}

    def get_user_by_email(self, email: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE email = ? AND is_active = 1", (email.lower(),)).fetchone()
        return dict(row) if row else None

    def get_user(self, user_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (user_id,)).fetchone()
        return dict(row) if row else None

    def list_users(self, tenant_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, tenant_id, email, name, role, created_at FROM users WHERE tenant_id = ? ORDER BY created_at",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_users(self) -> int:
        with self._connect() as db:
            row = db.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        return int(row["count"])

    def save_auth_session(self, token_hash: str, user_id: str, tenant_id: str, expires_at: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO auth_sessions VALUES (?, ?, ?, ?, ?)",
                (token_hash, user_id, tenant_id, expires_at, utc_now()),
            )

    def get_auth_session(self, token_hash: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM auth_sessions WHERE token_hash = ? AND expires_at > ?",
                (token_hash, utc_now()),
            ).fetchone()
        return dict(row) if row else None

    def save_model_config(self, tenant_id: str, values: dict[str, Any]) -> dict:
        from .secrets import encrypt

        model = {
            "id": values.get("id") or str(uuid.uuid4()), "tenant_id": tenant_id,
            "name": values["name"], "base_url": values["base_url"], "model": values["model"],
            "api_key": encrypt(values.get("api_key", "")), "input_price": values.get("input_price", 0),
            "output_price": values.get("output_price", 0), "is_default": int(values.get("is_default", False)),
            "created_at": values.get("created_at", utc_now()),
        }
        with self._connect() as db:
            db.execute("DELETE FROM model_configs WHERE tenant_id = ? AND name = ?", (tenant_id, model["name"]))
            db.execute("INSERT INTO model_configs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(model.values()))
        return {key: value for key, value in model.items() if key != "api_key"}

    def list_model_configs(self, tenant_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, tenant_id, name, base_url, model, input_price, output_price, is_default, created_at "
                "FROM model_configs WHERE tenant_id = ? ORDER BY name", (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_model_config(self, tenant_id: str, name: str | None = None) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM model_configs WHERE tenant_id = ? AND (name = ? OR (? IS NULL AND is_default = 1)) "
                "ORDER BY is_default DESC LIMIT 1", (tenant_id, name, name),
            ).fetchone()
        if not row:
            return None
        value = dict(row)
        from .secrets import decrypt

        value["api_key"] = decrypt(value.get("api_key", ""))
        return value

    def save_mcp_server(self, tenant_id: str, values: dict[str, Any]) -> dict:
        now = utc_now()
        with self._connect() as db:
            existing = db.execute(
                "SELECT id, created_at FROM mcp_servers "
                "WHERE tenant_id = ? AND provider_id = ?",
                (tenant_id, values["provider_id"]),
            ).fetchone()
            server = {
                "id": existing["id"] if existing else str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "provider_id": values["provider_id"],
                "url": values["url"],
                "header_credentials": json.dumps(
                    values.get("header_credentials") or {}, ensure_ascii=False
                ),
                "risk": values.get("risk", "low"),
                "required_scopes": json.dumps(
                    values.get("required_scopes") or [], ensure_ascii=False
                ),
                "timeout_seconds": float(values.get("timeout_seconds", 30)),
                "enabled": int(values.get("enabled", True)),
                "created_at": existing["created_at"] if existing else now,
                "updated_at": now,
            }
            db.execute(
                "DELETE FROM mcp_servers WHERE tenant_id = ? AND provider_id = ?",
                (tenant_id, server["provider_id"]),
            )
            db.execute(
                "INSERT INTO mcp_servers(id, tenant_id, provider_id, url, "
                "header_credentials, risk, required_scopes, timeout_seconds, enabled, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(server.values()),
            )
        return _decode_mcp_server(server)

    def list_mcp_servers(self, tenant_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM mcp_servers WHERE tenant_id = ? ORDER BY provider_id",
                (tenant_id,),
            ).fetchall()
        return [_decode_mcp_server(row) for row in rows]

    def get_mcp_server(self, tenant_id: str, server_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM mcp_servers WHERE tenant_id = ? AND id = ?",
                (tenant_id, server_id),
            ).fetchone()
        return _decode_mcp_server(row) if row else None

    def record_mcp_probe(
        self, tenant_id: str, server_id: str, result: dict[str, Any]
    ) -> dict:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE mcp_servers SET health_status = ?, last_tested_at = ?, "
                "last_error_code = ?, last_latency_ms = ?, last_tool_count = ? "
                "WHERE tenant_id = ? AND id = ?",
                (
                    result["health_status"],
                    result["last_tested_at"],
                    result.get("last_error_code"),
                    result.get("last_latency_ms"),
                    result.get("last_tool_count"),
                    tenant_id,
                    server_id,
                ),
            )
        if cursor.rowcount == 0:
            raise KeyError(server_id)
        value = self.get_mcp_server(tenant_id, server_id)
        if value is None:  # pragma: no cover - guarded by the update
            raise KeyError(server_id)
        return value

    def delete_mcp_server(self, tenant_id: str, server_id: str) -> bool:
        with self._connect() as db:
            result = db.execute(
                "DELETE FROM mcp_servers WHERE tenant_id = ? AND id = ?",
                (tenant_id, server_id),
            )
        return result.rowcount > 0

    def write_audit(self, tenant_id: str, action: str, path: str, metadata: dict, user_id: str | None = None) -> None:
        from .redaction import redact

        with self._connect() as db:
            db.execute(
                "INSERT INTO audit_logs(tenant_id, user_id, action, path, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (tenant_id, user_id, action, path, json.dumps(redact(metadata), ensure_ascii=False), utc_now()),
            )

    def usage(self, tenant_id: str, model: str, prompt_tokens: int, completion_tokens: int, cost: float) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO usage_records(tenant_id, model, prompt_tokens, completion_tokens, cost, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (tenant_id, model, prompt_tokens, completion_tokens, cost, utc_now()),
            )

    def usage_summary(self, tenant_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(prompt_tokens), 0) prompt_tokens, COALESCE(SUM(completion_tokens), 0) "
                "completion_tokens, COALESCE(SUM(cost), 0) cost, COUNT(*) requests FROM usage_records WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        return dict(row)

    def create_run(
        self,
        tenant_id: str,
        session_id: str,
        request_id: str,
        requested_model: str | None,
        selected_model: str,
    ) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "session_id": session_id,
            "request_id": request_id,
            "status": "running",
            "requested_model": requested_model,
            "selected_model": selected_model,
            "error_code": None,
            "created_at": utc_now(),
            "completed_at": None,
        }
        with self._connect() as db:
            db.execute(
                "INSERT INTO agent_runs(id, tenant_id, session_id, request_id, status, "
                "requested_model, selected_model, error_code, created_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return value

    def append_run_step(
        self,
        tenant_id: str,
        run_id: str,
        sequence: int,
        kind: str,
        name: str,
        status: str,
        input_content: str,
        output_content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        from .redaction import redact, redact_record_text

        safe_metadata = redact(metadata or {})
        value = {
            "id": str(uuid.uuid4()),
            "run_id": run_id,
            "tenant_id": tenant_id,
            "sequence": sequence,
            "kind": kind,
            "name": name,
            "status": status,
            "input_content": redact_record_text(input_content),
            "output_content": redact_record_text(output_content),
            "metadata": json.dumps(safe_metadata, ensure_ascii=False),
            "created_at": utc_now(),
        }
        with self._connect() as db:
            owned = db.execute(
                "SELECT 1 FROM agent_runs WHERE tenant_id = ? AND id = ?",
                (tenant_id, run_id),
            ).fetchone()
            if owned is None:
                raise KeyError(run_id)
            db.execute(
                "INSERT INTO run_steps(id, run_id, tenant_id, sequence, kind, name, status, "
                "input_content, output_content, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return {**value, "metadata": safe_metadata}

    def finish_run(
        self, tenant_id: str, run_id: str, status: str, error_code: str | None = None
    ) -> None:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE agent_runs SET status = ?, error_code = ?, completed_at = ? "
                "WHERE tenant_id = ? AND id = ?",
                (status, error_code, utc_now(), tenant_id, run_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(run_id)

    def get_run(self, tenant_id: str, run_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM agent_runs WHERE tenant_id = ? AND id = ?",
                (tenant_id, run_id),
            ).fetchone()
            if row is None:
                return None
            steps = db.execute(
                "SELECT * FROM run_steps WHERE tenant_id = ? AND run_id = ? ORDER BY sequence",
                (tenant_id, run_id),
            ).fetchall()
        value = dict(row)
        value["steps"] = [
            {**dict(step), "metadata": _decode_metadata(step["metadata"])} for step in steps
        ]
        return value

    def list_runs(self, tenant_id: str, limit: int = 50) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM agent_runs WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def search_memory(self, tenant_id: str, query: str, limit: int = 20) -> list[dict]:
        candidate_limit = 500
        with self._connect() as db:
            messages = db.execute(
                "SELECT m.id, 'message' kind, s.title, m.content, m.created_at, "
                "m.session_id, NULL run_id FROM messages m "
                "JOIN sessions s ON s.id = m.session_id "
                "WHERE m.tenant_id = ? ORDER BY m.id DESC LIMIT ?",
                (tenant_id, candidate_limit),
            ).fetchall()
            documents = db.execute(
                "SELECT c.id, 'document' kind, d.name title, c.content, c.created_at, "
                "NULL session_id, NULL run_id FROM document_chunks c "
                "JOIN documents d ON d.id = c.document_id "
                "WHERE c.tenant_id = ? ORDER BY c.created_at DESC LIMIT ?",
                (tenant_id, candidate_limit),
            ).fetchall()
            steps = db.execute(
                "SELECT id, 'run_step' kind, name title, "
                "input_content || '\n' || output_content content, created_at, "
                "NULL session_id, run_id FROM run_steps "
                "WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, candidate_limit),
            ).fetchall()
        rows = [dict(row) for row in (*messages, *documents, *steps)]
        return _memory_results(rows, query, limit)

    def list_audit(self, tenant_id: str, limit: int = 100) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, tenant_id, user_id, action, path, metadata, created_at "
                "FROM audit_logs WHERE tenant_id = ? ORDER BY id DESC LIMIT ?",
                (tenant_id, max(1, min(limit, 500))),
            ).fetchall()
        values = [dict(row) for row in rows]
        for value in values:
            value["metadata"] = _decode_metadata(value.get("metadata"))
        return values


class PostgresRepository:
    """PostgreSQL adapter with the same public methods as SQLiteRepository.

    SQLAlchemy is imported only when this backend is selected, keeping the
    default installation lightweight for personal deployments.
    """

    def __init__(self, database_url: str):
        from sqlalchemy import create_engine

        self.database_url = database_url
        self.engine = create_engine(database_url, pool_pre_ping=True, future=True)

    def init(self) -> None:
        from .migrations import upgrade_database

        with self.engine.connect() as connection:
            upgrade_database(self.database_url, connection=connection)

    def _execute(self, statement: str, values: dict[str, Any] | None = None):
        from sqlalchemy import text

        with self.engine.begin() as connection:
            return connection.execute(text(statement), values or {})

    def _one(self, statement: str, values: dict[str, Any] | None = None) -> dict | None:
        from sqlalchemy import text

        with self.engine.connect() as connection:
            result = connection.execute(text(statement), values or {}).mappings().first()
        return dict(result) if result else None

    def _many(self, statement: str, values: dict[str, Any] | None = None) -> list[dict]:
        from sqlalchemy import text

        with self.engine.connect() as connection:
            rows = connection.execute(text(statement), values or {}).mappings().all()
        return [dict(row) for row in rows]

    def create_session(self, tenant_id: str, title: str = "New conversation") -> dict:
        value = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, "title": title, "created_at": utc_now()}
        self._execute(
            "INSERT INTO sessions(id, tenant_id, title, created_at, updated_at) "
            "VALUES (:id, :tenant_id, :title, :created_at, :created_at)", value,
        )
        return {**value, "updated_at": value["created_at"], "messages": []}

    def list_sessions(self, tenant_id: str) -> list[dict]:
        return self._many(
            "SELECT * FROM sessions WHERE tenant_id = :tenant_id ORDER BY updated_at DESC",
            {"tenant_id": tenant_id},
        )

    def get_session(self, tenant_id: str, session_id: str) -> dict | None:
        session = self._one(
            "SELECT * FROM sessions WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": session_id},
        )
        if not session:
            return None
        session["messages"] = self._many(
            "SELECT role, content, created_at FROM messages "
            "WHERE tenant_id = :tenant_id AND session_id = :id ORDER BY id",
            {"tenant_id": tenant_id, "id": session_id},
        )
        return session

    def add_message(self, tenant_id: str, session_id: str, role: str, content: str) -> None:
        if not self._one(
            "SELECT 1 FROM sessions WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": session_id},
        ):
            raise KeyError(session_id)
        now = utc_now()
        self._execute(
            "INSERT INTO messages(session_id, tenant_id, role, content, created_at) "
            "VALUES (:session_id, :tenant_id, :role, :content, :created_at)",
            {"session_id": session_id, "tenant_id": tenant_id, "role": role, "content": content, "created_at": now},
        )
        self._execute(
            "UPDATE sessions SET updated_at = :now WHERE tenant_id = :tenant_id AND id = :id",
            {"now": now, "tenant_id": tenant_id, "id": session_id},
        )

    def create_document(self, tenant_id: str, name: str, content: str, chunks: list[dict]) -> dict:
        document = {
            "id": str(uuid.uuid4()), "tenant_id": tenant_id, "name": name,
            "content": content, "created_at": utc_now(),
        }
        self._execute("INSERT INTO documents VALUES (:id, :tenant_id, :name, :content, :created_at)", document)
        for item in chunks:
            self._execute(
                "INSERT INTO document_chunks VALUES (:id, :document_id, :tenant_id, :chunk_index, :content, :vector, :created_at)",
                {
                    "id": str(uuid.uuid4()), "document_id": document["id"], "tenant_id": tenant_id,
                    "chunk_index": item["index"], "content": item["content"],
                    "vector": json.dumps(item["vector"]), "created_at": document["created_at"],
                },
            )
        return {key: value for key, value in document.items() if key != "content"}

    def search_chunks(self, tenant_id: str, query: str, vector: list[float], limit: int = 5) -> list[dict]:
        terms = [term.lower() for term in query.split() if term.strip()]
        rows = self._many(
            "SELECT c.id, c.content, c.vector, d.name FROM document_chunks c "
            "JOIN documents d ON d.id = c.document_id WHERE c.tenant_id = :tenant_id",
            {"tenant_id": tenant_id},
        )
        from .vectors import cosine_similarity

        ranked = []
        for row in rows:
            lexical = sum(row["content"].lower().count(term) for term in terms)
            score = lexical + cosine_similarity(vector, json.loads(row["vector"]))
            if score > 0:
                ranked.append({"id": row["id"], "name": row["name"], "content": row["content"], "score": score})
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[: max(1, min(limit, 20))]

    def create_tenant(self, name: str, tenant_id: str | None = None) -> dict:
        value = {"id": tenant_id or str(uuid.uuid4()), "name": name, "created_at": utc_now()}
        self._execute(
            "INSERT INTO tenants(id, name, created_at, is_active) VALUES (:id, :name, :created_at, 1)", value
        )
        return value

    def list_tenants(self) -> list[dict]:
        return self._many("SELECT id, name, created_at, is_active FROM tenants ORDER BY created_at")

    def get_tenant(self, tenant_id: str) -> dict | None:
        return self._one(
            "SELECT id, name, created_at, is_active FROM tenants WHERE id = :id",
            {"id": tenant_id},
        )

    def create_user(self, tenant_id: str, email: str, name: str, password_hash: str, role: str = "member") -> dict:
        value = {
            "id": str(uuid.uuid4()), "tenant_id": tenant_id, "email": email.lower(), "name": name,
            "password_hash": password_hash, "role": role, "created_at": utc_now(),
        }
        self._execute(
            "INSERT INTO users(id, tenant_id, email, name, password_hash, role, created_at) "
            "VALUES (:id, :tenant_id, :email, :name, :password_hash, :role, :created_at)", value,
        )
        return {key: item for key, item in value.items() if key != "password_hash"}

    def get_user_by_email(self, email: str) -> dict | None:
        return self._one("SELECT * FROM users WHERE email = :email AND is_active = 1", {"email": email.lower()})

    def get_user(self, user_id: str) -> dict | None:
        return self._one("SELECT * FROM users WHERE id = :id AND is_active = 1", {"id": user_id})

    def list_users(self, tenant_id: str) -> list[dict]:
        return self._many(
            "SELECT id, tenant_id, email, name, role, created_at FROM users "
            "WHERE tenant_id = :tenant_id ORDER BY created_at", {"tenant_id": tenant_id},
        )

    def count_users(self) -> int:
        row = self._one("SELECT COUNT(*) AS count FROM users")
        return int(row["count"]) if row else 0

    def save_auth_session(self, token_hash: str, user_id: str, tenant_id: str, expires_at: str) -> None:
        self._execute(
            "INSERT INTO auth_sessions VALUES (:token_hash, :user_id, :tenant_id, :expires_at, :created_at)",
            {"token_hash": token_hash, "user_id": user_id, "tenant_id": tenant_id, "expires_at": expires_at, "created_at": utc_now()},
        )

    def get_auth_session(self, token_hash: str) -> dict | None:
        return self._one(
            "SELECT * FROM auth_sessions WHERE token_hash = :token_hash AND expires_at > :now",
            {"token_hash": token_hash, "now": utc_now()},
        )

    def save_model_config(self, tenant_id: str, values: dict[str, Any]) -> dict:
        from .secrets import encrypt

        model = {
            "id": values.get("id") or str(uuid.uuid4()), "tenant_id": tenant_id,
            "name": values["name"], "base_url": values["base_url"], "model": values["model"],
            "api_key": encrypt(values.get("api_key", "")), "input_price": values.get("input_price", 0),
            "output_price": values.get("output_price", 0), "is_default": int(values.get("is_default", False)),
            "created_at": values.get("created_at", utc_now()),
        }
        self._execute("DELETE FROM model_configs WHERE tenant_id = :tenant_id AND name = :name", model)
        self._execute("INSERT INTO model_configs VALUES (:id, :tenant_id, :name, :base_url, :model, :api_key, :input_price, :output_price, :is_default, :created_at)", model)
        return {key: value for key, value in model.items() if key != "api_key"}

    def list_model_configs(self, tenant_id: str) -> list[dict]:
        return self._many(
            "SELECT id, tenant_id, name, base_url, model, input_price, output_price, is_default, created_at "
            "FROM model_configs WHERE tenant_id = :tenant_id ORDER BY name", {"tenant_id": tenant_id},
        )

    def get_model_config(self, tenant_id: str, name: str | None = None) -> dict | None:
        value = self._one(
            "SELECT * FROM model_configs WHERE tenant_id = :tenant_id AND "
            "(:name IS NOT NULL AND name = :name OR :name IS NULL AND is_default = 1) "
            "ORDER BY is_default DESC LIMIT 1", {"tenant_id": tenant_id, "name": name},
        )
        if value:
            from .secrets import decrypt

            value["api_key"] = decrypt(value.get("api_key", ""))
        return value

    def save_mcp_server(self, tenant_id: str, values: dict[str, Any]) -> dict:
        existing = self._one(
            "SELECT id, created_at FROM mcp_servers "
            "WHERE tenant_id = :tenant_id AND provider_id = :provider_id",
            {"tenant_id": tenant_id, "provider_id": values["provider_id"]},
        )
        now = utc_now()
        server = {
            "id": existing["id"] if existing else str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "provider_id": values["provider_id"],
            "url": values["url"],
            "header_credentials": json.dumps(
                values.get("header_credentials") or {}, ensure_ascii=False
            ),
            "risk": values.get("risk", "low"),
            "required_scopes": json.dumps(
                values.get("required_scopes") or [], ensure_ascii=False
            ),
            "timeout_seconds": float(values.get("timeout_seconds", 30)),
            "enabled": int(values.get("enabled", True)),
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
        }
        self._execute(
            "DELETE FROM mcp_servers "
            "WHERE tenant_id = :tenant_id AND provider_id = :provider_id",
            server,
        )
        self._execute(
            "INSERT INTO mcp_servers(id, tenant_id, provider_id, url, "
            "header_credentials, risk, required_scopes, timeout_seconds, enabled, "
            "created_at, updated_at) VALUES (:id, :tenant_id, :provider_id, :url, "
            ":header_credentials, :risk, :required_scopes, :timeout_seconds, :enabled, "
            ":created_at, :updated_at)",
            server,
        )
        return _decode_mcp_server(server)

    def list_mcp_servers(self, tenant_id: str) -> list[dict]:
        rows = self._many(
            "SELECT * FROM mcp_servers WHERE tenant_id = :tenant_id "
            "ORDER BY provider_id",
            {"tenant_id": tenant_id},
        )
        return [_decode_mcp_server(row) for row in rows]

    def get_mcp_server(self, tenant_id: str, server_id: str) -> dict | None:
        row = self._one(
            "SELECT * FROM mcp_servers WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": server_id},
        )
        return _decode_mcp_server(row) if row else None

    def record_mcp_probe(
        self, tenant_id: str, server_id: str, result: dict[str, Any]
    ) -> dict:
        values = {**result, "tenant_id": tenant_id, "id": server_id}
        updated = self._execute(
            "UPDATE mcp_servers SET health_status = :health_status, "
            "last_tested_at = :last_tested_at, last_error_code = :last_error_code, "
            "last_latency_ms = :last_latency_ms, last_tool_count = :last_tool_count "
            "WHERE tenant_id = :tenant_id AND id = :id",
            values,
        )
        if updated.rowcount == 0:
            raise KeyError(server_id)
        value = self.get_mcp_server(tenant_id, server_id)
        if value is None:  # pragma: no cover - guarded by the update
            raise KeyError(server_id)
        return value

    def delete_mcp_server(self, tenant_id: str, server_id: str) -> bool:
        result = self._execute(
            "DELETE FROM mcp_servers WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": server_id},
        )
        return result.rowcount > 0

    def write_audit(self, tenant_id: str, action: str, path: str, metadata: dict, user_id: str | None = None) -> None:
        from .redaction import redact

        self._execute(
            "INSERT INTO audit_logs(tenant_id, user_id, action, path, metadata, created_at) "
            "VALUES (:tenant_id, :user_id, :action, :path, :metadata, :created_at)",
            {"tenant_id": tenant_id, "user_id": user_id, "action": action, "path": path, "metadata": json.dumps(redact(metadata)), "created_at": utc_now()},
        )

    def usage(self, tenant_id: str, model: str, prompt_tokens: int, completion_tokens: int, cost: float) -> None:
        self._execute(
            "INSERT INTO usage_records(tenant_id, model, prompt_tokens, completion_tokens, cost, created_at) "
            "VALUES (:tenant_id, :model, :prompt_tokens, :completion_tokens, :cost, :created_at)",
            {"tenant_id": tenant_id, "model": model, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "cost": cost, "created_at": utc_now()},
        )

    def usage_summary(self, tenant_id: str) -> dict:
        return self._one(
            "SELECT COALESCE(SUM(prompt_tokens), 0) prompt_tokens, COALESCE(SUM(completion_tokens), 0) completion_tokens, "
            "COALESCE(SUM(cost), 0) cost, COUNT(*) requests FROM usage_records WHERE tenant_id = :tenant_id",
            {"tenant_id": tenant_id},
        ) or {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0, "requests": 0}

    def create_run(
        self,
        tenant_id: str,
        session_id: str,
        request_id: str,
        requested_model: str | None,
        selected_model: str,
    ) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "session_id": session_id,
            "request_id": request_id,
            "status": "running",
            "requested_model": requested_model,
            "selected_model": selected_model,
            "error_code": None,
            "created_at": utc_now(),
            "completed_at": None,
        }
        self._execute(
            "INSERT INTO agent_runs(id, tenant_id, session_id, request_id, status, "
            "requested_model, selected_model, error_code, created_at, completed_at) "
            "VALUES (:id, :tenant_id, :session_id, :request_id, :status, :requested_model, "
            ":selected_model, :error_code, :created_at, :completed_at)",
            value,
        )
        return value

    def append_run_step(
        self,
        tenant_id: str,
        run_id: str,
        sequence: int,
        kind: str,
        name: str,
        status: str,
        input_content: str,
        output_content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        if not self._one(
            "SELECT 1 FROM agent_runs WHERE tenant_id = :tenant_id AND id = :run_id",
            {"tenant_id": tenant_id, "run_id": run_id},
        ):
            raise KeyError(run_id)
        from .redaction import redact, redact_record_text

        safe_metadata = redact(metadata or {})
        value = {
            "id": str(uuid.uuid4()),
            "run_id": run_id,
            "tenant_id": tenant_id,
            "sequence": sequence,
            "kind": kind,
            "name": name,
            "status": status,
            "input_content": redact_record_text(input_content),
            "output_content": redact_record_text(output_content),
            "metadata": json.dumps(safe_metadata, ensure_ascii=False),
            "created_at": utc_now(),
        }
        self._execute(
            "INSERT INTO run_steps(id, run_id, tenant_id, sequence, kind, name, status, "
            "input_content, output_content, metadata, created_at) "
            "VALUES (:id, :run_id, :tenant_id, :sequence, :kind, :name, :status, "
            ":input_content, :output_content, :metadata, :created_at)",
            value,
        )
        return {**value, "metadata": safe_metadata}

    def finish_run(
        self, tenant_id: str, run_id: str, status: str, error_code: str | None = None
    ) -> None:
        result = self._execute(
            "UPDATE agent_runs SET status = :status, error_code = :error_code, "
            "completed_at = :completed_at WHERE tenant_id = :tenant_id AND id = :run_id",
            {
                "status": status,
                "error_code": error_code,
                "completed_at": utc_now(),
                "tenant_id": tenant_id,
                "run_id": run_id,
            },
        )
        if result.rowcount == 0:
            raise KeyError(run_id)

    def get_run(self, tenant_id: str, run_id: str) -> dict | None:
        value = self._one(
            "SELECT * FROM agent_runs WHERE tenant_id = :tenant_id AND id = :run_id",
            {"tenant_id": tenant_id, "run_id": run_id},
        )
        if value is None:
            return None
        steps = self._many(
            "SELECT * FROM run_steps WHERE tenant_id = :tenant_id AND run_id = :run_id "
            "ORDER BY sequence",
            {"tenant_id": tenant_id, "run_id": run_id},
        )
        value["steps"] = [
            {**step, "metadata": _decode_metadata(step.get("metadata"))} for step in steps
        ]
        return value

    def list_runs(self, tenant_id: str, limit: int = 50) -> list[dict]:
        return self._many(
            "SELECT * FROM agent_runs WHERE tenant_id = :tenant_id "
            "ORDER BY created_at DESC LIMIT :limit",
            {"tenant_id": tenant_id, "limit": max(1, min(limit, 200))},
        )

    def search_memory(self, tenant_id: str, query: str, limit: int = 20) -> list[dict]:
        values = {"tenant_id": tenant_id, "candidate_limit": 500}
        messages = self._many(
            "SELECT CAST(m.id AS TEXT) id, 'message' kind, s.title, m.content, "
            "m.created_at, m.session_id, NULL run_id FROM messages m "
            "JOIN sessions s ON s.id = m.session_id WHERE m.tenant_id = :tenant_id "
            "ORDER BY m.id DESC LIMIT :candidate_limit",
            values,
        )
        documents = self._many(
            "SELECT c.id, 'document' kind, d.name title, c.content, c.created_at, "
            "NULL session_id, NULL run_id FROM document_chunks c "
            "JOIN documents d ON d.id = c.document_id WHERE c.tenant_id = :tenant_id "
            "ORDER BY c.created_at DESC LIMIT :candidate_limit",
            values,
        )
        steps = self._many(
            "SELECT id, 'run_step' kind, name title, "
            "CONCAT(input_content, '\n', output_content) content, created_at, "
            "NULL session_id, run_id FROM run_steps WHERE tenant_id = :tenant_id "
            "ORDER BY created_at DESC LIMIT :candidate_limit",
            values,
        )
        return _memory_results([*messages, *documents, *steps], query, limit)

    def list_audit(self, tenant_id: str, limit: int = 100) -> list[dict]:
        rows = self._many(
            "SELECT id, tenant_id, user_id, action, path, metadata, created_at "
            "FROM audit_logs WHERE tenant_id = :tenant_id ORDER BY id DESC LIMIT :limit",
            {"tenant_id": tenant_id, "limit": max(1, min(limit, 500))},
        )
        for row in rows:
            try:
                row["metadata"] = json.loads(row["metadata"])
            except (TypeError, json.JSONDecodeError):
                pass
        return rows
