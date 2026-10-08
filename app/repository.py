"""Persistence ports and relational repository implementations.

The application depends on the small Repository protocol. SQLite is the
default for local deployments; PostgreSQL is selected by a SQLAlchemy URL.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .core.credentials import (
    encrypted_credential_id,
    validate_workspace_credential_map,
)
from .ports.enforcement_repository import EnforcementRepository
from .ports.import_repository import ImportRepository
from .ports.math_release_repository import MathReleaseRepository
from .ports.qualification_repository import QualificationRepository
from .ports.research_repository import ResearchRepository
from .ports.review_repository import ReviewRepository
from .repositories.common import decode_list as _decode_list
from .repositories.common import decode_metadata as _decode_metadata
from .repositories.common import utc_now
from .repositories.conversation_imports import (
    PostgresImportRepositoryMixin,
    SQLiteImportRepositoryMixin,
)
from .repositories.enforcement import (
    PostgresEnforcementRepositoryMixin,
    SQLiteEnforcementRepositoryMixin,
)
from .repositories.math_release import (
    PostgresMathReleaseRepositoryMixin,
    SQLiteMathReleaseRepositoryMixin,
)
from .repositories.qualification import (
    PostgresQualificationRepositoryMixin,
    SQLiteQualificationRepositoryMixin,
)
from .repositories.research import (
    PostgresResearchRepositoryMixin,
    SQLiteResearchRepositoryMixin,
)
from .repositories.review import (
    PostgresReviewRepositoryMixin,
    SQLiteReviewRepositoryMixin,
)

if TYPE_CHECKING:
    from .ports.search import SearchBackend


class Repository(
    ResearchRepository,
    QualificationRepository,
    ReviewRepository,
    ImportRepository,
    MathReleaseRepository,
    EnforcementRepository,
    Protocol,
):
    def init(self) -> None: ...

    def schema_revision(self) -> str | None: ...

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

    def create_credential(
        self, tenant_id: str, name: str, secret_value: str
    ) -> dict: ...

    def list_credentials(self, tenant_id: str) -> list[dict]: ...

    def get_credential(self, tenant_id: str, credential_id: str) -> dict | None: ...

    def replace_credential(
        self, tenant_id: str, credential_id: str, secret_value: str
    ) -> dict | None: ...

    def revoke_credential(self, tenant_id: str, credential_id: str) -> dict | None: ...

    def create_scheduled_task(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def list_scheduled_tasks(self, tenant_id: str) -> list[dict]: ...

    def get_scheduled_task(
        self, tenant_id: str, task_id: str
    ) -> dict | None: ...

    def list_active_scheduled_tasks(self) -> list[dict]: ...

    def list_due_scheduled_tasks(self, due_at: str, limit: int = 100) -> list[dict]: ...

    def set_scheduled_task_status(
        self,
        tenant_id: str,
        task_id: str,
        status: str,
        *,
        expected_status: str,
    ) -> dict | None: ...

    def advance_scheduled_task(
        self,
        tenant_id: str,
        task_id: str,
        *,
        expected_next_run_at: str,
        status: str,
        last_run_at: str,
        next_run_at: str | None,
    ) -> dict | None: ...

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
        *,
        capability_set_id: str = "runtime.none.v1",
        capability_policy_hash: str | None = None,
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

    def get_run_by_request_id(
        self, tenant_id: str, request_id: str
    ) -> dict | None: ...

    def list_runs(self, tenant_id: str, limit: int = 50) -> list[dict]: ...

    def create_artifact(self, tenant_id: str, values: dict[str, Any]) -> dict: ...

    def get_artifact(self, tenant_id: str, artifact_id: str) -> dict | None: ...

    def list_artifacts(
        self, tenant_id: str, limit: int = 50, run_id: str | None = None
    ) -> list[dict]: ...

    def create_citation(self, tenant_id: str, values: dict[str, Any]) -> dict: ...

    def list_citations(
        self,
        tenant_id: str,
        limit: int = 100,
        run_id: str | None = None,
        artifact_id: str | None = None,
    ) -> list[dict]: ...

    def create_evidence_protocol(self, tenant_id: str, values: dict[str, Any]) -> dict: ...

    def get_evidence_protocol(self, tenant_id: str, protocol_id: str) -> dict | None: ...

    def get_evidence_protocol_by_hash(
        self, tenant_id: str, profile: str, content_hash: str
    ) -> dict | None: ...

    def list_evidence_protocols(
        self, tenant_id: str, limit: int = 50, profile: str | None = None
    ) -> list[dict]: ...

    def create_evidence_claim(self, tenant_id: str, values: dict[str, Any]) -> dict: ...

    def create_execution_receipt(self, tenant_id: str, values: dict[str, Any]) -> dict: ...

    def create_evidence_review(self, tenant_id: str, values: dict[str, Any]) -> dict: ...

    def create_freeze_manifest(self, tenant_id: str, values: dict[str, Any]) -> dict: ...

    def create_decision_cases(
        self, tenant_id: str, protocol_id: str, receipt_id: str, values: list[dict[str, Any]]
    ) -> list[dict]: ...

    def list_decision_cases(
        self,
        tenant_id: str,
        *,
        protocol_id: str | None = None,
        family: str | None = None,
        resolution: str | None = None,
        limit: int = 500,
    ) -> list[dict]: ...

    def get_decision_case(self, tenant_id: str, case_id: str) -> dict | None: ...

    def search_backend(self) -> SearchBackend: ...

    def search_memory(self, tenant_id: str, query: str, limit: int = 20) -> list[dict]: ...


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


def _decode_scheduled_task(row: Any) -> dict[str, Any]:
    value = dict(row)
    try:
        payload = json.loads(value.get("payload") or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    value["payload"] = payload if isinstance(payload, dict) else {}
    value["interval_seconds"] = (
        float(value["interval_seconds"])
        if value.get("interval_seconds") is not None
        else None
    )
    return value


def _decode_artifact(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["metadata"] = _decode_metadata(value.get("metadata"))
    value["version"] = int(value.get("version") or 1)
    value["size_bytes"] = int(value.get("size_bytes") or 0)
    return value


def _decode_citation(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["locator"] = _decode_metadata(value.get("locator"))
    value["metadata"] = _decode_metadata(value.get("metadata"))
    return value


def _decode_protocol(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["version"] = int(value.get("version") or 1)
    value["stop_conditions"] = _decode_list(value.get("stop_conditions"))
    value["budget"] = _decode_metadata(value.get("budget"))
    return value


def _decode_claim(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["evidence_refs"] = _decode_list(value.get("evidence_refs"))
    value["prohibited_upgrades"] = _decode_list(value.get("prohibited_upgrades"))
    return value


def _decode_receipt(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["runtime"] = _decode_metadata(value.get("runtime"))
    value["budget"] = _decode_metadata(value.get("budget"))
    value["artifact_ids"] = _decode_list(value.get("artifact_ids"))
    value["metadata"] = _decode_metadata(value.get("metadata"))
    return value


def _decode_review(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["independent"] = bool(value.get("independent"))
    value["evidence_refs"] = _decode_list(value.get("evidence_refs"))
    return value


def _decode_freeze_manifest(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["version"] = int(value.get("version") or 1)
    value["members"] = _decode_list(value.get("members"))
    return value


def _decode_decision_case(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["questions"] = _decode_metadata(value.get("questions"))
    value["answers"] = _decode_metadata(value.get("answers"))
    value["gold_answers"] = _decode_metadata(value.get("gold_answers"))
    value["has_gold"] = bool(value.get("has_gold"))
    value["confidence"] = float(value.get("confidence") or 0)
    value["entropy"] = float(value.get("entropy") or 0)
    value["threshold"] = float(value.get("threshold") or 0)
    return value


class SQLiteRepository(
    SQLiteResearchRepositoryMixin,
    SQLiteQualificationRepositoryMixin,
    SQLiteReviewRepositoryMixin,
    SQLiteImportRepositoryMixin,
    SQLiteMathReleaseRepositoryMixin,
    SQLiteEnforcementRepositoryMixin,
):
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

    def schema_revision(self) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT version_num FROM alembic_version").fetchone()
        return str(row[0]) if row is not None else None

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
        credential_reference = values["credential_reference"]
        credential_id = encrypted_credential_id(credential_reference)
        model = {
            "id": values.get("id") or str(uuid.uuid4()), "tenant_id": tenant_id,
            "name": values["name"], "base_url": values["base_url"], "model": values["model"],
            "api_key": "", "input_price": values.get("input_price", 0),
            "output_price": values.get("output_price", 0), "is_default": int(values.get("is_default", False)),
            "created_at": values.get("created_at", utc_now()),
            "credential_reference": credential_reference,
        }
        with self._connect() as db:
            credential = db.execute(
                "SELECT revoked_at FROM credentials WHERE tenant_id = ? AND id = ?",
                (tenant_id, credential_id),
            ).fetchone()
            if credential is None or credential["revoked_at"]:
                raise ValueError("credential_reference_not_active")
            db.execute(
                "DELETE FROM model_configs WHERE tenant_id = ? AND name = ?",
                (tenant_id, model["name"]),
            )
            db.execute(
                "INSERT INTO model_configs(id, tenant_id, name, base_url, model, "
                "api_key, input_price, output_price, is_default, created_at, "
                "credential_reference) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(model.values()),
            )
        return {key: value for key, value in model.items() if key != "api_key"}

    def list_model_configs(self, tenant_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, tenant_id, name, base_url, model, input_price, output_price, is_default, created_at, credential_reference "
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
        value.pop("api_key", None)
        return value

    def save_mcp_server(self, tenant_id: str, values: dict[str, Any]) -> dict:
        header_credentials = validate_workspace_credential_map(
            values.get("header_credentials") or {}
        )
        credential_ids = {
            encrypted_credential_id(reference)
            for reference in header_credentials.values()
        }
        for credential_id in credential_ids:
            credential = self.get_credential(tenant_id, credential_id)
            if credential is None or credential.get("revoked_at"):
                raise ValueError("credential_reference_not_active")
        now = utc_now()
        with self._connect() as db:
            existing = db.execute(
                "SELECT id, created_at FROM mcp_servers WHERE tenant_id = ? AND provider_id = ?",
                (tenant_id, values["provider_id"]),
            ).fetchone()
            server = {
                "id": existing["id"] if existing else str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "provider_id": values["provider_id"],
                "url": values["url"],
                "header_credentials": json.dumps(
                    header_credentials, ensure_ascii=False
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

    def create_credential(
        self, tenant_id: str, name: str, secret_value: str
    ) -> dict:
        now = utc_now()
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "name": name,
            "secret_value": secret_value,
            "created_at": now,
            "updated_at": now,
            "revoked_at": None,
        }
        with self._connect() as db:
            result = db.execute(
                "INSERT OR IGNORE INTO credentials(id, tenant_id, name, secret_value, "
                "created_at, updated_at, revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        if result.rowcount == 0:
            raise ValueError("credential_name_conflict")
        return value

    def list_credentials(self, tenant_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, tenant_id, name, created_at, updated_at, revoked_at "
                "FROM credentials WHERE tenant_id = ? ORDER BY name",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_credential(self, tenant_id: str, credential_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM credentials WHERE tenant_id = ? AND id = ?",
                (tenant_id, credential_id),
            ).fetchone()
        return dict(row) if row else None

    def replace_credential(
        self, tenant_id: str, credential_id: str, secret_value: str
    ) -> dict | None:
        now = utc_now()
        with self._connect() as db:
            result = db.execute(
                "UPDATE credentials SET secret_value = ?, updated_at = ?, revoked_at = NULL "
                "WHERE tenant_id = ? AND id = ?",
                (secret_value, now, tenant_id, credential_id),
            )
        if result.rowcount == 0:
            return None
        return self.get_credential(tenant_id, credential_id)

    def revoke_credential(self, tenant_id: str, credential_id: str) -> dict | None:
        now = utc_now()
        with self._connect() as db:
            result = db.execute(
                "UPDATE credentials SET revoked_at = COALESCE(revoked_at, ?), "
                "updated_at = ? WHERE tenant_id = ? AND id = ?",
                (now, now, tenant_id, credential_id),
            )
        if result.rowcount == 0:
            return None
        return self.get_credential(tenant_id, credential_id)

    def create_scheduled_task(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        now = utc_now()
        task = {
            "id": values.get("id") or str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "name": values["name"],
            "target": values["target"],
            "payload": json.dumps(values.get("payload") or {}, ensure_ascii=False),
            "trigger_kind": values["trigger_kind"],
            "status": values.get("status", "scheduled"),
            "next_run_at": values.get("next_run_at"),
            "interval_seconds": values.get("interval_seconds"),
            "last_run_at": None,
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as db:
            db.execute(
                "INSERT INTO scheduled_tasks(id, tenant_id, name, target, payload, "
                "trigger_kind, status, next_run_at, interval_seconds, last_run_at, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(task.values()),
            )
        return _decode_scheduled_task(task)

    def list_scheduled_tasks(self, tenant_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM scheduled_tasks WHERE tenant_id = ? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        return [_decode_scheduled_task(row) for row in rows]

    def get_scheduled_task(self, tenant_id: str, task_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM scheduled_tasks WHERE tenant_id = ? AND id = ?",
                (tenant_id, task_id),
            ).fetchone()
        return _decode_scheduled_task(row) if row else None

    def list_active_scheduled_tasks(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM scheduled_tasks WHERE status = 'scheduled' ORDER BY next_run_at, id"
            ).fetchall()
        return [_decode_scheduled_task(row) for row in rows]

    def list_due_scheduled_tasks(self, due_at: str, limit: int = 100) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM scheduled_tasks WHERE status = 'scheduled' "
                "AND next_run_at IS NOT NULL AND next_run_at <= ? "
                "ORDER BY next_run_at, id LIMIT ?",
                (due_at, max(1, min(limit, 1000))),
            ).fetchall()
        return [_decode_scheduled_task(row) for row in rows]

    def set_scheduled_task_status(
        self,
        tenant_id: str,
        task_id: str,
        status: str,
        *,
        expected_status: str,
    ) -> dict | None:
        with self._connect() as db:
            result = db.execute(
                "UPDATE scheduled_tasks SET status = ?, updated_at = ? "
                "WHERE tenant_id = ? AND id = ? AND status = ?",
                (status, utc_now(), tenant_id, task_id, expected_status),
            )
        return self.get_scheduled_task(tenant_id, task_id) if result.rowcount > 0 else None

    def advance_scheduled_task(
        self,
        tenant_id: str,
        task_id: str,
        *,
        expected_next_run_at: str,
        status: str,
        last_run_at: str,
        next_run_at: str | None,
    ) -> dict | None:
        with self._connect() as db:
            result = db.execute(
                "UPDATE scheduled_tasks SET status = ?, last_run_at = ?, "
                "next_run_at = ?, updated_at = ? WHERE tenant_id = ? AND id = ? "
                "AND status = 'scheduled' AND next_run_at = ?",
                (
                    status,
                    last_run_at,
                    next_run_at,
                    utc_now(),
                    tenant_id,
                    task_id,
                    expected_next_run_at,
                ),
            )
        return self.get_scheduled_task(tenant_id, task_id) if result.rowcount > 0 else None

    def write_audit(self, tenant_id: str, action: str, path: str, metadata: dict, user_id: str | None = None) -> None:
        from .redaction import redact

        with self._connect() as db:
            db.execute(
                "INSERT INTO audit_logs(tenant_id, user_id, action, path, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    tenant_id,
                    user_id,
                    action,
                    path,
                    json.dumps(redact(metadata), ensure_ascii=False),
                    utc_now(),
                ),
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
        *,
        capability_set_id: str = "runtime.none.v1",
        capability_policy_hash: str | None = None,
    ) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "session_id": session_id,
            "request_id": request_id,
            "status": "running",
            "requested_model": requested_model,
            "selected_model": selected_model,
            "capability_set_id": capability_set_id,
            "capability_policy_hash": capability_policy_hash,
            "error_code": None,
            "created_at": utc_now(),
            "completed_at": None,
        }
        with self._connect() as db:
            db.execute(
                "INSERT INTO agent_runs(id, tenant_id, session_id, request_id, status, "
                "requested_model, selected_model, capability_set_id, "
                "capability_policy_hash, error_code, created_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        value["artifacts"] = self.list_artifacts(tenant_id, 200, run_id)
        value["citations"] = self.list_citations(tenant_id, 500, run_id=run_id)
        return value

    def get_run_by_request_id(
        self, tenant_id: str, request_id: str
    ) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT id FROM agent_runs WHERE tenant_id = ? AND request_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (tenant_id, request_id),
            ).fetchone()
        return self.get_run(tenant_id, row["id"]) if row is not None else None

    def list_runs(self, tenant_id: str, limit: int = 50) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM agent_runs WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_artifact(self, tenant_id: str, values: dict[str, Any]) -> dict:
        artifact_id = str(uuid.uuid4())
        value = {
            "id": artifact_id,
            "tenant_id": tenant_id,
            "series_id": values.get("series_id") or artifact_id,
            "version": int(values.get("version") or 1),
            "run_id": values.get("run_id"),
            "step_id": values.get("step_id"),
            "name": values["name"],
            "kind": values["kind"],
            "media_type": values["media_type"],
            "content_text": values.get("content_text") or "",
            "uri": values.get("uri"),
            "content_hash": values["content_hash"],
            "size_bytes": int(values.get("size_bytes") or 0),
            "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
            "created_at": utc_now(),
        }
        with self._connect() as db:
            if value["run_id"] is not None:
                owned_run = db.execute(
                    "SELECT 1 FROM agent_runs WHERE tenant_id = ? AND id = ?",
                    (tenant_id, value["run_id"]),
                ).fetchone()
                if owned_run is None:
                    raise KeyError(value["run_id"])
            if value["step_id"] is not None:
                owned_step = db.execute(
                    "SELECT run_id FROM run_steps WHERE tenant_id = ? AND id = ?",
                    (tenant_id, value["step_id"]),
                ).fetchone()
                if owned_step is None or (
                    value["run_id"] is not None
                    and owned_step["run_id"] != value["run_id"]
                ):
                    raise KeyError(value["step_id"])
            db.execute(
                "INSERT INTO artifacts(id, tenant_id, series_id, version, run_id, "
                "step_id, name, kind, media_type, content_text, uri, content_hash, "
                "size_bytes, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return _decode_artifact(value)

    def get_artifact(self, tenant_id: str, artifact_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM artifacts WHERE tenant_id = ? AND id = ?",
                (tenant_id, artifact_id),
            ).fetchone()
        return _decode_artifact(row) if row is not None else None

    def list_artifacts(
        self, tenant_id: str, limit: int = 50, run_id: str | None = None
    ) -> list[dict]:
        sql = "SELECT * FROM artifacts WHERE tenant_id = ?"
        parameters: list[Any] = [tenant_id]
        if run_id is not None:
            sql += " AND run_id = ?"
            parameters.append(run_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(max(1, min(limit, 200)))
        with self._connect() as db:
            rows = db.execute(sql, parameters).fetchall()
        return [_decode_artifact(row) for row in rows]

    def create_citation(self, tenant_id: str, values: dict[str, Any]) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "run_id": values.get("run_id"),
            "step_id": values.get("step_id"),
            "artifact_id": values.get("artifact_id"),
            "source_kind": values["source_kind"],
            "source_id": values.get("source_id"),
            "source_uri": values.get("source_uri"),
            "title": values["title"],
            "locator": json.dumps(values.get("locator") or {}, ensure_ascii=False),
            "excerpt": values.get("excerpt") or "",
            "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
            "created_at": utc_now(),
        }
        with self._connect() as db:
            if value["run_id"] is not None:
                owned_run = db.execute(
                    "SELECT 1 FROM agent_runs WHERE tenant_id = ? AND id = ?",
                    (tenant_id, value["run_id"]),
                ).fetchone()
                if owned_run is None:
                    raise KeyError(value["run_id"])
            if value["step_id"] is not None:
                owned_step = db.execute(
                    "SELECT run_id FROM run_steps WHERE tenant_id = ? AND id = ?",
                    (tenant_id, value["step_id"]),
                ).fetchone()
                if owned_step is None or (
                    value["run_id"] is not None
                    and owned_step["run_id"] != value["run_id"]
                ):
                    raise KeyError(value["step_id"])
            if value["artifact_id"] is not None:
                owned_artifact = db.execute(
                    "SELECT 1 FROM artifacts WHERE tenant_id = ? AND id = ?",
                    (tenant_id, value["artifact_id"]),
                ).fetchone()
                if owned_artifact is None:
                    raise KeyError(value["artifact_id"])
            db.execute(
                "INSERT INTO citations(id, tenant_id, run_id, step_id, artifact_id, "
                "source_kind, source_id, source_uri, title, locator, excerpt, metadata, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return _decode_citation(value)

    def list_citations(
        self,
        tenant_id: str,
        limit: int = 100,
        run_id: str | None = None,
        artifact_id: str | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM citations WHERE tenant_id = ?"
        parameters: list[Any] = [tenant_id]
        if run_id is not None:
            sql += " AND run_id = ?"
            parameters.append(run_id)
        if artifact_id is not None:
            sql += " AND artifact_id = ?"
            parameters.append(artifact_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(max(1, min(limit, 500)))
        with self._connect() as db:
            rows = db.execute(sql, parameters).fetchall()
        return [_decode_citation(row) for row in rows]

    def create_evidence_protocol(self, tenant_id: str, values: dict[str, Any]) -> dict:
        now = utc_now()
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "profile": values["profile"],
            "name": values["name"],
            "version": int(values.get("version") or 1),
            "status": values["status"],
            "purpose": values["purpose"],
            "scope": values["scope"],
            "completion_predicate": values["completion_predicate"],
            "stop_conditions": json.dumps(values.get("stop_conditions") or [], ensure_ascii=False),
            "budget": json.dumps(values.get("budget") or {}, ensure_ascii=False),
            "source_uri": values.get("source_uri"),
            "content_hash": values["content_hash"],
            "created_at": now,
            "frozen_at": now if values["status"] == "frozen" else None,
        }
        with self._connect() as db:
            db.execute(
                "INSERT INTO evidence_protocols(id, tenant_id, profile, name, version, "
                "status, purpose, scope, completion_predicate, stop_conditions, budget, "
                "source_uri, content_hash, created_at, frozen_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return _decode_protocol(value)

    def get_evidence_protocol(self, tenant_id: str, protocol_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM evidence_protocols WHERE tenant_id = ? AND id = ?",
                (tenant_id, protocol_id),
            ).fetchone()
            if row is None:
                return None
            claims = db.execute(
                "SELECT * FROM evidence_claims WHERE tenant_id = ? AND protocol_id = ? "
                "ORDER BY created_at",
                (tenant_id, protocol_id),
            ).fetchall()
            receipts = db.execute(
                "SELECT * FROM execution_receipts WHERE tenant_id = ? AND protocol_id = ? "
                "ORDER BY created_at",
                (tenant_id, protocol_id),
            ).fetchall()
            freezes = db.execute(
                "SELECT * FROM freeze_manifests WHERE tenant_id = ? AND protocol_id = ? "
                "ORDER BY version",
                (tenant_id, protocol_id),
            ).fetchall()
            receipt_ids = [item["id"] for item in receipts]
            reviews = []
            if receipt_ids:
                placeholders = ",".join("?" for _ in receipt_ids)
                reviews = db.execute(
                    "SELECT * FROM evidence_reviews WHERE tenant_id = ? "
                    f"AND receipt_id IN ({placeholders}) ORDER BY created_at",
                    [tenant_id, *receipt_ids],
                ).fetchall()
        value = _decode_protocol(row)
        decoded_receipts = [_decode_receipt(item) for item in receipts]
        decoded_reviews = [_decode_review(item) for item in reviews]
        for receipt in decoded_receipts:
            receipt["reviews"] = [
                item for item in decoded_reviews if item["receipt_id"] == receipt["id"]
            ]
        artifact_ids = {
            artifact_id
            for receipt in decoded_receipts
            for artifact_id in receipt["artifact_ids"]
        }
        value["claims"] = [_decode_claim(item) for item in claims]
        value["receipts"] = decoded_receipts
        value["freezes"] = [_decode_freeze_manifest(item) for item in freezes]
        value["artifacts"] = [
            artifact
            for artifact_id in artifact_ids
            if (artifact := self.get_artifact(tenant_id, artifact_id)) is not None
        ]
        return value

    def get_evidence_protocol_by_hash(
        self, tenant_id: str, profile: str, content_hash: str
    ) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT id FROM evidence_protocols WHERE tenant_id = ? "
                "AND profile = ? AND content_hash = ?",
                (tenant_id, profile, content_hash),
            ).fetchone()
        return self.get_evidence_protocol(tenant_id, row["id"]) if row else None

    def list_evidence_protocols(
        self, tenant_id: str, limit: int = 50, profile: str | None = None
    ) -> list[dict]:
        sql = "SELECT * FROM evidence_protocols WHERE tenant_id = ?"
        parameters: list[Any] = [tenant_id]
        if profile is not None:
            sql += " AND profile = ?"
            parameters.append(profile)
        sql += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(max(1, min(limit, 200)))
        with self._connect() as db:
            rows = db.execute(sql, parameters).fetchall()
        return [_decode_protocol(row) for row in rows]

    def create_evidence_claim(self, tenant_id: str, values: dict[str, Any]) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "protocol_id": values["protocol_id"],
            "run_id": values.get("run_id"),
            "statement": values["statement"],
            "resolution": values["resolution"],
            "scope": values["scope"],
            "evidence_refs": json.dumps(values.get("evidence_refs") or [], ensure_ascii=False),
            "prohibited_upgrades": json.dumps(
                values.get("prohibited_upgrades") or [], ensure_ascii=False
            ),
            "created_at": utc_now(),
        }
        with self._connect() as db:
            if (
                db.execute(
                    "SELECT 1 FROM evidence_protocols WHERE tenant_id = ? AND id = ?",
                    (tenant_id, value["protocol_id"]),
                ).fetchone()
                is None
            ):
                raise KeyError(value["protocol_id"])
            db.execute(
                "INSERT INTO evidence_claims(id, tenant_id, protocol_id, run_id, statement, "
                "resolution, scope, evidence_refs, prohibited_upgrades, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return _decode_claim(value)

    def create_execution_receipt(self, tenant_id: str, values: dict[str, Any]) -> dict:
        artifact_ids = list(values.get("artifact_ids") or [])
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "protocol_id": values["protocol_id"],
            "run_id": values.get("run_id"),
            "status": values["status"],
            "input_digest": values["input_digest"],
            "output_digest": values["output_digest"],
            "runtime": json.dumps(values.get("runtime") or {}, ensure_ascii=False),
            "budget": json.dumps(values.get("budget") or {}, ensure_ascii=False),
            "artifact_ids": json.dumps(artifact_ids, ensure_ascii=False),
            "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
            "created_at": utc_now(),
        }
        with self._connect() as db:
            if (
                db.execute(
                    "SELECT 1 FROM evidence_protocols WHERE tenant_id = ? AND id = ?",
                    (tenant_id, value["protocol_id"]),
                ).fetchone()
                is None
            ):
                raise KeyError(value["protocol_id"])
            if (
                value["run_id"] is not None
                and db.execute(
                    "SELECT 1 FROM agent_runs WHERE tenant_id = ? AND id = ?",
                    (tenant_id, value["run_id"]),
                ).fetchone()
                is None
            ):
                raise KeyError(value["run_id"])
            if artifact_ids:
                placeholders = ",".join("?" for _ in artifact_ids)
                owned = db.execute(
                    "SELECT COUNT(*) count FROM artifacts WHERE tenant_id = ? "
                    f"AND id IN ({placeholders})",
                    [tenant_id, *artifact_ids],
                ).fetchone()
                if owned is None or int(owned["count"]) != len(set(artifact_ids)):
                    raise KeyError("artifact_ids")
            db.execute(
                "INSERT INTO execution_receipts(id, tenant_id, protocol_id, run_id, status, "
                "input_digest, output_digest, runtime, budget, artifact_ids, metadata, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return _decode_receipt(value)

    def create_evidence_review(self, tenant_id: str, values: dict[str, Any]) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "receipt_id": values["receipt_id"],
            "reviewer_kind": values["reviewer_kind"],
            "reviewer_id": values.get("reviewer_id"),
            "independent": int(bool(values.get("independent"))),
            "status": values["status"],
            "finding": values["finding"],
            "evidence_refs": json.dumps(values.get("evidence_refs") or [], ensure_ascii=False),
            "created_at": utc_now(),
        }
        with self._connect() as db:
            if (
                db.execute(
                    "SELECT 1 FROM execution_receipts WHERE tenant_id = ? AND id = ?",
                    (tenant_id, value["receipt_id"]),
                ).fetchone()
                is None
            ):
                raise KeyError(value["receipt_id"])
            db.execute(
                "INSERT INTO evidence_reviews(id, tenant_id, receipt_id, reviewer_kind, "
                "reviewer_id, independent, status, finding, evidence_refs, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return _decode_review(value)

    def create_freeze_manifest(self, tenant_id: str, values: dict[str, Any]) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "protocol_id": values["protocol_id"],
            "name": values["name"],
            "version": int(values.get("version") or 1),
            "members": json.dumps(values.get("members") or [], ensure_ascii=False),
            "content_hash": values["content_hash"],
            "created_at": utc_now(),
        }
        with self._connect() as db:
            if (
                db.execute(
                    "SELECT 1 FROM evidence_protocols WHERE tenant_id = ? AND id = ?",
                    (tenant_id, value["protocol_id"]),
                ).fetchone()
                is None
            ):
                raise KeyError(value["protocol_id"])
            db.execute(
                "INSERT INTO freeze_manifests(id, tenant_id, protocol_id, name, version, "
                "members, content_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return _decode_freeze_manifest(value)

    def create_decision_cases(
        self, tenant_id: str, protocol_id: str, receipt_id: str, values: list[dict[str, Any]]
    ) -> list[dict]:
        created_at = utc_now()
        rows = []
        for item in values:
            rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "protocol_id": protocol_id,
                    "receipt_id": receipt_id,
                    "source_case_id": item["source_case_id"],
                    "family": item["family"],
                    "state_text": item["state_text"],
                    "questions": json.dumps(item["questions"], ensure_ascii=False),
                    "answers": json.dumps(item["answers"], ensure_ascii=False),
                    "gold_answers": json.dumps(item.get("gold_answers") or {}, ensure_ascii=False),
                    "has_gold": int(bool(item.get("gold_answers"))),
                    "resolution": item["resolution"],
                    "confidence": float(item["confidence"]),
                    "entropy": float(item["entropy"]),
                    "threshold": float(item["threshold"]),
                    "run_id": item.get("run_id"),
                    "step_id": item.get("step_id"),
                    "primary_artifact_id": item.get("primary_artifact_id"),
                    "created_at": created_at,
                }
            )
        with self._connect() as db:
            if (
                db.execute(
                    "SELECT 1 FROM evidence_protocols WHERE tenant_id = ? AND id = ?",
                    (tenant_id, protocol_id),
                ).fetchone()
                is None
            ):
                raise KeyError(protocol_id)
            if (
                db.execute(
                    "SELECT 1 FROM execution_receipts WHERE tenant_id = ? AND id = ?",
                    (tenant_id, receipt_id),
                ).fetchone()
                is None
            ):
                raise KeyError(receipt_id)
            db.executemany(
                "INSERT INTO decision_cases(id, tenant_id, protocol_id, receipt_id, "
                "source_case_id, family, state_text, questions, answers, gold_answers, "
                "has_gold, resolution, confidence, entropy, threshold, run_id, step_id, "
                "primary_artifact_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [tuple(row.values()) for row in rows],
            )
        return [_decode_decision_case(row) for row in rows]

    def list_decision_cases(
        self,
        tenant_id: str,
        *,
        protocol_id: str | None = None,
        family: str | None = None,
        resolution: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        sql = "SELECT * FROM decision_cases WHERE tenant_id = ?"
        parameters: list[Any] = [tenant_id]
        for column, value in (
            ("protocol_id", protocol_id),
            ("family", family),
            ("resolution", resolution),
        ):
            if value is not None:
                sql += f" AND {column} = ?"
                parameters.append(value)
        sql += " ORDER BY created_at DESC, source_case_id LIMIT ?"
        parameters.append(max(1, min(limit, 1000)))
        with self._connect() as db:
            rows = db.execute(sql, parameters).fetchall()
        return [_decode_decision_case(row) for row in rows]

    def get_decision_case(self, tenant_id: str, case_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM decision_cases WHERE tenant_id = ? AND id = ?",
                (tenant_id, case_id),
            ).fetchone()
        return _decode_decision_case(row) if row is not None else None

    def _search_candidates(self, tenant_id: str, limit: int) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT source_id id, kind, title, content, created_at, session_id, "
                "run_id, step_id, artifact_id, source_kind FROM search_entries "
                "WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def search_backend(self) -> SearchBackend:
        from .adapters.search import LexicalSearchBackend, SQLiteFTS5SearchBackend

        fallback = LexicalSearchBackend(self._search_candidates)
        return SQLiteFTS5SearchBackend(self._connect, fallback)

    def search_memory(self, tenant_id: str, query: str, limit: int = 20) -> list[dict]:
        return self.search_backend().search(tenant_id, query, limit)

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


class PostgresRepository(
    PostgresResearchRepositoryMixin,
    PostgresQualificationRepositoryMixin,
    PostgresReviewRepositoryMixin,
    PostgresImportRepositoryMixin,
    PostgresMathReleaseRepositoryMixin,
    PostgresEnforcementRepositoryMixin,
):
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

    def schema_revision(self) -> str | None:
        value = self._one("SELECT version_num FROM alembic_version")
        return str(value["version_num"]) if value is not None else None

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
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "title": title,
            "created_at": utc_now(),
        }
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
            {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "role": role,
                "content": content,
                "created_at": now,
            },
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
                ranked.append(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "content": row["content"],
                        "score": score,
                    }
                )
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[: max(1, min(limit, 20))]

    def create_tenant(self, name: str, tenant_id: str | None = None) -> dict:
        value = {"id": tenant_id or str(uuid.uuid4()), "name": name, "created_at": utc_now()}
        self._execute(
            "INSERT INTO tenants(id, name, created_at, is_active) VALUES (:id, :name, :created_at, 1)",
            value,
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
            {
                "token_hash": token_hash,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "expires_at": expires_at,
                "created_at": utc_now(),
            },
        )

    def get_auth_session(self, token_hash: str) -> dict | None:
        return self._one(
            "SELECT * FROM auth_sessions WHERE token_hash = :token_hash AND expires_at > :now",
            {"token_hash": token_hash, "now": utc_now()},
        )

    def save_model_config(self, tenant_id: str, values: dict[str, Any]) -> dict:
        credential_reference = values["credential_reference"]
        credential_id = encrypted_credential_id(credential_reference)
        credential = self._one(
            "SELECT revoked_at FROM credentials WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": credential_id},
        )
        if credential is None or credential["revoked_at"]:
            raise ValueError("credential_reference_not_active")
        model = {
            "id": values.get("id") or str(uuid.uuid4()), "tenant_id": tenant_id,
            "name": values["name"], "base_url": values["base_url"], "model": values["model"],
            "api_key": "", "input_price": values.get("input_price", 0),
            "output_price": values.get("output_price", 0), "is_default": int(values.get("is_default", False)),
            "created_at": values.get("created_at", utc_now()),
            "credential_reference": credential_reference,
        }
        self._execute(
            "DELETE FROM model_configs WHERE tenant_id = :tenant_id AND name = :name", model
        )
        self._execute(
            "INSERT INTO model_configs(id, tenant_id, name, base_url, model, api_key, "
            "input_price, output_price, is_default, created_at, credential_reference) "
            "VALUES (:id, :tenant_id, :name, :base_url, :model, :api_key, "
            ":input_price, :output_price, :is_default, :created_at, :credential_reference)",
            model,
        )
        return {key: value for key, value in model.items() if key != "api_key"}

    def list_model_configs(self, tenant_id: str) -> list[dict]:
        return self._many(
            "SELECT id, tenant_id, name, base_url, model, input_price, output_price, is_default, created_at, credential_reference "
            "FROM model_configs WHERE tenant_id = :tenant_id ORDER BY name", {"tenant_id": tenant_id},
        )

    def get_model_config(self, tenant_id: str, name: str | None = None) -> dict | None:
        if name is None:
            statement = (
                "SELECT * FROM model_configs WHERE tenant_id = :tenant_id "
                "AND is_default = 1 ORDER BY created_at DESC LIMIT 1"
            )
            parameters = {"tenant_id": tenant_id}
        else:
            statement = (
                "SELECT * FROM model_configs WHERE tenant_id = :tenant_id "
                "AND name = :name LIMIT 1"
            )
            parameters = {"tenant_id": tenant_id, "name": name}
        value = self._one(statement, parameters)
        if value:
            value.pop("api_key", None)
        return value

    def save_mcp_server(self, tenant_id: str, values: dict[str, Any]) -> dict:
        header_credentials = validate_workspace_credential_map(
            values.get("header_credentials") or {}
        )
        credential_ids = {
            encrypted_credential_id(reference)
            for reference in header_credentials.values()
        }
        for credential_id in credential_ids:
            credential = self.get_credential(tenant_id, credential_id)
            if credential is None or credential.get("revoked_at"):
                raise ValueError("credential_reference_not_active")
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
                header_credentials, ensure_ascii=False
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
            "DELETE FROM mcp_servers WHERE tenant_id = :tenant_id AND provider_id = :provider_id",
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
            "SELECT * FROM mcp_servers WHERE tenant_id = :tenant_id ORDER BY provider_id",
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

    def create_credential(
        self, tenant_id: str, name: str, secret_value: str
    ) -> dict:
        now = utc_now()
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "name": name,
            "secret_value": secret_value,
            "created_at": now,
            "updated_at": now,
            "revoked_at": None,
        }
        result = self._execute(
            "INSERT INTO credentials(id, tenant_id, name, secret_value, created_at, "
            "updated_at, revoked_at) VALUES (:id, :tenant_id, :name, :secret_value, "
            ":created_at, :updated_at, :revoked_at) ON CONFLICT (tenant_id, name) "
            "DO NOTHING",
            value,
        )
        if result.rowcount == 0:
            raise ValueError("credential_name_conflict")
        return value

    def list_credentials(self, tenant_id: str) -> list[dict]:
        return self._many(
            "SELECT id, tenant_id, name, created_at, updated_at, revoked_at "
            "FROM credentials WHERE tenant_id = :tenant_id ORDER BY name",
            {"tenant_id": tenant_id},
        )

    def get_credential(self, tenant_id: str, credential_id: str) -> dict | None:
        return self._one(
            "SELECT * FROM credentials WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": credential_id},
        )

    def replace_credential(
        self, tenant_id: str, credential_id: str, secret_value: str
    ) -> dict | None:
        result = self._execute(
            "UPDATE credentials SET secret_value = :secret_value, "
            "updated_at = :updated_at, revoked_at = NULL "
            "WHERE tenant_id = :tenant_id AND id = :id",
            {
                "secret_value": secret_value,
                "updated_at": utc_now(),
                "tenant_id": tenant_id,
                "id": credential_id,
            },
        )
        return self.get_credential(tenant_id, credential_id) if result.rowcount > 0 else None

    def revoke_credential(self, tenant_id: str, credential_id: str) -> dict | None:
        now = utc_now()
        result = self._execute(
            "UPDATE credentials SET revoked_at = COALESCE(revoked_at, :now), "
            "updated_at = :now WHERE tenant_id = :tenant_id AND id = :id",
            {"now": now, "tenant_id": tenant_id, "id": credential_id},
        )
        return self.get_credential(tenant_id, credential_id) if result.rowcount > 0 else None

    def create_scheduled_task(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        now = utc_now()
        task = {
            "id": values.get("id") or str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "name": values["name"],
            "target": values["target"],
            "payload": json.dumps(values.get("payload") or {}, ensure_ascii=False),
            "trigger_kind": values["trigger_kind"],
            "status": values.get("status", "scheduled"),
            "next_run_at": values.get("next_run_at"),
            "interval_seconds": values.get("interval_seconds"),
            "last_run_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self._execute(
            "INSERT INTO scheduled_tasks(id, tenant_id, name, target, payload, "
            "trigger_kind, status, next_run_at, interval_seconds, last_run_at, "
            "created_at, updated_at) VALUES (:id, :tenant_id, :name, :target, "
            ":payload, :trigger_kind, :status, :next_run_at, :interval_seconds, "
            ":last_run_at, :created_at, :updated_at)",
            task,
        )
        return _decode_scheduled_task(task)

    def list_scheduled_tasks(self, tenant_id: str) -> list[dict]:
        rows = self._many(
            "SELECT * FROM scheduled_tasks WHERE tenant_id = :tenant_id ORDER BY created_at DESC",
            {"tenant_id": tenant_id},
        )
        return [_decode_scheduled_task(row) for row in rows]

    def get_scheduled_task(self, tenant_id: str, task_id: str) -> dict | None:
        row = self._one(
            "SELECT * FROM scheduled_tasks WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": task_id},
        )
        return _decode_scheduled_task(row) if row else None

    def list_active_scheduled_tasks(self) -> list[dict]:
        rows = self._many(
            "SELECT * FROM scheduled_tasks WHERE status = 'scheduled' ORDER BY next_run_at, id"
        )
        return [_decode_scheduled_task(row) for row in rows]

    def list_due_scheduled_tasks(self, due_at: str, limit: int = 100) -> list[dict]:
        rows = self._many(
            "SELECT * FROM scheduled_tasks WHERE status = 'scheduled' "
            "AND next_run_at IS NOT NULL AND next_run_at <= :due_at "
            "ORDER BY next_run_at, id LIMIT :limit",
            {"due_at": due_at, "limit": max(1, min(limit, 1000))},
        )
        return [_decode_scheduled_task(row) for row in rows]

    def set_scheduled_task_status(
        self,
        tenant_id: str,
        task_id: str,
        status: str,
        *,
        expected_status: str,
    ) -> dict | None:
        result = self._execute(
            "UPDATE scheduled_tasks SET status = :status, updated_at = :updated_at "
            "WHERE tenant_id = :tenant_id AND id = :id "
            "AND status = :expected_status",
            {
                "status": status,
                "updated_at": utc_now(),
                "tenant_id": tenant_id,
                "id": task_id,
                "expected_status": expected_status,
            },
        )
        return self.get_scheduled_task(tenant_id, task_id) if result.rowcount > 0 else None

    def advance_scheduled_task(
        self,
        tenant_id: str,
        task_id: str,
        *,
        expected_next_run_at: str,
        status: str,
        last_run_at: str,
        next_run_at: str | None,
    ) -> dict | None:
        result = self._execute(
            "UPDATE scheduled_tasks SET status = :status, last_run_at = :last_run_at, "
            "next_run_at = :next_run_at, updated_at = :updated_at "
            "WHERE tenant_id = :tenant_id AND id = :id AND status = 'scheduled' "
            "AND next_run_at = :expected_next_run_at",
            {
                "status": status,
                "last_run_at": last_run_at,
                "next_run_at": next_run_at,
                "updated_at": utc_now(),
                "tenant_id": tenant_id,
                "id": task_id,
                "expected_next_run_at": expected_next_run_at,
            },
        )
        return self.get_scheduled_task(tenant_id, task_id) if result.rowcount > 0 else None

    def write_audit(self, tenant_id: str, action: str, path: str, metadata: dict, user_id: str | None = None) -> None:
        from .redaction import redact

        self._execute(
            "INSERT INTO audit_logs(tenant_id, user_id, action, path, metadata, created_at) "
            "VALUES (:tenant_id, :user_id, :action, :path, :metadata, :created_at)",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "action": action,
                "path": path,
                "metadata": json.dumps(redact(metadata)),
                "created_at": utc_now(),
            },
        )

    def usage(self, tenant_id: str, model: str, prompt_tokens: int, completion_tokens: int, cost: float) -> None:
        self._execute(
            "INSERT INTO usage_records(tenant_id, model, prompt_tokens, completion_tokens, cost, created_at) "
            "VALUES (:tenant_id, :model, :prompt_tokens, :completion_tokens, :cost, :created_at)",
            {
                "tenant_id": tenant_id,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost": cost,
                "created_at": utc_now(),
            },
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
        *,
        capability_set_id: str = "runtime.none.v1",
        capability_policy_hash: str | None = None,
    ) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "session_id": session_id,
            "request_id": request_id,
            "status": "running",
            "requested_model": requested_model,
            "selected_model": selected_model,
            "capability_set_id": capability_set_id,
            "capability_policy_hash": capability_policy_hash,
            "error_code": None,
            "created_at": utc_now(),
            "completed_at": None,
        }
        self._execute(
            "INSERT INTO agent_runs(id, tenant_id, session_id, request_id, status, "
            "requested_model, selected_model, capability_set_id, capability_policy_hash, "
            "error_code, created_at, completed_at) "
            "VALUES (:id, :tenant_id, :session_id, :request_id, :status, :requested_model, "
            ":selected_model, :capability_set_id, :capability_policy_hash, :error_code, "
            ":created_at, :completed_at)",
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
        value["artifacts"] = self.list_artifacts(tenant_id, 200, run_id)
        value["citations"] = self.list_citations(tenant_id, 500, run_id=run_id)
        return value

    def get_run_by_request_id(
        self, tenant_id: str, request_id: str
    ) -> dict | None:
        row = self._one(
            "SELECT id FROM agent_runs WHERE tenant_id = :tenant_id "
            "AND request_id = :request_id ORDER BY created_at DESC LIMIT 1",
            {"tenant_id": tenant_id, "request_id": request_id},
        )
        return self.get_run(tenant_id, row["id"]) if row is not None else None

    def list_runs(self, tenant_id: str, limit: int = 50) -> list[dict]:
        return self._many(
            "SELECT * FROM agent_runs WHERE tenant_id = :tenant_id "
            "ORDER BY created_at DESC LIMIT :limit",
            {"tenant_id": tenant_id, "limit": max(1, min(limit, 200))},
        )

    def create_artifact(self, tenant_id: str, values: dict[str, Any]) -> dict:
        artifact_id = str(uuid.uuid4())
        value = {
            "id": artifact_id,
            "tenant_id": tenant_id,
            "series_id": values.get("series_id") or artifact_id,
            "version": int(values.get("version") or 1),
            "run_id": values.get("run_id"),
            "step_id": values.get("step_id"),
            "name": values["name"],
            "kind": values["kind"],
            "media_type": values["media_type"],
            "content_text": values.get("content_text") or "",
            "uri": values.get("uri"),
            "content_hash": values["content_hash"],
            "size_bytes": int(values.get("size_bytes") or 0),
            "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
            "created_at": utc_now(),
        }
        if value["run_id"] is not None and not self._one(
            "SELECT 1 FROM agent_runs WHERE tenant_id = :tenant_id AND id = :run_id",
            {"tenant_id": tenant_id, "run_id": value["run_id"]},
        ):
            raise KeyError(value["run_id"])
        if value["step_id"] is not None:
            owned_step = self._one(
                "SELECT run_id FROM run_steps WHERE tenant_id = :tenant_id AND id = :step_id",
                {"tenant_id": tenant_id, "step_id": value["step_id"]},
            )
            if owned_step is None or (
                value["run_id"] is not None
                and owned_step["run_id"] != value["run_id"]
            ):
                raise KeyError(value["step_id"])
        self._execute(
            "INSERT INTO artifacts(id, tenant_id, series_id, version, run_id, "
            "step_id, name, kind, media_type, content_text, uri, content_hash, "
            "size_bytes, metadata, created_at) VALUES (:id, :tenant_id, :series_id, "
            ":version, :run_id, :step_id, :name, :kind, :media_type, :content_text, "
            ":uri, :content_hash, :size_bytes, :metadata, :created_at)",
            value,
        )
        return _decode_artifact(value)

    def get_artifact(self, tenant_id: str, artifact_id: str) -> dict | None:
        row = self._one(
            "SELECT * FROM artifacts WHERE tenant_id = :tenant_id AND id = :artifact_id",
            {"tenant_id": tenant_id, "artifact_id": artifact_id},
        )
        return _decode_artifact(row) if row is not None else None

    def list_artifacts(
        self, tenant_id: str, limit: int = 50, run_id: str | None = None
    ) -> list[dict]:
        statement = "SELECT * FROM artifacts WHERE tenant_id = :tenant_id"
        values: dict[str, Any] = {
            "tenant_id": tenant_id,
            "limit": max(1, min(limit, 200)),
        }
        if run_id is not None:
            statement += " AND run_id = :run_id"
            values["run_id"] = run_id
        statement += " ORDER BY created_at DESC LIMIT :limit"
        return [_decode_artifact(row) for row in self._many(statement, values)]

    def create_citation(self, tenant_id: str, values: dict[str, Any]) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "run_id": values.get("run_id"),
            "step_id": values.get("step_id"),
            "artifact_id": values.get("artifact_id"),
            "source_kind": values["source_kind"],
            "source_id": values.get("source_id"),
            "source_uri": values.get("source_uri"),
            "title": values["title"],
            "locator": json.dumps(values.get("locator") or {}, ensure_ascii=False),
            "excerpt": values.get("excerpt") or "",
            "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
            "created_at": utc_now(),
        }
        if value["run_id"] is not None and not self._one(
            "SELECT 1 FROM agent_runs WHERE tenant_id = :tenant_id AND id = :run_id",
            {"tenant_id": tenant_id, "run_id": value["run_id"]},
        ):
            raise KeyError(value["run_id"])
        if value["step_id"] is not None:
            owned_step = self._one(
                "SELECT run_id FROM run_steps WHERE tenant_id = :tenant_id AND id = :step_id",
                {"tenant_id": tenant_id, "step_id": value["step_id"]},
            )
            if owned_step is None or (
                value["run_id"] is not None
                and owned_step["run_id"] != value["run_id"]
            ):
                raise KeyError(value["step_id"])
        if value["artifact_id"] is not None and not self._one(
            "SELECT 1 FROM artifacts WHERE tenant_id = :tenant_id AND id = :artifact_id",
            {"tenant_id": tenant_id, "artifact_id": value["artifact_id"]},
        ):
            raise KeyError(value["artifact_id"])
        self._execute(
            "INSERT INTO citations(id, tenant_id, run_id, step_id, artifact_id, "
            "source_kind, source_id, source_uri, title, locator, excerpt, metadata, "
            "created_at) VALUES (:id, :tenant_id, :run_id, :step_id, :artifact_id, "
            ":source_kind, :source_id, :source_uri, :title, :locator, :excerpt, "
            ":metadata, :created_at)",
            value,
        )
        return _decode_citation(value)

    def list_citations(
        self,
        tenant_id: str,
        limit: int = 100,
        run_id: str | None = None,
        artifact_id: str | None = None,
    ) -> list[dict]:
        statement = "SELECT * FROM citations WHERE tenant_id = :tenant_id"
        values: dict[str, Any] = {
            "tenant_id": tenant_id,
            "limit": max(1, min(limit, 500)),
        }
        if run_id is not None:
            statement += " AND run_id = :run_id"
            values["run_id"] = run_id
        if artifact_id is not None:
            statement += " AND artifact_id = :artifact_id"
            values["artifact_id"] = artifact_id
        statement += " ORDER BY created_at DESC LIMIT :limit"
        return [_decode_citation(row) for row in self._many(statement, values)]

    def create_evidence_protocol(self, tenant_id: str, values: dict[str, Any]) -> dict:
        now = utc_now()
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "profile": values["profile"],
            "name": values["name"],
            "version": int(values.get("version") or 1),
            "status": values["status"],
            "purpose": values["purpose"],
            "scope": values["scope"],
            "completion_predicate": values["completion_predicate"],
            "stop_conditions": json.dumps(values.get("stop_conditions") or [], ensure_ascii=False),
            "budget": json.dumps(values.get("budget") or {}, ensure_ascii=False),
            "source_uri": values.get("source_uri"),
            "content_hash": values["content_hash"],
            "created_at": now,
            "frozen_at": now if values["status"] == "frozen" else None,
        }
        self._execute(
            "INSERT INTO evidence_protocols(id, tenant_id, profile, name, version, "
            "status, purpose, scope, completion_predicate, stop_conditions, budget, "
            "source_uri, content_hash, created_at, frozen_at) VALUES "
            "(:id, :tenant_id, :profile, :name, :version, :status, :purpose, :scope, "
            ":completion_predicate, :stop_conditions, :budget, :source_uri, :content_hash, "
            ":created_at, :frozen_at)",
            value,
        )
        return _decode_protocol(value)

    def get_evidence_protocol(self, tenant_id: str, protocol_id: str) -> dict | None:
        row = self._one(
            "SELECT * FROM evidence_protocols WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": protocol_id},
        )
        if row is None:
            return None
        values = {"tenant_id": tenant_id, "protocol_id": protocol_id}
        claims = self._many(
            "SELECT * FROM evidence_claims WHERE tenant_id = :tenant_id "
            "AND protocol_id = :protocol_id ORDER BY created_at",
            values,
        )
        receipts = self._many(
            "SELECT * FROM execution_receipts WHERE tenant_id = :tenant_id "
            "AND protocol_id = :protocol_id ORDER BY created_at",
            values,
        )
        freezes = self._many(
            "SELECT * FROM freeze_manifests WHERE tenant_id = :tenant_id "
            "AND protocol_id = :protocol_id ORDER BY version",
            values,
        )
        decoded_receipts = [_decode_receipt(item) for item in receipts]
        artifact_ids: set[str] = set()
        for receipt in decoded_receipts:
            receipt["reviews"] = [
                _decode_review(item)
                for item in self._many(
                    "SELECT * FROM evidence_reviews WHERE tenant_id = :tenant_id "
                    "AND receipt_id = :receipt_id ORDER BY created_at",
                    {"tenant_id": tenant_id, "receipt_id": receipt["id"]},
                )
            ]
            artifact_ids.update(receipt["artifact_ids"])
        value = _decode_protocol(row)
        value["claims"] = [_decode_claim(item) for item in claims]
        value["receipts"] = decoded_receipts
        value["freezes"] = [_decode_freeze_manifest(item) for item in freezes]
        value["artifacts"] = [
            artifact
            for artifact_id in artifact_ids
            if (artifact := self.get_artifact(tenant_id, artifact_id)) is not None
        ]
        return value

    def get_evidence_protocol_by_hash(
        self, tenant_id: str, profile: str, content_hash: str
    ) -> dict | None:
        row = self._one(
            "SELECT id FROM evidence_protocols WHERE tenant_id = :tenant_id "
            "AND profile = :profile AND content_hash = :content_hash",
            {"tenant_id": tenant_id, "profile": profile, "content_hash": content_hash},
        )
        return self.get_evidence_protocol(tenant_id, row["id"]) if row else None

    def list_evidence_protocols(
        self, tenant_id: str, limit: int = 50, profile: str | None = None
    ) -> list[dict]:
        statement = "SELECT * FROM evidence_protocols WHERE tenant_id = :tenant_id"
        values: dict[str, Any] = {
            "tenant_id": tenant_id,
            "limit": max(1, min(limit, 200)),
        }
        if profile is not None:
            statement += " AND profile = :profile"
            values["profile"] = profile
        statement += " ORDER BY created_at DESC LIMIT :limit"
        return [_decode_protocol(row) for row in self._many(statement, values)]

    def create_evidence_claim(self, tenant_id: str, values: dict[str, Any]) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "protocol_id": values["protocol_id"],
            "run_id": values.get("run_id"),
            "statement": values["statement"],
            "resolution": values["resolution"],
            "scope": values["scope"],
            "evidence_refs": json.dumps(values.get("evidence_refs") or [], ensure_ascii=False),
            "prohibited_upgrades": json.dumps(
                values.get("prohibited_upgrades") or [], ensure_ascii=False
            ),
            "created_at": utc_now(),
        }
        if not self._one(
            "SELECT 1 FROM evidence_protocols WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": value["protocol_id"]},
        ):
            raise KeyError(value["protocol_id"])
        self._execute(
            "INSERT INTO evidence_claims(id, tenant_id, protocol_id, run_id, statement, "
            "resolution, scope, evidence_refs, prohibited_upgrades, created_at) VALUES "
            "(:id, :tenant_id, :protocol_id, :run_id, :statement, :resolution, :scope, "
            ":evidence_refs, :prohibited_upgrades, :created_at)",
            value,
        )
        return _decode_claim(value)

    def create_execution_receipt(self, tenant_id: str, values: dict[str, Any]) -> dict:
        artifact_ids = list(values.get("artifact_ids") or [])
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "protocol_id": values["protocol_id"],
            "run_id": values.get("run_id"),
            "status": values["status"],
            "input_digest": values["input_digest"],
            "output_digest": values["output_digest"],
            "runtime": json.dumps(values.get("runtime") or {}, ensure_ascii=False),
            "budget": json.dumps(values.get("budget") or {}, ensure_ascii=False),
            "artifact_ids": json.dumps(artifact_ids, ensure_ascii=False),
            "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
            "created_at": utc_now(),
        }
        if not self._one(
            "SELECT 1 FROM evidence_protocols WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": value["protocol_id"]},
        ):
            raise KeyError(value["protocol_id"])
        if value["run_id"] is not None and not self._one(
            "SELECT 1 FROM agent_runs WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": value["run_id"]},
        ):
            raise KeyError(value["run_id"])
        for artifact_id in set(artifact_ids):
            if not self._one(
                "SELECT 1 FROM artifacts WHERE tenant_id = :tenant_id AND id = :id",
                {"tenant_id": tenant_id, "id": artifact_id},
            ):
                raise KeyError("artifact_ids")
        self._execute(
            "INSERT INTO execution_receipts(id, tenant_id, protocol_id, run_id, status, "
            "input_digest, output_digest, runtime, budget, artifact_ids, metadata, "
            "created_at) VALUES (:id, :tenant_id, :protocol_id, :run_id, :status, "
            ":input_digest, :output_digest, :runtime, :budget, :artifact_ids, :metadata, "
            ":created_at)",
            value,
        )
        return _decode_receipt(value)

    def create_evidence_review(self, tenant_id: str, values: dict[str, Any]) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "receipt_id": values["receipt_id"],
            "reviewer_kind": values["reviewer_kind"],
            "reviewer_id": values.get("reviewer_id"),
            "independent": int(bool(values.get("independent"))),
            "status": values["status"],
            "finding": values["finding"],
            "evidence_refs": json.dumps(
                values.get("evidence_refs") or [], ensure_ascii=False
            ),
            "created_at": utc_now(),
        }
        if not self._one(
            "SELECT 1 FROM execution_receipts WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": value["receipt_id"]},
        ):
            raise KeyError(value["receipt_id"])
        self._execute(
            "INSERT INTO evidence_reviews(id, tenant_id, receipt_id, reviewer_kind, "
            "reviewer_id, independent, status, finding, evidence_refs, created_at) VALUES "
            "(:id, :tenant_id, :receipt_id, :reviewer_kind, :reviewer_id, :independent, "
            ":status, :finding, :evidence_refs, :created_at)",
            value,
        )
        return _decode_review(value)

    def create_freeze_manifest(self, tenant_id: str, values: dict[str, Any]) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "protocol_id": values["protocol_id"],
            "name": values["name"],
            "version": int(values.get("version") or 1),
            "members": json.dumps(values.get("members") or [], ensure_ascii=False),
            "content_hash": values["content_hash"],
            "created_at": utc_now(),
        }
        if not self._one(
            "SELECT 1 FROM evidence_protocols WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": value["protocol_id"]},
        ):
            raise KeyError(value["protocol_id"])
        self._execute(
            "INSERT INTO freeze_manifests(id, tenant_id, protocol_id, name, version, "
            "members, content_hash, created_at) VALUES (:id, :tenant_id, :protocol_id, "
            ":name, :version, :members, :content_hash, :created_at)",
            value,
        )
        return _decode_freeze_manifest(value)

    def create_decision_cases(
        self, tenant_id: str, protocol_id: str, receipt_id: str, values: list[dict[str, Any]]
    ) -> list[dict]:
        if not self._one(
            "SELECT 1 FROM evidence_protocols WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": protocol_id},
        ):
            raise KeyError(protocol_id)
        if not self._one(
            "SELECT 1 FROM execution_receipts WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": receipt_id},
        ):
            raise KeyError(receipt_id)
        created_at = utc_now()
        rows = [
            {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "protocol_id": protocol_id,
                "receipt_id": receipt_id,
                "source_case_id": item["source_case_id"],
                "family": item["family"],
                "state_text": item["state_text"],
                "questions": json.dumps(item["questions"], ensure_ascii=False),
                "answers": json.dumps(item["answers"], ensure_ascii=False),
                "gold_answers": json.dumps(item.get("gold_answers") or {}, ensure_ascii=False),
                "has_gold": int(bool(item.get("gold_answers"))),
                "resolution": item["resolution"],
                "confidence": float(item["confidence"]),
                "entropy": float(item["entropy"]),
                "threshold": float(item["threshold"]),
                "run_id": item.get("run_id"),
                "step_id": item.get("step_id"),
                "primary_artifact_id": item.get("primary_artifact_id"),
                "created_at": created_at,
            }
            for item in values
        ]
        from sqlalchemy import text

        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO decision_cases(id, tenant_id, protocol_id, receipt_id, "
                    "source_case_id, family, state_text, questions, answers, gold_answers, "
                    "has_gold, resolution, confidence, entropy, threshold, run_id, step_id, "
                    "primary_artifact_id, created_at) VALUES (:id, :tenant_id, :protocol_id, "
                    ":receipt_id, :source_case_id, :family, :state_text, :questions, :answers, "
                    ":gold_answers, :has_gold, :resolution, :confidence, :entropy, :threshold, "
                    ":run_id, :step_id, :primary_artifact_id, :created_at)"
                ),
                rows,
            )
        return [_decode_decision_case(row) for row in rows]

    def list_decision_cases(
        self,
        tenant_id: str,
        *,
        protocol_id: str | None = None,
        family: str | None = None,
        resolution: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        statement = "SELECT * FROM decision_cases WHERE tenant_id = :tenant_id"
        values: dict[str, Any] = {
            "tenant_id": tenant_id,
            "limit": max(1, min(limit, 1000)),
        }
        for column, value in (
            ("protocol_id", protocol_id),
            ("family", family),
            ("resolution", resolution),
        ):
            if value is not None:
                statement += f" AND {column} = :{column}"
                values[column] = value
        statement += " ORDER BY created_at DESC, source_case_id LIMIT :limit"
        return [_decode_decision_case(row) for row in self._many(statement, values)]

    def get_decision_case(self, tenant_id: str, case_id: str) -> dict | None:
        row = self._one(
            "SELECT * FROM decision_cases WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": case_id},
        )
        return _decode_decision_case(row) if row is not None else None

    def _search_candidates(self, tenant_id: str, limit: int) -> list[dict]:
        values = {"tenant_id": tenant_id, "candidate_limit": limit}
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
        artifacts = self._many(
            "SELECT id, 'artifact' kind, name title, "
            "CONCAT(content_text, CASE WHEN uri IS NULL THEN '' ELSE CONCAT('\n', uri) END) content, "
            "created_at, NULL session_id, run_id, step_id, id artifact_id, "
            "kind source_kind FROM artifacts WHERE tenant_id = :tenant_id "
            "ORDER BY created_at DESC LIMIT :candidate_limit",
            values,
        )
        citations = self._many(
            "SELECT id, 'citation' kind, title, "
            "CONCAT(excerpt, CASE WHEN source_uri IS NULL THEN '' ELSE CONCAT('\n', source_uri) END) content, "
            "created_at, NULL session_id, run_id, step_id, artifact_id, source_kind "
            "FROM citations WHERE tenant_id = :tenant_id "
            "ORDER BY created_at DESC LIMIT :candidate_limit",
            values,
        )
        decisions = self._many(
            "SELECT id, 'decision_case' kind, source_case_id title, "
            "CONCAT(state_text, '\n', questions, '\n', answers) content, created_at, "
            "NULL session_id, run_id, step_id, primary_artifact_id artifact_id, "
            "family source_kind FROM decision_cases WHERE tenant_id = :tenant_id "
            "ORDER BY created_at DESC LIMIT :candidate_limit",
            values,
        )
        research_claims = self._many(
            "SELECT id, 'research_claim' kind, claim_key title, "
            "CONCAT(statement, '\n', scope, '\n', claim_type, '\n', method_revision, "
            "'\n', lifecycle_status, '\n', promotion_stage, '\n', status_axes, '\n', "
            "closure_status, '\n', blockers) content, created_at, NULL session_id, "
            "NULL run_id, NULL step_id, "
            "NULL artifact_id, claim_type source_kind FROM research_claim_revisions "
            "WHERE tenant_id = :tenant_id ORDER BY created_at DESC LIMIT :candidate_limit",
            values,
        )
        imported_messages = self._many(
            "SELECT m.id, 'conversation_message' kind, c.title, m.content, "
            "COALESCE(m.normalized_created_at, m.created_at) created_at, "
            "NULL session_id, NULL run_id, NULL step_id, b.source_artifact_id artifact_id, "
            "m.importer_id source_kind, m.batch_id import_batch_id, "
            "m.conversation_id FROM conversation_import_messages m "
            "JOIN conversation_import_conversations c ON c.id = m.conversation_id "
            "AND c.tenant_id = m.tenant_id "
            "JOIN conversation_import_batches b ON b.id = m.batch_id "
            "AND b.tenant_id = m.tenant_id WHERE m.tenant_id = :tenant_id "
            "ORDER BY m.created_at DESC LIMIT :candidate_limit",
            values,
        )
        return [
            *messages,
            *documents,
            *steps,
            *artifacts,
            *citations,
            *decisions,
            *research_claims,
            *imported_messages,
        ]

    def search_backend(self) -> SearchBackend:
        from .adapters.search import LexicalSearchBackend

        return LexicalSearchBackend(self._search_candidates)

    def search_memory(self, tenant_id: str, query: str, limit: int = 20) -> list[dict]:
        return self.search_backend().search(tenant_id, query, limit)

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
