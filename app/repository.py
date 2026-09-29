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
from typing import TYPE_CHECKING, Any, Protocol

from .core.credentials import (
    encrypted_credential_id,
    validate_workspace_credential_map,
)

if TYPE_CHECKING:
    from .ports.search import SearchBackend


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Repository(Protocol):
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

    def create_research_registry(
        self,
        tenant_id: str,
        case_values: dict[str, Any],
        sources: list[dict[str, Any]],
        claims: list[dict[str, Any]],
    ) -> dict: ...

    def get_research_case(self, tenant_id: str, case_id: str) -> dict | None: ...

    def get_research_case_by_protocol(
        self, tenant_id: str, protocol_id: str
    ) -> dict | None: ...

    def list_research_cases(
        self, tenant_id: str, limit: int = 50, profile: str | None = None
    ) -> list[dict]: ...

    def list_research_claims(
        self,
        tenant_id: str,
        *,
        research_case_id: str,
        claim_type: str | None = None,
        closure_status: str | None = None,
        limit: int = 1_000,
    ) -> list[dict]: ...

    def get_research_claim(self, tenant_id: str, claim_id: str) -> dict | None: ...

    def create_research_claim_relation(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def list_research_claim_relations(
        self,
        tenant_id: str,
        *,
        research_case_id: str,
        claim_id: str | None = None,
    ) -> list[dict]: ...

    def withdraw_research_claim_relation(
        self,
        tenant_id: str,
        relation_id: str,
        *,
        reason: str,
        withdrawn_by: str | None,
    ) -> dict | None: ...

    def create_research_verification_attempt(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def list_research_verification_attempts(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]: ...

    def create_research_verification_plan(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def get_research_verification_plan(
        self, tenant_id: str, plan_id: str
    ) -> dict | None: ...

    def list_research_verification_plans(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]: ...

    def create_research_verification_execution(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def finish_research_verification_execution(
        self, tenant_id: str, execution_id: str, values: dict[str, Any]
    ) -> dict | None: ...

    def list_research_verification_executions(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]: ...

    def create_research_promotion_evaluation(
        self, tenant_id: str, values: dict[str, Any], *, promote: bool
    ) -> dict: ...

    def list_research_promotion_evaluations(self, tenant_id: str, claim_id: str) -> list[dict]: ...

    def create_qualification_evaluation(self, tenant_id: str, values: dict[str, Any]) -> dict: ...

    def list_qualification_evaluations(
        self, tenant_id: str, claim_id: str, profile_id: str | None = None
    ) -> list[dict]: ...

    def create_qualification_receipt(self, tenant_id: str, values: dict[str, Any]) -> dict: ...

    def get_qualification_receipt(self, tenant_id: str, receipt_id: str) -> dict | None: ...

    def list_qualification_receipts(
        self, tenant_id: str, claim_id: str | None = None
    ) -> list[dict]: ...

    def create_evidence_edges(
        self, tenant_id: str, evaluation_id: str, edges: list[dict[str, Any]]
    ) -> list[dict]: ...

    def upsert_current_use_binding(self, tenant_id: str, values: dict[str, Any]) -> dict: ...

    def get_current_use_binding(
        self,
        tenant_id: str,
        claim_id: str,
        profile_id: str,
        use_scope: str = "knowledge",
    ) -> dict | None: ...

    def list_current_use_bindings(
        self, tenant_id: str, profile_id: str, state: str = "current"
    ) -> list[dict]: ...

    def create_authorization_grant(self, tenant_id: str, values: dict[str, Any]) -> dict: ...

    def list_authorization_grants(
        self, tenant_id: str, qualification_receipt_ids: list[str] | None = None
    ) -> list[dict]: ...

    def search_backend(self) -> SearchBackend: ...

    def search_memory(self, tenant_id: str, query: str, limit: int = 20) -> list[dict]: ...


def _decode_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _decode_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        decoded = json.loads(value or "[]")
        return decoded if isinstance(decoded, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


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


def _decode_research_case(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["metadata"] = _decode_metadata(value.get("metadata"))
    return value


def _decode_research_source(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["metadata"] = _decode_metadata(value.get("metadata"))
    return value


def _decode_research_claim(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["revision_number"] = int(value.get("revision_number") or 1)
    value["status_axes"] = _decode_metadata(value.get("status_axes"))
    value["blockers"] = _decode_list(value.get("blockers"))
    value["definitions"] = _decode_list(value.get("definitions"))
    value["negative_boundaries"] = _decode_list(value.get("negative_boundaries"))
    value["dependency_claim_ids"] = _decode_list(value.get("dependency_claim_ids"))
    value.setdefault("sources", [])
    return value


def _decode_research_relation(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["evidence_refs"] = _decode_list(value.get("evidence_refs"))
    value["metadata"] = _decode_metadata(value.get("metadata"))
    return value


def _decode_verification_attempt(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["independent"] = bool(value.get("independent"))
    value["independence"] = _decode_metadata(value.get("independence"))
    value["verifier_lineage"] = _decode_metadata(value.get("verifier_lineage"))
    value["artifact_ids"] = _decode_list(value.get("artifact_ids"))
    value["metadata"] = _decode_metadata(value.get("metadata"))
    return value


def _decode_qualification_evaluation(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["profile_version"] = int(value.get("profile_version") or 1)
    for field in (
        "profile_snapshot",
        "evidence_closure",
        "evidence_vector",
        "independence_summary",
    ):
        value[field] = _decode_metadata(value.get(field))
    for field in ("criteria", "blockers"):
        value[field] = _decode_list(value.get(field))
    return value


def _decode_qualification_receipt(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["profile_version"] = int(value.get("profile_version") or 1)
    for field in ("evidence_vector", "independence_summary"):
        value[field] = _decode_metadata(value.get(field))
    for field in ("criteria", "blockers"):
        value[field] = _decode_list(value.get(field))
    return value


def _decode_authorization_grant(row: Any) -> dict[str, Any]:
    value = dict(row)
    for field in ("scope", "conditions", "budget"):
        value[field] = _decode_metadata(value.get(field))
    value["max_calls"] = int(value.get("max_calls") or 0)
    value["calls_used"] = int(value.get("calls_used") or 0)
    return value


def _decode_verification_plan(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["version"] = int(value.get("version") or 1)
    value["auto_promote"] = bool(value.get("auto_promote"))
    value["metadata"] = _decode_metadata(value.get("metadata"))
    return value


def _decode_verification_execution(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["plan_version"] = int(value.get("plan_version") or 1)
    value["input_snapshot"] = _decode_metadata(value.get("input_snapshot"))
    value["metadata"] = _decode_metadata(value.get("metadata"))
    return value


def _decode_promotion_evaluation(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["input_snapshot"] = _decode_metadata(value.get("input_snapshot"))
    for field in ("criteria", "blockers", "attempt_ids", "relation_ids"):
        value[field] = _decode_list(value.get(field))
    return value


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

    def create_research_registry(
        self,
        tenant_id: str,
        case_values: dict[str, Any],
        sources: list[dict[str, Any]],
        claims: list[dict[str, Any]],
    ) -> dict:
        created_at = utc_now()
        case = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "protocol_id": case_values["protocol_id"],
            "receipt_id": case_values["receipt_id"],
            "profile": case_values["profile"],
            "name": case_values["name"],
            "registry_id": case_values["registry_id"],
            "registry_version": case_values["registry_version"],
            "authority": case_values["authority"],
            "as_of_date": case_values.get("as_of_date"),
            "status": case_values["status"],
            "source_artifact_id": case_values["source_artifact_id"],
            "source_ledger_artifact_id": case_values.get("source_ledger_artifact_id"),
            "metadata": json.dumps(case_values.get("metadata") or {}, ensure_ascii=False),
            "created_at": created_at,
        }
        source_rows = []
        source_ids: dict[str, str] = {}
        for item in sources:
            source_id = str(uuid.uuid4())
            source_ids[item["ref_key"]] = source_id
            source_rows.append(
                {
                    "id": source_id,
                    "tenant_id": tenant_id,
                    "research_case_id": case["id"],
                    "source_key": item["source_key"],
                    "locator": item["locator"],
                    "content_hash": item["content_hash"],
                    "status": item["status"],
                    "metadata": json.dumps(item.get("metadata") or {}, ensure_ascii=False),
                    "created_at": created_at,
                }
            )
        claim_rows = []
        link_rows = []
        for item in claims:
            claim_id = str(uuid.uuid4())
            claim_rows.append(
                {
                    "id": claim_id,
                    "tenant_id": tenant_id,
                    "research_case_id": case["id"],
                    "claim_key": item["claim_key"],
                    "revision_number": int(item.get("revision_number") or 1),
                    "statement": item["statement"],
                    "claim_type": item["claim_type"],
                    "scope": item["scope"],
                    "method_revision": item["method_revision"],
                    "lifecycle_status": item["lifecycle_status"],
                    "status_axes": json.dumps(item.get("status_axes") or {}, ensure_ascii=False),
                    "closure_status": item["closure_status"],
                    "blockers": json.dumps(item.get("blockers") or [], ensure_ascii=False),
                    "semantic_hash": item.get("semantic_hash") or "",
                    "definitions": json.dumps(item.get("definitions") or [], ensure_ascii=False),
                    "negative_boundaries": json.dumps(
                        item.get("negative_boundaries") or [], ensure_ascii=False
                    ),
                    "dependency_claim_ids": json.dumps(
                        item.get("dependency_claim_ids") or [], ensure_ascii=False
                    ),
                    "parent_revision_id": item.get("parent_revision_id"),
                    "created_at": created_at,
                }
            )
            link_rows.extend(
                {
                    "tenant_id": tenant_id,
                    "claim_revision_id": claim_id,
                    "source_id": source_ids[ref_key],
                    "created_at": created_at,
                }
                for ref_key in item.get("source_ref_keys") or []
            )
        with self._connect() as db:
            if (
                db.execute(
                    "SELECT 1 FROM evidence_protocols WHERE tenant_id = ? AND id = ?",
                    (tenant_id, case["protocol_id"]),
                ).fetchone()
                is None
            ):
                raise KeyError(case["protocol_id"])
            if (
                db.execute(
                    "SELECT 1 FROM execution_receipts WHERE tenant_id = ? AND id = ?",
                    (tenant_id, case["receipt_id"]),
                ).fetchone()
                is None
            ):
                raise KeyError(case["receipt_id"])
            db.execute(
                "INSERT INTO research_cases(id, tenant_id, protocol_id, receipt_id, "
                "profile, name, registry_id, registry_version, authority, as_of_date, "
                "status, source_artifact_id, source_ledger_artifact_id, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(case.values()),
            )
            db.executemany(
                "INSERT INTO research_sources(id, tenant_id, research_case_id, source_key, "
                "locator, content_hash, status, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [tuple(row.values()) for row in source_rows],
            )
            db.executemany(
                "INSERT INTO research_claim_revisions(id, tenant_id, research_case_id, "
                "claim_key, revision_number, statement, claim_type, scope, method_revision, "
                "lifecycle_status, status_axes, closure_status, blockers, semantic_hash, "
                "definitions, negative_boundaries, dependency_claim_ids, "
                "parent_revision_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [tuple(row.values()) for row in claim_rows],
            )
            db.executemany(
                "INSERT INTO research_claim_sources(tenant_id, claim_revision_id, "
                "source_id, created_at) VALUES (?, ?, ?, ?)",
                [tuple(row.values()) for row in link_rows],
            )
        return _decode_research_case(case)

    def get_research_case(self, tenant_id: str, case_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM research_cases WHERE tenant_id = ? AND id = ?",
                (tenant_id, case_id),
            ).fetchone()
        return _decode_research_case(row) if row is not None else None

    def get_research_case_by_protocol(
        self, tenant_id: str, protocol_id: str
    ) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM research_cases WHERE tenant_id = ? AND protocol_id = ?",
                (tenant_id, protocol_id),
            ).fetchone()
        return _decode_research_case(row) if row is not None else None

    def list_research_cases(
        self, tenant_id: str, limit: int = 50, profile: str | None = None
    ) -> list[dict]:
        sql = "SELECT * FROM research_cases WHERE tenant_id = ?"
        parameters: list[Any] = [tenant_id]
        if profile is not None:
            sql += " AND profile = ?"
            parameters.append(profile)
        sql += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(max(1, min(limit, 200)))
        with self._connect() as db:
            rows = db.execute(sql, parameters).fetchall()
        return [_decode_research_case(row) for row in rows]

    def list_research_claims(
        self,
        tenant_id: str,
        *,
        research_case_id: str,
        claim_type: str | None = None,
        closure_status: str | None = None,
        limit: int = 1_000,
    ) -> list[dict]:
        sql = "SELECT * FROM research_claim_revisions WHERE tenant_id = ? AND research_case_id = ?"
        parameters: list[Any] = [tenant_id, research_case_id]
        for column, value in (("claim_type", claim_type), ("closure_status", closure_status)):
            if value is not None:
                sql += f" AND {column} = ?"
                parameters.append(value)
        sql += " ORDER BY claim_key, revision_number DESC LIMIT ?"
        parameters.append(max(1, min(limit, 2_000)))
        with self._connect() as db:
            rows = db.execute(sql, parameters).fetchall()
            source_rows = db.execute(
                "SELECT l.claim_revision_id, s.* FROM research_claim_sources l "
                "JOIN research_sources s ON s.id = l.source_id AND s.tenant_id = l.tenant_id "
                "JOIN research_claim_revisions c ON c.id = l.claim_revision_id "
                "AND c.tenant_id = l.tenant_id WHERE c.tenant_id = ? "
                "AND c.research_case_id = ? ORDER BY s.source_key, s.locator",
                (tenant_id, research_case_id),
            ).fetchall()
        sources_by_claim: dict[str, list[dict]] = {}
        for row in source_rows:
            value = dict(row)
            claim_id = value.pop("claim_revision_id")
            sources_by_claim.setdefault(claim_id, []).append(_decode_research_source(value))
        values = [_decode_research_claim(row) for row in rows]
        for value in values:
            value["sources"] = sources_by_claim.get(value["id"], [])
        return values

    def get_research_claim(self, tenant_id: str, claim_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM research_claim_revisions WHERE tenant_id = ? AND id = ?",
                (tenant_id, claim_id),
            ).fetchone()
            if row is None:
                return None
            source_rows = db.execute(
                "SELECT s.* FROM research_claim_sources l JOIN research_sources s "
                "ON s.id = l.source_id AND s.tenant_id = l.tenant_id "
                "WHERE l.tenant_id = ? AND l.claim_revision_id = ? "
                "ORDER BY s.source_key, s.locator",
                (tenant_id, claim_id),
            ).fetchall()
        value = _decode_research_claim(row)
        value["sources"] = [_decode_research_source(item) for item in source_rows]
        return value

    def create_research_claim_relation(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "research_case_id": values["research_case_id"],
            "source_claim_id": values["source_claim_id"],
            "target_claim_id": values["target_claim_id"],
            "relation_type": values["relation_type"],
            "status": values.get("status") or "active",
            "rationale": values["rationale"],
            "evidence_refs": json.dumps(
                values.get("evidence_refs") or [], ensure_ascii=False
            ),
            "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
            "created_by": values.get("created_by"),
            "withdrawal_reason": None,
            "withdrawn_by": None,
            "withdrawn_at": None,
            "created_at": utc_now(),
        }
        with self._connect() as db:
            claims = db.execute(
                "SELECT id FROM research_claim_revisions WHERE tenant_id = ? "
                "AND research_case_id = ? AND id IN (?, ?)",
                (
                    tenant_id,
                    value["research_case_id"],
                    value["source_claim_id"],
                    value["target_claim_id"],
                ),
            ).fetchall()
            if len(claims) != 2:
                raise KeyError("claim_relation_endpoints")
            db.execute(
                "INSERT INTO research_claim_relations(id, tenant_id, research_case_id, "
                "source_claim_id, target_claim_id, relation_type, status, rationale, "
                "evidence_refs, metadata, created_by, withdrawal_reason, withdrawn_by, "
                "withdrawn_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return _decode_research_relation(value)

    def list_research_claim_relations(
        self,
        tenant_id: str,
        *,
        research_case_id: str,
        claim_id: str | None = None,
    ) -> list[dict]:
        sql = (
            "SELECT r.*, source.claim_key source_claim_key, "
            "target.claim_key target_claim_key FROM research_claim_relations r "
            "JOIN research_claim_revisions source ON source.id = r.source_claim_id "
            "AND source.tenant_id = r.tenant_id "
            "JOIN research_claim_revisions target ON target.id = r.target_claim_id "
            "AND target.tenant_id = r.tenant_id "
            "WHERE r.tenant_id = ? AND r.research_case_id = ?"
        )
        parameters: list[Any] = [tenant_id, research_case_id]
        if claim_id is not None:
            sql += " AND (r.source_claim_id = ? OR r.target_claim_id = ?)"
            parameters.extend((claim_id, claim_id))
        sql += " ORDER BY r.created_at, r.id"
        with self._connect() as db:
            rows = db.execute(sql, parameters).fetchall()
        return [_decode_research_relation(row) for row in rows]

    def withdraw_research_claim_relation(
        self,
        tenant_id: str,
        relation_id: str,
        *,
        reason: str,
        withdrawn_by: str | None,
    ) -> dict | None:
        with self._connect() as db:
            db.execute(
                "UPDATE research_claim_relations SET status = 'withdrawn', "
                "withdrawal_reason = ?, withdrawn_by = ?, withdrawn_at = ? "
                "WHERE tenant_id = ? AND id = ? AND status = 'active'",
                (reason, withdrawn_by, utc_now(), tenant_id, relation_id),
            )
            row = db.execute(
                "SELECT r.*, source.claim_key source_claim_key, "
                "target.claim_key target_claim_key FROM research_claim_relations r "
                "JOIN research_claim_revisions source ON source.id = r.source_claim_id "
                "AND source.tenant_id = r.tenant_id "
                "JOIN research_claim_revisions target ON target.id = r.target_claim_id "
                "AND target.tenant_id = r.tenant_id "
                "WHERE r.tenant_id = ? AND r.id = ?",
                (tenant_id, relation_id),
            ).fetchone()
        return _decode_research_relation(row) if row is not None else None

    def create_research_verification_attempt(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "research_case_id": values["research_case_id"],
            "claim_revision_id": values["claim_revision_id"],
            "receipt_id": values["receipt_id"],
            "run_id": values.get("run_id"),
            "plan_id": values.get("plan_id"),
            "verification_execution_id": values.get("verification_execution_id"),
            "kind": values["kind"],
            "outcome": values["outcome"],
            "method": values["method"],
            "scope": values["scope"],
            "independent": int(bool(values.get("independent"))),
            "independence": json.dumps(values.get("independence") or {}, ensure_ascii=False),
            "validation_modality": values.get("validation_modality") or "agent_review",
            "verifier_lineage": json.dumps(
                values.get("verifier_lineage") or {}, ensure_ascii=False
            ),
            "input_digest": values["input_digest"],
            "output_digest": values["output_digest"],
            "artifact_ids": json.dumps(
                values.get("artifact_ids") or [], ensure_ascii=False
            ),
            "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
            "created_by": values.get("created_by"),
            "created_at": utc_now(),
        }
        with self._connect() as db:
            if (
                db.execute(
                    "SELECT 1 FROM research_claim_revisions WHERE tenant_id = ? AND id = ? "
                    "AND research_case_id = ?",
                    (
                        tenant_id,
                        value["claim_revision_id"],
                        value["research_case_id"],
                    ),
                ).fetchone()
                is None
            ):
                raise KeyError(value["claim_revision_id"])
            if (
                db.execute(
                    "SELECT 1 FROM execution_receipts WHERE tenant_id = ? AND id = ?",
                    (tenant_id, value["receipt_id"]),
                ).fetchone()
                is None
            ):
                raise KeyError(value["receipt_id"])
            db.execute(
                "INSERT INTO research_verification_attempts(id, tenant_id, "
                "research_case_id, claim_revision_id, receipt_id, run_id, plan_id, "
                "verification_execution_id, kind, outcome, method, scope, independent, "
                "independence, validation_modality, verifier_lineage, input_digest, "
                "output_digest, artifact_ids, metadata, created_by, created_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return _decode_verification_attempt(value)

    def list_research_verification_attempts(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM research_verification_attempts WHERE tenant_id = ? "
                "AND claim_revision_id = ? ORDER BY created_at, id",
                (tenant_id, claim_id),
            ).fetchall()
        return [_decode_verification_attempt(row) for row in rows]

    def create_research_verification_plan(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            claim = db.execute(
                "SELECT research_case_id FROM research_claim_revisions "
                "WHERE tenant_id = ? AND id = ?",
                (tenant_id, values["claim_revision_id"]),
            ).fetchone()
            if claim is None or claim["research_case_id"] != values["research_case_id"]:
                raise KeyError(values["claim_revision_id"])
            row = db.execute(
                "SELECT COALESCE(MAX(version), 0) version "
                "FROM research_verification_plans WHERE tenant_id = ? "
                "AND claim_revision_id = ? AND plan_key = ?",
                (tenant_id, values["claim_revision_id"], values["plan_key"]),
            ).fetchone()
            version = int(row["version"]) + 1
            db.execute(
                "UPDATE research_verification_plans SET status = 'retired' "
                "WHERE tenant_id = ? AND claim_revision_id = ? AND plan_key = ? "
                "AND status = 'active'",
                (tenant_id, values["claim_revision_id"], values["plan_key"]),
            )
            value = {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "research_case_id": values["research_case_id"],
                "claim_revision_id": values["claim_revision_id"],
                "plan_key": values["plan_key"],
                "version": version,
                "status": values["status"],
                "executor": values["executor"],
                "name": values["name"],
                "kind": values["kind"],
                "method": values["method"],
                "scope": values["scope"],
                "prompt": values["prompt"],
                "system_prompt": values["system_prompt"],
                "model": values.get("model"),
                "result_contract_version": values["result_contract_version"],
                "auto_promote": int(bool(values.get("auto_promote"))),
                "content_digest": values["content_digest"],
                "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
                "created_by": values.get("created_by"),
                "created_at": utc_now(),
            }
            db.execute(
                "INSERT INTO research_verification_plans(id, tenant_id, research_case_id, "
                "claim_revision_id, plan_key, version, status, executor, name, kind, method, "
                "scope, prompt, system_prompt, model, result_contract_version, auto_promote, "
                "content_digest, metadata, created_by, created_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return _decode_verification_plan(value)

    def get_research_verification_plan(
        self, tenant_id: str, plan_id: str
    ) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM research_verification_plans WHERE tenant_id = ? AND id = ?",
                (tenant_id, plan_id),
            ).fetchone()
        return _decode_verification_plan(row) if row is not None else None

    def list_research_verification_plans(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM research_verification_plans WHERE tenant_id = ? "
                "AND claim_revision_id = ? ORDER BY plan_key, version DESC",
                (tenant_id, claim_id),
            ).fetchall()
        return [_decode_verification_plan(row) for row in rows]

    def create_research_verification_execution(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        value = {
            "id": values["id"],
            "tenant_id": tenant_id,
            "research_case_id": values["research_case_id"],
            "claim_revision_id": values["claim_revision_id"],
            "plan_id": values["plan_id"],
            "plan_version": values["plan_version"],
            "status": values["status"],
            "request_id": values["request_id"],
            "run_id": None,
            "scheduled_task_id": values.get("scheduled_task_id"),
            "attempt_id": None,
            "artifact_id": None,
            "promotion_evaluation_id": None,
            "outcome": None,
            "input_digest": values["input_digest"],
            "input_snapshot": json.dumps(values.get("input_snapshot") or {}, ensure_ascii=False),
            "output_digest": None,
            "error_code": None,
            "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
            "created_by": values.get("created_by"),
            "started_at": utc_now(),
            "completed_at": None,
        }
        with self._connect() as db:
            plan = db.execute(
                "SELECT claim_revision_id, research_case_id FROM research_verification_plans "
                "WHERE tenant_id = ? AND id = ?",
                (tenant_id, value["plan_id"]),
            ).fetchone()
            if (
                plan is None
                or plan["claim_revision_id"] != value["claim_revision_id"]
                or plan["research_case_id"] != value["research_case_id"]
            ):
                raise KeyError(value["plan_id"])
            db.execute(
                "INSERT INTO research_verification_executions(id, tenant_id, "
                "research_case_id, claim_revision_id, plan_id, plan_version, status, "
                "request_id, run_id, scheduled_task_id, attempt_id, artifact_id, "
                "promotion_evaluation_id, outcome, input_digest, input_snapshot, output_digest, "
                "error_code, metadata, created_by, started_at, completed_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return _decode_verification_execution(value)

    def finish_research_verification_execution(
        self, tenant_id: str, execution_id: str, values: dict[str, Any]
    ) -> dict | None:
        safe_metadata = json.dumps(values.get("metadata") or {}, ensure_ascii=False)
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE research_verification_executions SET status = ?, run_id = ?, "
                "attempt_id = ?, artifact_id = ?, promotion_evaluation_id = ?, "
                "outcome = ?, output_digest = ?, error_code = ?, metadata = ?, "
                "completed_at = ? WHERE tenant_id = ? AND id = ? AND status = 'running'",
                (
                    values["status"],
                    values.get("run_id"),
                    values.get("attempt_id"),
                    values.get("artifact_id"),
                    values.get("promotion_evaluation_id"),
                    values.get("outcome"),
                    values.get("output_digest"),
                    values.get("error_code"),
                    safe_metadata,
                    utc_now(),
                    tenant_id,
                    execution_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = db.execute(
                "SELECT * FROM research_verification_executions WHERE tenant_id = ? AND id = ?",
                (tenant_id, execution_id),
            ).fetchone()
        return _decode_verification_execution(row) if row is not None else None

    def list_research_verification_executions(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM research_verification_executions WHERE tenant_id = ? "
                "AND claim_revision_id = ? ORDER BY started_at DESC, id DESC",
                (tenant_id, claim_id),
            ).fetchall()
        return [_decode_verification_execution(row) for row in rows]

    def create_research_promotion_evaluation(
        self, tenant_id: str, values: dict[str, Any], *, promote: bool
    ) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "research_case_id": values["research_case_id"],
            "claim_revision_id": values["claim_revision_id"],
            "from_stage": values["from_stage"],
            "target_stage": values["target_stage"],
            "decision": values["decision"],
            "policy_version": values["policy_version"],
            "input_digest": values["input_digest"],
            "evaluation_digest": values["evaluation_digest"],
            "criteria": json.dumps(values.get("criteria") or [], ensure_ascii=False),
            "input_snapshot": json.dumps(values.get("input_snapshot") or {}, ensure_ascii=False),
            "blockers": json.dumps(values.get("blockers") or [], ensure_ascii=False),
            "attempt_ids": json.dumps(values.get("attempt_ids") or [], ensure_ascii=False),
            "relation_ids": json.dumps(
                values.get("relation_ids") or [], ensure_ascii=False
            ),
            "created_by": values.get("created_by"),
            "created_at": utc_now(),
        }
        with self._connect() as db:
            claim = db.execute(
                "SELECT promotion_stage FROM research_claim_revisions "
                "WHERE tenant_id = ? AND id = ? AND research_case_id = ?",
                (
                    tenant_id,
                    value["claim_revision_id"],
                    value["research_case_id"],
                ),
            ).fetchone()
            if claim is None or claim["promotion_stage"] != value["from_stage"]:
                raise KeyError("promotion_stage_changed")
            db.execute(
                "INSERT INTO research_promotion_evaluations(id, tenant_id, "
                "research_case_id, claim_revision_id, from_stage, target_stage, decision, "
                "policy_version, input_digest, evaluation_digest, criteria, input_snapshot, "
                "blockers, attempt_ids, relation_ids, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
            if promote:
                db.execute(
                    "UPDATE research_claim_revisions SET promotion_stage = ? "
                    "WHERE tenant_id = ? AND id = ? AND promotion_stage = ?",
                    (
                        value["target_stage"],
                        tenant_id,
                        value["claim_revision_id"],
                        value["from_stage"],
                    ),
                )
        return _decode_promotion_evaluation(value)

    def list_research_promotion_evaluations(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM research_promotion_evaluations WHERE tenant_id = ? "
                "AND claim_revision_id = ? ORDER BY created_at, id",
                (tenant_id, claim_id),
            ).fetchall()
        return [_decode_promotion_evaluation(row) for row in rows]

    def create_qualification_evaluation(self, tenant_id: str, values: dict[str, Any]) -> dict:
        value = {
            "id": values.get("id") or str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "claim_revision_id": values["claim_revision_id"],
            "claim_semantic_hash": values["claim_semantic_hash"],
            "profile_id": values["profile_id"],
            "profile_version": int(values["profile_version"]),
            "profile_hash": values["profile_hash"],
            "profile_snapshot": json.dumps(values["profile_snapshot"], ensure_ascii=False),
            "evidence_closure_hash": values["evidence_closure_hash"],
            "evidence_closure": json.dumps(values["evidence_closure"], ensure_ascii=False),
            "policy_version": values["policy_version"],
            "policy_hash": values["policy_hash"],
            "verdict": values["verdict"],
            "criteria": json.dumps(values.get("criteria") or [], ensure_ascii=False),
            "blockers": json.dumps(values.get("blockers") or [], ensure_ascii=False),
            "evidence_vector": json.dumps(values.get("evidence_vector") or {}, ensure_ascii=False),
            "independence_summary": json.dumps(
                values.get("independence_summary") or {}, ensure_ascii=False
            ),
            "evaluated_by": values.get("evaluated_by"),
            "created_at": utc_now(),
        }
        with self._connect() as db:
            if (
                db.execute(
                    "SELECT 1 FROM research_claim_revisions WHERE tenant_id = ? AND id = ?",
                    (tenant_id, value["claim_revision_id"]),
                ).fetchone()
                is None
            ):
                raise KeyError(value["claim_revision_id"])
            db.execute(
                "INSERT INTO qualification_evaluations(id, tenant_id, claim_revision_id, "
                "claim_semantic_hash, profile_id, profile_version, profile_hash, "
                "profile_snapshot, evidence_closure_hash, evidence_closure, policy_version, "
                "policy_hash, verdict, criteria, blockers, evidence_vector, "
                "independence_summary, evaluated_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return _decode_qualification_evaluation(value)

    def list_qualification_evaluations(
        self, tenant_id: str, claim_id: str, profile_id: str | None = None
    ) -> list[dict]:
        sql = (
            "SELECT * FROM qualification_evaluations WHERE tenant_id = ? AND claim_revision_id = ?"
        )
        parameters: list[Any] = [tenant_id, claim_id]
        if profile_id is not None:
            sql += " AND profile_id = ?"
            parameters.append(profile_id)
        sql += " ORDER BY created_at DESC, id DESC"
        with self._connect() as db:
            rows = db.execute(sql, parameters).fetchall()
        return [_decode_qualification_evaluation(row) for row in rows]

    def create_qualification_receipt(self, tenant_id: str, values: dict[str, Any]) -> dict:
        value = {
            "id": values.get("id") or str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "evaluation_id": values["evaluation_id"],
            "claim_revision_id": values["claim_revision_id"],
            "claim_semantic_hash": values["claim_semantic_hash"],
            "profile_id": values["profile_id"],
            "profile_version": int(values["profile_version"]),
            "evidence_closure_hash": values["evidence_closure_hash"],
            "policy_version": values["policy_version"],
            "policy_hash": values["policy_hash"],
            "verdict": values["verdict"],
            "criteria": json.dumps(values.get("criteria") or [], ensure_ascii=False),
            "blockers": json.dumps(values.get("blockers") or [], ensure_ascii=False),
            "evidence_vector": json.dumps(values.get("evidence_vector") or {}, ensure_ascii=False),
            "independence_summary": json.dumps(
                values.get("independence_summary") or {}, ensure_ascii=False
            ),
            "receipt_hash": values["receipt_hash"],
            "issued_at": values.get("issued_at") or utc_now(),
        }
        with self._connect() as db:
            db.execute(
                "INSERT INTO qualification_receipts(id, tenant_id, evaluation_id, "
                "claim_revision_id, claim_semantic_hash, profile_id, profile_version, "
                "evidence_closure_hash, policy_version, policy_hash, verdict, criteria, "
                "blockers, evidence_vector, independence_summary, receipt_hash, issued_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return _decode_qualification_receipt(value)

    def get_qualification_receipt(self, tenant_id: str, receipt_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM qualification_receipts WHERE tenant_id = ? AND id = ?",
                (tenant_id, receipt_id),
            ).fetchone()
        return _decode_qualification_receipt(row) if row is not None else None

    def list_qualification_receipts(
        self, tenant_id: str, claim_id: str | None = None
    ) -> list[dict]:
        sql = "SELECT * FROM qualification_receipts WHERE tenant_id = ?"
        parameters: list[Any] = [tenant_id]
        if claim_id is not None:
            sql += " AND claim_revision_id = ?"
            parameters.append(claim_id)
        sql += " ORDER BY issued_at, id"
        with self._connect() as db:
            rows = db.execute(sql, parameters).fetchall()
        return [_decode_qualification_receipt(row) for row in rows]

    def create_evidence_edges(
        self, tenant_id: str, evaluation_id: str, edges: list[dict[str, Any]]
    ) -> list[dict]:
        created_at = utc_now()
        rows = [
            {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "evaluation_id": evaluation_id,
                "source_node_type": item["source_node_type"],
                "source_node_id": item["source_node_id"],
                "target_node_type": item["target_node_type"],
                "target_node_id": item["target_node_id"],
                "edge_type": item["edge_type"],
                "source_hash": item.get("source_hash"),
                "target_hash": item.get("target_hash"),
                "status": item.get("status") or "active",
                "created_at": created_at,
            }
            for item in edges
        ]
        if not rows:
            return []
        with self._connect() as db:
            db.executemany(
                "INSERT INTO evidence_edges(id, tenant_id, evaluation_id, "
                "source_node_type, source_node_id, target_node_type, target_node_id, "
                "edge_type, source_hash, target_hash, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [tuple(row.values()) for row in rows],
            )
        return rows

    def upsert_current_use_binding(self, tenant_id: str, values: dict[str, Any]) -> dict:
        now = utc_now()
        value = {
            "id": values.get("id") or str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "claim_revision_id": values["claim_revision_id"],
            "profile_id": values["profile_id"],
            "use_scope": values.get("use_scope") or "knowledge",
            "qualification_receipt_id": values["qualification_receipt_id"],
            "state": values.get("state") or "current",
            "stale_reason": values.get("stale_reason"),
            "bound_by": values.get("bound_by"),
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as db:
            db.execute(
                "INSERT INTO current_use_bindings(id, tenant_id, claim_revision_id, "
                "profile_id, use_scope, qualification_receipt_id, state, stale_reason, "
                "bound_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, claim_revision_id, profile_id, use_scope) DO UPDATE "
                "SET qualification_receipt_id = excluded.qualification_receipt_id, "
                "state = excluded.state, stale_reason = excluded.stale_reason, "
                "bound_by = excluded.bound_by, updated_at = excluded.updated_at",
                tuple(value.values()),
            )
            row = db.execute(
                "SELECT * FROM current_use_bindings WHERE tenant_id = ? "
                "AND claim_revision_id = ? AND profile_id = ? AND use_scope = ?",
                (tenant_id, value["claim_revision_id"], value["profile_id"], value["use_scope"]),
            ).fetchone()
        return dict(row)

    def get_current_use_binding(
        self,
        tenant_id: str,
        claim_id: str,
        profile_id: str,
        use_scope: str = "knowledge",
    ) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM current_use_bindings WHERE tenant_id = ? "
                "AND claim_revision_id = ? AND profile_id = ? AND use_scope = ?",
                (tenant_id, claim_id, profile_id, use_scope),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_current_use_bindings(
        self, tenant_id: str, profile_id: str, state: str = "current"
    ) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM current_use_bindings WHERE tenant_id = ? "
                "AND profile_id = ? AND state = ? ORDER BY updated_at DESC",
                (tenant_id, profile_id, state),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_authorization_grant(self, tenant_id: str, values: dict[str, Any]) -> dict:
        value = {
            "id": values.get("id") or str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "qualification_receipt_id": values["qualification_receipt_id"],
            "actor_id": values["actor_id"],
            "action": values["action"],
            "target": values["target"],
            "scope": json.dumps(values.get("scope") or {}, ensure_ascii=False),
            "conditions": json.dumps(values.get("conditions") or {}, ensure_ascii=False),
            "expires_at": values.get("expires_at"),
            "budget": json.dumps(values.get("budget") or {}, ensure_ascii=False),
            "max_calls": int(values.get("max_calls") or 1),
            "calls_used": 0,
            "policy_version": values["policy_version"],
            "state": values.get("state") or "active",
            "grant_receipt": values["grant_receipt"],
            "created_by": values.get("created_by"),
            "created_at": utc_now(),
        }
        with self._connect() as db:
            if (
                db.execute(
                    "SELECT 1 FROM qualification_receipts WHERE tenant_id = ? AND id = ?",
                    (tenant_id, value["qualification_receipt_id"]),
                ).fetchone()
                is None
            ):
                raise KeyError(value["qualification_receipt_id"])
            db.execute(
                "INSERT INTO authorization_grants(id, tenant_id, qualification_receipt_id, "
                "actor_id, action, target, scope, conditions, expires_at, budget, max_calls, "
                "calls_used, policy_version, state, grant_receipt, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(value.values()),
            )
        return _decode_authorization_grant(value)

    def list_authorization_grants(
        self, tenant_id: str, qualification_receipt_ids: list[str] | None = None
    ) -> list[dict]:
        sql = "SELECT * FROM authorization_grants WHERE tenant_id = ?"
        parameters: list[Any] = [tenant_id]
        if qualification_receipt_ids is not None:
            if not qualification_receipt_ids:
                return []
            placeholders = ",".join("?" for _ in qualification_receipt_ids)
            sql += f" AND qualification_receipt_id IN ({placeholders})"
            parameters.extend(qualification_receipt_ids)
        sql += " ORDER BY created_at, id"
        with self._connect() as db:
            rows = db.execute(sql, parameters).fetchall()
        return [_decode_authorization_grant(row) for row in rows]

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

    def create_research_registry(
        self,
        tenant_id: str,
        case_values: dict[str, Any],
        sources: list[dict[str, Any]],
        claims: list[dict[str, Any]],
    ) -> dict:
        created_at = utc_now()
        case = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "protocol_id": case_values["protocol_id"],
            "receipt_id": case_values["receipt_id"],
            "profile": case_values["profile"],
            "name": case_values["name"],
            "registry_id": case_values["registry_id"],
            "registry_version": case_values["registry_version"],
            "authority": case_values["authority"],
            "as_of_date": case_values.get("as_of_date"),
            "status": case_values["status"],
            "source_artifact_id": case_values["source_artifact_id"],
            "source_ledger_artifact_id": case_values.get("source_ledger_artifact_id"),
            "metadata": json.dumps(case_values.get("metadata") or {}, ensure_ascii=False),
            "created_at": created_at,
        }
        source_ids: dict[str, str] = {}
        source_rows = []
        for item in sources:
            source_id = str(uuid.uuid4())
            source_ids[item["ref_key"]] = source_id
            source_rows.append(
                {
                    "id": source_id,
                    "tenant_id": tenant_id,
                    "research_case_id": case["id"],
                    "source_key": item["source_key"],
                    "locator": item["locator"],
                    "content_hash": item["content_hash"],
                    "status": item["status"],
                    "metadata": json.dumps(item.get("metadata") or {}, ensure_ascii=False),
                    "created_at": created_at,
                }
            )
        claim_rows = []
        link_rows = []
        for item in claims:
            claim_id = str(uuid.uuid4())
            claim_rows.append(
                {
                    "id": claim_id,
                    "tenant_id": tenant_id,
                    "research_case_id": case["id"],
                    "claim_key": item["claim_key"],
                    "revision_number": int(item.get("revision_number") or 1),
                    "statement": item["statement"],
                    "claim_type": item["claim_type"],
                    "scope": item["scope"],
                    "method_revision": item["method_revision"],
                    "lifecycle_status": item["lifecycle_status"],
                    "status_axes": json.dumps(item.get("status_axes") or {}, ensure_ascii=False),
                    "closure_status": item["closure_status"],
                    "blockers": json.dumps(item.get("blockers") or [], ensure_ascii=False),
                    "semantic_hash": item.get("semantic_hash") or "",
                    "definitions": json.dumps(item.get("definitions") or [], ensure_ascii=False),
                    "negative_boundaries": json.dumps(
                        item.get("negative_boundaries") or [], ensure_ascii=False
                    ),
                    "dependency_claim_ids": json.dumps(
                        item.get("dependency_claim_ids") or [], ensure_ascii=False
                    ),
                    "parent_revision_id": item.get("parent_revision_id"),
                    "created_at": created_at,
                }
            )
            link_rows.extend(
                {
                    "tenant_id": tenant_id,
                    "claim_revision_id": claim_id,
                    "source_id": source_ids[ref_key],
                    "created_at": created_at,
                }
                for ref_key in item.get("source_ref_keys") or []
            )
        from sqlalchemy import text

        with self.engine.begin() as connection:
            protocol = connection.execute(
                text("SELECT 1 FROM evidence_protocols WHERE tenant_id = :tenant_id AND id = :id"),
                {"tenant_id": tenant_id, "id": case["protocol_id"]},
            ).first()
            receipt = connection.execute(
                text("SELECT 1 FROM execution_receipts WHERE tenant_id = :tenant_id AND id = :id"),
                {"tenant_id": tenant_id, "id": case["receipt_id"]},
            ).first()
            if protocol is None:
                raise KeyError(case["protocol_id"])
            if receipt is None:
                raise KeyError(case["receipt_id"])
            connection.execute(
                text(
                    "INSERT INTO research_cases(id, tenant_id, protocol_id, receipt_id, "
                    "profile, name, registry_id, registry_version, authority, as_of_date, "
                    "status, source_artifact_id, source_ledger_artifact_id, metadata, "
                    "created_at) VALUES (:id, :tenant_id, :protocol_id, :receipt_id, "
                    ":profile, :name, :registry_id, :registry_version, :authority, "
                    ":as_of_date, :status, :source_artifact_id, "
                    ":source_ledger_artifact_id, :metadata, :created_at)"
                ),
                case,
            )
            if source_rows:
                connection.execute(
                    text(
                        "INSERT INTO research_sources(id, tenant_id, research_case_id, "
                        "source_key, locator, content_hash, status, metadata, created_at) "
                        "VALUES (:id, :tenant_id, :research_case_id, :source_key, :locator, "
                        ":content_hash, :status, :metadata, :created_at)"
                    ),
                    source_rows,
                )
            connection.execute(
                text(
                    "INSERT INTO research_claim_revisions(id, tenant_id, research_case_id, "
                    "claim_key, revision_number, statement, claim_type, scope, "
                    "method_revision, lifecycle_status, status_axes, closure_status, "
                    "blockers, semantic_hash, definitions, negative_boundaries, "
                    "dependency_claim_ids, parent_revision_id, created_at) VALUES (:id, :tenant_id, "
                    ":research_case_id, "
                    ":claim_key, :revision_number, :statement, :claim_type, :scope, "
                    ":method_revision, :lifecycle_status, :status_axes, :closure_status, "
                    ":blockers, :semantic_hash, :definitions, :negative_boundaries, "
                    ":dependency_claim_ids, :parent_revision_id, :created_at)"
                ),
                claim_rows,
            )
            if link_rows:
                connection.execute(
                    text(
                        "INSERT INTO research_claim_sources(tenant_id, claim_revision_id, "
                        "source_id, created_at) VALUES (:tenant_id, :claim_revision_id, "
                        ":source_id, :created_at)"
                    ),
                    link_rows,
                )
        return _decode_research_case(case)

    def get_research_case(self, tenant_id: str, case_id: str) -> dict | None:
        row = self._one(
            "SELECT * FROM research_cases WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": case_id},
        )
        return _decode_research_case(row) if row is not None else None

    def get_research_case_by_protocol(
        self, tenant_id: str, protocol_id: str
    ) -> dict | None:
        row = self._one(
            "SELECT * FROM research_cases "
            "WHERE tenant_id = :tenant_id AND protocol_id = :protocol_id",
            {"tenant_id": tenant_id, "protocol_id": protocol_id},
        )
        return _decode_research_case(row) if row is not None else None

    def list_research_cases(
        self, tenant_id: str, limit: int = 50, profile: str | None = None
    ) -> list[dict]:
        statement = "SELECT * FROM research_cases WHERE tenant_id = :tenant_id"
        values: dict[str, Any] = {
            "tenant_id": tenant_id,
            "limit": max(1, min(limit, 200)),
        }
        if profile is not None:
            statement += " AND profile = :profile"
            values["profile"] = profile
        statement += " ORDER BY created_at DESC LIMIT :limit"
        return [_decode_research_case(row) for row in self._many(statement, values)]

    def list_research_claims(
        self,
        tenant_id: str,
        *,
        research_case_id: str,
        claim_type: str | None = None,
        closure_status: str | None = None,
        limit: int = 1_000,
    ) -> list[dict]:
        statement = (
            "SELECT * FROM research_claim_revisions WHERE tenant_id = :tenant_id "
            "AND research_case_id = :research_case_id"
        )
        values: dict[str, Any] = {
            "tenant_id": tenant_id,
            "research_case_id": research_case_id,
            "limit": max(1, min(limit, 2_000)),
        }
        for column, value in (("claim_type", claim_type), ("closure_status", closure_status)):
            if value is not None:
                statement += f" AND {column} = :{column}"
                values[column] = value
        statement += " ORDER BY claim_key, revision_number DESC LIMIT :limit"
        claims = [_decode_research_claim(row) for row in self._many(statement, values)]
        source_rows = self._many(
            "SELECT l.claim_revision_id, s.* FROM research_claim_sources l "
            "JOIN research_sources s ON s.id = l.source_id AND s.tenant_id = l.tenant_id "
            "JOIN research_claim_revisions c ON c.id = l.claim_revision_id "
            "AND c.tenant_id = l.tenant_id WHERE c.tenant_id = :tenant_id "
            "AND c.research_case_id = :research_case_id ORDER BY s.source_key, s.locator",
            values,
        )
        sources_by_claim: dict[str, list[dict]] = {}
        for row in source_rows:
            claim_id = row.pop("claim_revision_id")
            sources_by_claim.setdefault(claim_id, []).append(_decode_research_source(row))
        for claim in claims:
            claim["sources"] = sources_by_claim.get(claim["id"], [])
        return claims

    def get_research_claim(self, tenant_id: str, claim_id: str) -> dict | None:
        row = self._one(
            "SELECT * FROM research_claim_revisions WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": claim_id},
        )
        if row is None:
            return None
        value = _decode_research_claim(row)
        source_rows = self._many(
            "SELECT s.* FROM research_claim_sources l JOIN research_sources s "
            "ON s.id = l.source_id AND s.tenant_id = l.tenant_id "
            "WHERE l.tenant_id = :tenant_id AND l.claim_revision_id = :id "
            "ORDER BY s.source_key, s.locator",
            {"tenant_id": tenant_id, "id": claim_id},
        )
        value["sources"] = [_decode_research_source(item) for item in source_rows]
        return value

    def create_research_claim_relation(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "research_case_id": values["research_case_id"],
            "source_claim_id": values["source_claim_id"],
            "target_claim_id": values["target_claim_id"],
            "relation_type": values["relation_type"],
            "status": values.get("status") or "active",
            "rationale": values["rationale"],
            "evidence_refs": json.dumps(values.get("evidence_refs") or [], ensure_ascii=False),
            "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
            "created_by": values.get("created_by"),
            "withdrawal_reason": None,
            "withdrawn_by": None,
            "withdrawn_at": None,
            "created_at": utc_now(),
        }
        endpoints = self._many(
            "SELECT id FROM research_claim_revisions WHERE tenant_id = :tenant_id "
            "AND research_case_id = :research_case_id "
            "AND id IN (:source_claim_id, :target_claim_id)",
            value,
        )
        if len(endpoints) != 2:
            raise KeyError("claim_relation_endpoints")
        self._execute(
            "INSERT INTO research_claim_relations(id, tenant_id, research_case_id, "
            "source_claim_id, target_claim_id, relation_type, status, rationale, "
            "evidence_refs, metadata, created_by, withdrawal_reason, withdrawn_by, "
            "withdrawn_at, created_at) VALUES (:id, :tenant_id, "
            ":research_case_id, :source_claim_id, :target_claim_id, :relation_type, "
            ":status, :rationale, :evidence_refs, :metadata, :created_by, "
            ":withdrawal_reason, :withdrawn_by, :withdrawn_at, :created_at)",
            value,
        )
        return _decode_research_relation(value)

    def list_research_claim_relations(
        self,
        tenant_id: str,
        *,
        research_case_id: str,
        claim_id: str | None = None,
    ) -> list[dict]:
        statement = (
            "SELECT r.*, source.claim_key source_claim_key, "
            "target.claim_key target_claim_key FROM research_claim_relations r "
            "JOIN research_claim_revisions source ON source.id = r.source_claim_id "
            "AND source.tenant_id = r.tenant_id "
            "JOIN research_claim_revisions target ON target.id = r.target_claim_id "
            "AND target.tenant_id = r.tenant_id "
            "WHERE r.tenant_id = :tenant_id "
            "AND r.research_case_id = :research_case_id"
        )
        parameters: dict[str, Any] = {
            "tenant_id": tenant_id,
            "research_case_id": research_case_id,
        }
        if claim_id is not None:
            statement += " AND (r.source_claim_id = :claim_id OR r.target_claim_id = :claim_id)"
            parameters["claim_id"] = claim_id
        statement += " ORDER BY r.created_at, r.id"
        return [
            _decode_research_relation(row) for row in self._many(statement, parameters)
        ]

    def withdraw_research_claim_relation(
        self,
        tenant_id: str,
        relation_id: str,
        *,
        reason: str,
        withdrawn_by: str | None,
    ) -> dict | None:
        values = {
            "tenant_id": tenant_id,
            "relation_id": relation_id,
            "reason": reason,
            "withdrawn_by": withdrawn_by,
            "withdrawn_at": utc_now(),
        }
        self._execute(
            "UPDATE research_claim_relations SET status = 'withdrawn', "
            "withdrawal_reason = :reason, withdrawn_by = :withdrawn_by, "
            "withdrawn_at = :withdrawn_at WHERE tenant_id = :tenant_id "
            "AND id = :relation_id AND status = 'active'",
            values,
        )
        row = self._one(
            "SELECT r.*, source.claim_key source_claim_key, "
            "target.claim_key target_claim_key FROM research_claim_relations r "
            "JOIN research_claim_revisions source ON source.id = r.source_claim_id "
            "AND source.tenant_id = r.tenant_id "
            "JOIN research_claim_revisions target ON target.id = r.target_claim_id "
            "AND target.tenant_id = r.tenant_id "
            "WHERE r.tenant_id = :tenant_id AND r.id = :relation_id",
            values,
        )
        return _decode_research_relation(row) if row is not None else None

    def create_research_verification_attempt(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "research_case_id": values["research_case_id"],
            "claim_revision_id": values["claim_revision_id"],
            "receipt_id": values["receipt_id"],
            "run_id": values.get("run_id"),
            "plan_id": values.get("plan_id"),
            "verification_execution_id": values.get("verification_execution_id"),
            "kind": values["kind"],
            "outcome": values["outcome"],
            "method": values["method"],
            "scope": values["scope"],
            "independent": int(bool(values.get("independent"))),
            "independence": json.dumps(values.get("independence") or {}, ensure_ascii=False),
            "validation_modality": values.get("validation_modality") or "agent_review",
            "verifier_lineage": json.dumps(
                values.get("verifier_lineage") or {}, ensure_ascii=False
            ),
            "input_digest": values["input_digest"],
            "output_digest": values["output_digest"],
            "artifact_ids": json.dumps(
                values.get("artifact_ids") or [], ensure_ascii=False
            ),
            "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
            "created_by": values.get("created_by"),
            "created_at": utc_now(),
        }
        if not self._one(
            "SELECT 1 FROM research_claim_revisions WHERE tenant_id = :tenant_id "
            "AND id = :claim_revision_id AND research_case_id = :research_case_id",
            value,
        ):
            raise KeyError(value["claim_revision_id"])
        if not self._one(
            "SELECT 1 FROM execution_receipts WHERE tenant_id = :tenant_id AND id = :receipt_id",
            value,
        ):
            raise KeyError(value["receipt_id"])
        self._execute(
            "INSERT INTO research_verification_attempts(id, tenant_id, research_case_id, "
            "claim_revision_id, receipt_id, run_id, plan_id, verification_execution_id, "
            "kind, outcome, method, scope, independent, independence, validation_modality, "
            "verifier_lineage, input_digest, output_digest, artifact_ids, metadata, "
            "created_by, created_at) VALUES (:id, :tenant_id, "
            ":research_case_id, :claim_revision_id, :receipt_id, :run_id, :plan_id, "
            ":verification_execution_id, :kind, :outcome, :method, :scope, :independent, "
            ":independence, :validation_modality, :verifier_lineage, :input_digest, "
            ":output_digest, :artifact_ids, :metadata, :created_by, :created_at)",
            value,
        )
        return _decode_verification_attempt(value)

    def list_research_verification_attempts(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]:
        return [
            _decode_verification_attempt(row)
            for row in self._many(
                "SELECT * FROM research_verification_attempts "
                "WHERE tenant_id = :tenant_id AND claim_revision_id = :claim_id "
                "ORDER BY created_at, id",
                {"tenant_id": tenant_id, "claim_id": claim_id},
            )
        ]

    def create_research_verification_plan(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        from sqlalchemy import text

        with self.engine.begin() as connection:
            claim = (
                connection.execute(
                    text(
                        "SELECT research_case_id FROM research_claim_revisions "
                        "WHERE tenant_id = :tenant_id AND id = :claim_revision_id FOR UPDATE"
                    ),
                    {"tenant_id": tenant_id, **values},
                )
                .mappings()
                .first()
            )
            if claim is None or claim["research_case_id"] != values["research_case_id"]:
                raise KeyError(values["claim_revision_id"])
            versions = (
                connection.execute(
                    text(
                        "SELECT version FROM research_verification_plans "
                        "WHERE tenant_id = :tenant_id AND claim_revision_id = :claim_revision_id "
                        "AND plan_key = :plan_key FOR UPDATE"
                    ),
                    {"tenant_id": tenant_id, **values},
                )
                .scalars()
                .all()
            )
            version = max((int(item) for item in versions), default=0) + 1
            connection.execute(
                text(
                    "UPDATE research_verification_plans SET status = 'retired' "
                    "WHERE tenant_id = :tenant_id AND claim_revision_id = :claim_revision_id "
                    "AND plan_key = :plan_key AND status = 'active'"
                ),
                {"tenant_id": tenant_id, **values},
            )
            value = {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "research_case_id": values["research_case_id"],
                "claim_revision_id": values["claim_revision_id"],
                "plan_key": values["plan_key"],
                "version": version,
                "status": values["status"],
                "executor": values["executor"],
                "name": values["name"],
                "kind": values["kind"],
                "method": values["method"],
                "scope": values["scope"],
                "prompt": values["prompt"],
                "system_prompt": values["system_prompt"],
                "model": values.get("model"),
                "result_contract_version": values["result_contract_version"],
                "auto_promote": int(bool(values.get("auto_promote"))),
                "content_digest": values["content_digest"],
                "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
                "created_by": values.get("created_by"),
                "created_at": utc_now(),
            }
            connection.execute(
                text(
                    "INSERT INTO research_verification_plans(id, tenant_id, "
                    "research_case_id, claim_revision_id, plan_key, version, status, "
                    "executor, name, kind, method, scope, prompt, system_prompt, model, "
                    "result_contract_version, auto_promote, content_digest, metadata, "
                    "created_by, created_at) VALUES (:id, :tenant_id, :research_case_id, "
                    ":claim_revision_id, :plan_key, :version, :status, :executor, :name, "
                    ":kind, :method, :scope, :prompt, :system_prompt, :model, "
                    ":result_contract_version, :auto_promote, :content_digest, :metadata, "
                    ":created_by, :created_at)"
                ),
                value,
            )
        return _decode_verification_plan(value)

    def get_research_verification_plan(
        self, tenant_id: str, plan_id: str
    ) -> dict | None:
        row = self._one(
            "SELECT * FROM research_verification_plans WHERE tenant_id = :tenant_id "
            "AND id = :plan_id",
            {"tenant_id": tenant_id, "plan_id": plan_id},
        )
        return _decode_verification_plan(row) if row is not None else None

    def list_research_verification_plans(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]:
        return [
            _decode_verification_plan(row)
            for row in self._many(
                "SELECT * FROM research_verification_plans WHERE tenant_id = :tenant_id "
                "AND claim_revision_id = :claim_id ORDER BY plan_key, version DESC",
                {"tenant_id": tenant_id, "claim_id": claim_id},
            )
        ]

    def create_research_verification_execution(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        value = {
            "id": values["id"],
            "tenant_id": tenant_id,
            "research_case_id": values["research_case_id"],
            "claim_revision_id": values["claim_revision_id"],
            "plan_id": values["plan_id"],
            "plan_version": values["plan_version"],
            "status": values["status"],
            "request_id": values["request_id"],
            "run_id": None,
            "scheduled_task_id": values.get("scheduled_task_id"),
            "attempt_id": None,
            "artifact_id": None,
            "promotion_evaluation_id": None,
            "outcome": None,
            "input_digest": values["input_digest"],
            "input_snapshot": json.dumps(values.get("input_snapshot") or {}, ensure_ascii=False),
            "output_digest": None,
            "error_code": None,
            "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
            "created_by": values.get("created_by"),
            "started_at": utc_now(),
            "completed_at": None,
        }
        if not self._one(
            "SELECT 1 FROM research_verification_plans WHERE tenant_id = :tenant_id "
            "AND id = :plan_id AND claim_revision_id = :claim_revision_id "
            "AND research_case_id = :research_case_id",
            value,
        ):
            raise KeyError(value["plan_id"])
        self._execute(
            "INSERT INTO research_verification_executions(id, tenant_id, research_case_id, "
            "claim_revision_id, plan_id, plan_version, status, request_id, run_id, "
            "scheduled_task_id, attempt_id, artifact_id, promotion_evaluation_id, outcome, "
            "input_digest, input_snapshot, output_digest, error_code, metadata, created_by, started_at, "
            "completed_at) VALUES (:id, :tenant_id, :research_case_id, :claim_revision_id, "
            ":plan_id, :plan_version, :status, :request_id, :run_id, :scheduled_task_id, "
            ":attempt_id, :artifact_id, :promotion_evaluation_id, :outcome, :input_digest, "
            ":input_snapshot, :output_digest, :error_code, :metadata, :created_by, :started_at, "
            ":completed_at)",
            value,
        )
        return _decode_verification_execution(value)

    def finish_research_verification_execution(
        self, tenant_id: str, execution_id: str, values: dict[str, Any]
    ) -> dict | None:
        parameters = {
            "tenant_id": tenant_id,
            "execution_id": execution_id,
            "status": values["status"],
            "run_id": values.get("run_id"),
            "attempt_id": values.get("attempt_id"),
            "artifact_id": values.get("artifact_id"),
            "promotion_evaluation_id": values.get("promotion_evaluation_id"),
            "outcome": values.get("outcome"),
            "output_digest": values.get("output_digest"),
            "error_code": values.get("error_code"),
            "metadata": json.dumps(values.get("metadata") or {}, ensure_ascii=False),
            "completed_at": utc_now(),
        }
        result = self._execute(
            "UPDATE research_verification_executions SET status = :status, run_id = :run_id, "
            "attempt_id = :attempt_id, artifact_id = :artifact_id, "
            "promotion_evaluation_id = :promotion_evaluation_id, outcome = :outcome, "
            "output_digest = :output_digest, error_code = :error_code, metadata = :metadata, "
            "completed_at = :completed_at WHERE tenant_id = :tenant_id AND id = :execution_id "
            "AND status = 'running'",
            parameters,
        )
        if result.rowcount == 0:
            return None
        row = self._one(
            "SELECT * FROM research_verification_executions WHERE tenant_id = :tenant_id "
            "AND id = :execution_id",
            parameters,
        )
        return _decode_verification_execution(row) if row is not None else None

    def list_research_verification_executions(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]:
        return [
            _decode_verification_execution(row)
            for row in self._many(
                "SELECT * FROM research_verification_executions WHERE tenant_id = :tenant_id "
                "AND claim_revision_id = :claim_id ORDER BY started_at DESC, id DESC",
                {"tenant_id": tenant_id, "claim_id": claim_id},
            )
        ]

    def create_research_promotion_evaluation(
        self, tenant_id: str, values: dict[str, Any], *, promote: bool
    ) -> dict:
        value = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "research_case_id": values["research_case_id"],
            "claim_revision_id": values["claim_revision_id"],
            "from_stage": values["from_stage"],
            "target_stage": values["target_stage"],
            "decision": values["decision"],
            "policy_version": values["policy_version"],
            "input_digest": values["input_digest"],
            "evaluation_digest": values["evaluation_digest"],
            "criteria": json.dumps(values.get("criteria") or [], ensure_ascii=False),
            "input_snapshot": json.dumps(values.get("input_snapshot") or {}, ensure_ascii=False),
            "blockers": json.dumps(values.get("blockers") or [], ensure_ascii=False),
            "attempt_ids": json.dumps(values.get("attempt_ids") or [], ensure_ascii=False),
            "relation_ids": json.dumps(
                values.get("relation_ids") or [], ensure_ascii=False
            ),
            "created_by": values.get("created_by"),
            "created_at": utc_now(),
        }
        from sqlalchemy import text

        with self.engine.begin() as connection:
            claim = (
                connection.execute(
                    text(
                        "SELECT promotion_stage FROM research_claim_revisions "
                        "WHERE tenant_id = :tenant_id AND id = :claim_revision_id "
                        "AND research_case_id = :research_case_id FOR UPDATE"
                    ),
                    value,
                )
                .mappings()
                .first()
            )
            if claim is None or claim["promotion_stage"] != value["from_stage"]:
                raise KeyError("promotion_stage_changed")
            connection.execute(
                text(
                    "INSERT INTO research_promotion_evaluations(id, tenant_id, "
                    "research_case_id, claim_revision_id, from_stage, target_stage, "
                    "decision, policy_version, input_digest, evaluation_digest, criteria, "
                    "input_snapshot, blockers, attempt_ids, relation_ids, created_by, created_at) VALUES "
                    "(:id, :tenant_id, :research_case_id, :claim_revision_id, :from_stage, "
                    ":target_stage, :decision, :policy_version, :input_digest, "
                    ":evaluation_digest, :criteria, :input_snapshot, :blockers, :attempt_ids, "
                    ":relation_ids, :created_by, :created_at)"
                ),
                value,
            )
            if promote:
                connection.execute(
                    text(
                        "UPDATE research_claim_revisions SET promotion_stage = :target_stage "
                        "WHERE tenant_id = :tenant_id AND id = :claim_revision_id "
                        "AND promotion_stage = :from_stage"
                    ),
                    value,
                )
        return _decode_promotion_evaluation(value)

    def list_research_promotion_evaluations(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]:
        return [
            _decode_promotion_evaluation(row)
            for row in self._many(
                "SELECT * FROM research_promotion_evaluations "
                "WHERE tenant_id = :tenant_id AND claim_revision_id = :claim_id "
                "ORDER BY created_at, id",
                {"tenant_id": tenant_id, "claim_id": claim_id},
            )
        ]

    def create_qualification_evaluation(self, tenant_id: str, values: dict[str, Any]) -> dict:
        value = {
            "id": values.get("id") or str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "claim_revision_id": values["claim_revision_id"],
            "claim_semantic_hash": values["claim_semantic_hash"],
            "profile_id": values["profile_id"],
            "profile_version": int(values["profile_version"]),
            "profile_hash": values["profile_hash"],
            "profile_snapshot": json.dumps(values["profile_snapshot"], ensure_ascii=False),
            "evidence_closure_hash": values["evidence_closure_hash"],
            "evidence_closure": json.dumps(values["evidence_closure"], ensure_ascii=False),
            "policy_version": values["policy_version"],
            "policy_hash": values["policy_hash"],
            "verdict": values["verdict"],
            "criteria": json.dumps(values.get("criteria") or [], ensure_ascii=False),
            "blockers": json.dumps(values.get("blockers") or [], ensure_ascii=False),
            "evidence_vector": json.dumps(values.get("evidence_vector") or {}, ensure_ascii=False),
            "independence_summary": json.dumps(
                values.get("independence_summary") or {}, ensure_ascii=False
            ),
            "evaluated_by": values.get("evaluated_by"),
            "created_at": utc_now(),
        }
        if not self._one(
            "SELECT 1 FROM research_claim_revisions WHERE tenant_id = :tenant_id "
            "AND id = :claim_revision_id",
            value,
        ):
            raise KeyError(value["claim_revision_id"])
        self._execute(
            "INSERT INTO qualification_evaluations(id, tenant_id, claim_revision_id, "
            "claim_semantic_hash, profile_id, profile_version, profile_hash, "
            "profile_snapshot, evidence_closure_hash, evidence_closure, policy_version, "
            "policy_hash, verdict, criteria, blockers, evidence_vector, "
            "independence_summary, evaluated_by, created_at) VALUES (:id, :tenant_id, "
            ":claim_revision_id, :claim_semantic_hash, :profile_id, :profile_version, "
            ":profile_hash, :profile_snapshot, :evidence_closure_hash, :evidence_closure, "
            ":policy_version, :policy_hash, :verdict, :criteria, :blockers, "
            ":evidence_vector, :independence_summary, :evaluated_by, :created_at)",
            value,
        )
        return _decode_qualification_evaluation(value)

    def list_qualification_evaluations(
        self, tenant_id: str, claim_id: str, profile_id: str | None = None
    ) -> list[dict]:
        statement = (
            "SELECT * FROM qualification_evaluations WHERE tenant_id = :tenant_id "
            "AND claim_revision_id = :claim_id"
        )
        parameters = {"tenant_id": tenant_id, "claim_id": claim_id}
        if profile_id is not None:
            statement += " AND profile_id = :profile_id"
            parameters["profile_id"] = profile_id
        statement += " ORDER BY created_at DESC, id DESC"
        return [_decode_qualification_evaluation(row) for row in self._many(statement, parameters)]

    def create_qualification_receipt(self, tenant_id: str, values: dict[str, Any]) -> dict:
        value = {
            "id": values.get("id") or str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "evaluation_id": values["evaluation_id"],
            "claim_revision_id": values["claim_revision_id"],
            "claim_semantic_hash": values["claim_semantic_hash"],
            "profile_id": values["profile_id"],
            "profile_version": int(values["profile_version"]),
            "evidence_closure_hash": values["evidence_closure_hash"],
            "policy_version": values["policy_version"],
            "policy_hash": values["policy_hash"],
            "verdict": values["verdict"],
            "criteria": json.dumps(values.get("criteria") or [], ensure_ascii=False),
            "blockers": json.dumps(values.get("blockers") or [], ensure_ascii=False),
            "evidence_vector": json.dumps(values.get("evidence_vector") or {}, ensure_ascii=False),
            "independence_summary": json.dumps(
                values.get("independence_summary") or {}, ensure_ascii=False
            ),
            "receipt_hash": values["receipt_hash"],
            "issued_at": values.get("issued_at") or utc_now(),
        }
        self._execute(
            "INSERT INTO qualification_receipts(id, tenant_id, evaluation_id, "
            "claim_revision_id, claim_semantic_hash, profile_id, profile_version, "
            "evidence_closure_hash, policy_version, policy_hash, verdict, criteria, "
            "blockers, evidence_vector, independence_summary, receipt_hash, issued_at) "
            "VALUES (:id, :tenant_id, :evaluation_id, :claim_revision_id, "
            ":claim_semantic_hash, :profile_id, :profile_version, :evidence_closure_hash, "
            ":policy_version, :policy_hash, :verdict, :criteria, :blockers, "
            ":evidence_vector, :independence_summary, :receipt_hash, :issued_at)",
            value,
        )
        return _decode_qualification_receipt(value)

    def get_qualification_receipt(self, tenant_id: str, receipt_id: str) -> dict | None:
        row = self._one(
            "SELECT * FROM qualification_receipts WHERE tenant_id = :tenant_id "
            "AND id = :receipt_id",
            {"tenant_id": tenant_id, "receipt_id": receipt_id},
        )
        return _decode_qualification_receipt(row) if row is not None else None

    def list_qualification_receipts(
        self, tenant_id: str, claim_id: str | None = None
    ) -> list[dict]:
        statement = "SELECT * FROM qualification_receipts WHERE tenant_id = :tenant_id"
        parameters: dict[str, Any] = {"tenant_id": tenant_id}
        if claim_id is not None:
            statement += " AND claim_revision_id = :claim_id"
            parameters["claim_id"] = claim_id
        statement += " ORDER BY issued_at, id"
        return [
            _decode_qualification_receipt(row)
            for row in self._many(statement, parameters)
        ]

    def create_evidence_edges(
        self, tenant_id: str, evaluation_id: str, edges: list[dict[str, Any]]
    ) -> list[dict]:
        rows = [
            {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "evaluation_id": evaluation_id,
                "source_node_type": item["source_node_type"],
                "source_node_id": item["source_node_id"],
                "target_node_type": item["target_node_type"],
                "target_node_id": item["target_node_id"],
                "edge_type": item["edge_type"],
                "source_hash": item.get("source_hash"),
                "target_hash": item.get("target_hash"),
                "status": item.get("status") or "active",
                "created_at": utc_now(),
            }
            for item in edges
        ]
        for row in rows:
            self._execute(
                "INSERT INTO evidence_edges(id, tenant_id, evaluation_id, source_node_type, "
                "source_node_id, target_node_type, target_node_id, edge_type, source_hash, "
                "target_hash, status, created_at) VALUES (:id, :tenant_id, :evaluation_id, "
                ":source_node_type, :source_node_id, :target_node_type, :target_node_id, "
                ":edge_type, :source_hash, :target_hash, :status, :created_at)",
                row,
            )
        return rows

    def upsert_current_use_binding(self, tenant_id: str, values: dict[str, Any]) -> dict:
        now = utc_now()
        value = {
            "id": values.get("id") or str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "claim_revision_id": values["claim_revision_id"],
            "profile_id": values["profile_id"],
            "use_scope": values.get("use_scope") or "knowledge",
            "qualification_receipt_id": values["qualification_receipt_id"],
            "state": values.get("state") or "current",
            "stale_reason": values.get("stale_reason"),
            "bound_by": values.get("bound_by"),
            "created_at": now,
            "updated_at": now,
        }
        self._execute(
            "INSERT INTO current_use_bindings(id, tenant_id, claim_revision_id, profile_id, "
            "use_scope, qualification_receipt_id, state, stale_reason, bound_by, created_at, "
            "updated_at) VALUES (:id, :tenant_id, :claim_revision_id, :profile_id, "
            ":use_scope, :qualification_receipt_id, :state, :stale_reason, :bound_by, "
            ":created_at, :updated_at) ON CONFLICT(tenant_id, claim_revision_id, "
            "profile_id, use_scope) DO UPDATE SET qualification_receipt_id = "
            "excluded.qualification_receipt_id, state = excluded.state, stale_reason = "
            "excluded.stale_reason, bound_by = excluded.bound_by, updated_at = excluded.updated_at",
            value,
        )
        return (
            self.get_current_use_binding(
                tenant_id, value["claim_revision_id"], value["profile_id"], value["use_scope"]
            )
            or value
        )

    def get_current_use_binding(
        self,
        tenant_id: str,
        claim_id: str,
        profile_id: str,
        use_scope: str = "knowledge",
    ) -> dict | None:
        return self._one(
            "SELECT * FROM current_use_bindings WHERE tenant_id = :tenant_id "
            "AND claim_revision_id = :claim_id AND profile_id = :profile_id "
            "AND use_scope = :use_scope",
            {
                "tenant_id": tenant_id,
                "claim_id": claim_id,
                "profile_id": profile_id,
                "use_scope": use_scope,
            },
        )

    def list_current_use_bindings(
        self, tenant_id: str, profile_id: str, state: str = "current"
    ) -> list[dict]:
        return self._many(
            "SELECT * FROM current_use_bindings WHERE tenant_id = :tenant_id "
            "AND profile_id = :profile_id AND state = :state ORDER BY updated_at DESC",
            {"tenant_id": tenant_id, "profile_id": profile_id, "state": state},
        )

    def create_authorization_grant(self, tenant_id: str, values: dict[str, Any]) -> dict:
        value = {
            "id": values.get("id") or str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "qualification_receipt_id": values["qualification_receipt_id"],
            "actor_id": values["actor_id"],
            "action": values["action"],
            "target": values["target"],
            "scope": json.dumps(values.get("scope") or {}, ensure_ascii=False),
            "conditions": json.dumps(values.get("conditions") or {}, ensure_ascii=False),
            "expires_at": values.get("expires_at"),
            "budget": json.dumps(values.get("budget") or {}, ensure_ascii=False),
            "max_calls": int(values.get("max_calls") or 1),
            "calls_used": 0,
            "policy_version": values["policy_version"],
            "state": values.get("state") or "active",
            "grant_receipt": values["grant_receipt"],
            "created_by": values.get("created_by"),
            "created_at": utc_now(),
        }
        if not self.get_qualification_receipt(tenant_id, value["qualification_receipt_id"]):
            raise KeyError(value["qualification_receipt_id"])
        self._execute(
            "INSERT INTO authorization_grants(id, tenant_id, qualification_receipt_id, "
            "actor_id, action, target, scope, conditions, expires_at, budget, max_calls, "
            "calls_used, policy_version, state, grant_receipt, created_by, created_at) "
            "VALUES (:id, :tenant_id, :qualification_receipt_id, :actor_id, :action, "
            ":target, :scope, :conditions, :expires_at, :budget, :max_calls, :calls_used, "
            ":policy_version, :state, :grant_receipt, :created_by, :created_at)",
            value,
        )
        return _decode_authorization_grant(value)

    def list_authorization_grants(
        self, tenant_id: str, qualification_receipt_ids: list[str] | None = None
    ) -> list[dict]:
        statement = "SELECT * FROM authorization_grants WHERE tenant_id = :tenant_id"
        parameters: dict[str, Any] = {"tenant_id": tenant_id}
        if qualification_receipt_ids is not None:
            if not qualification_receipt_ids:
                return []
            placeholders = []
            for index, receipt_id in enumerate(qualification_receipt_ids):
                key = f"receipt_{index}"
                placeholders.append(f":{key}")
                parameters[key] = receipt_id
            statement += " AND qualification_receipt_id IN (" + ",".join(placeholders) + ")"
        statement += " ORDER BY created_at, id"
        return [_decode_authorization_grant(row) for row in self._many(statement, parameters)]

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
        return [
            *messages,
            *documents,
            *steps,
            *artifacts,
            *citations,
            *decisions,
            *research_claims,
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
