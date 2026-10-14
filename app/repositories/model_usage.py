"""SQLite and PostgreSQL persistence for model call receipts.

The pre-call receipt and RUNNING Step are inserted together. Finalization is
also transactional, so a crashed process leaves a visible IN_FLIGHT attempt
instead of a fabricated zero-cost result.
"""

from __future__ import annotations

import json
from typing import Any

from ..redaction import redact, redact_record_text
from .common import utc_now

_IDENTITY_FIELDS = (
    "model_call_id", "tenant_id", "principal_id", "api_key_id", "request_id",
    "run_id", "step_id", "iteration", "model", "provider_config_id",
    "pricing_snapshot",
)
_BEGIN_COLUMNS = (
    "attempt_id", *_IDENTITY_FIELDS, "usage_status", "created_at",
)
_FINAL_COLUMNS = (
    "usage_status", "prompt_tokens", "completion_tokens", "estimated_cost",
    "termination", "finish_reason", "provider_request_id", "output_bytes", "finalization_hash",
    "finalized_at",
)


def _decoded(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["pricing_snapshot"] = json.loads(value["pricing_snapshot"])
    return value


def _begin_values(values: dict[str, Any]) -> dict[str, Any]:
    result = {key: values.get(key) for key in _BEGIN_COLUMNS}
    result["pricing_snapshot"] = json.dumps(
        values["pricing_snapshot"], sort_keys=True, separators=(",", ":")
    )
    result["usage_status"] = "IN_FLIGHT"
    result["created_at"] = utc_now()
    return result


def _validate_existing(existing: dict[str, Any], proposed: dict[str, Any]) -> None:
    if any(existing[key] != proposed[key] for key in ("attempt_id", *_IDENTITY_FIELDS)):
        raise ValueError("Model usage attempt identity conflicts with an existing receipt")


def _final_values(values: dict[str, Any]) -> dict[str, Any]:
    result = {key: values.get(key) for key in _FINAL_COLUMNS}
    result["finalized_at"] = utc_now()
    return result


class SQLiteModelUsageRepositoryMixin:
    def begin_model_usage(self, values: dict[str, Any]) -> dict[str, Any]:
        proposed = _begin_values(values)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owned = db.execute(
                "SELECT 1 FROM agent_runs WHERE tenant_id = ? AND id = ? AND request_id = ?",
                (values["tenant_id"], values["run_id"], values["request_id"]),
            ).fetchone()
            if owned is None:
                raise KeyError(values["run_id"])
            columns = ", ".join(_BEGIN_COLUMNS)
            marks = ", ".join("?" for _ in _BEGIN_COLUMNS)
            inserted = db.execute(
                f"INSERT INTO model_usage_receipts({columns}) VALUES ({marks}) "
                "ON CONFLICT(attempt_id) DO NOTHING",
                tuple(proposed[key] for key in _BEGIN_COLUMNS),
            ).rowcount
            if inserted:
                db.execute(
                    "INSERT INTO run_steps(id, run_id, tenant_id, sequence, kind, name, status, "
                    "input_content, output_content, metadata, created_at) "
                    "VALUES (?, ?, ?, ?, 'model', ?, 'running', ?, '', ?, ?)",
                    (
                        values["step_id"], values["run_id"], values["tenant_id"],
                        values["sequence"], values["model"],
                        redact_record_text(values["input_content"]),
                        json.dumps(redact(values["step_metadata"]), ensure_ascii=False),
                        proposed["created_at"],
                    ),
                )
            row = db.execute(
                "SELECT * FROM model_usage_receipts WHERE attempt_id = ?",
                (values["attempt_id"],),
            ).fetchone()
            if row is None:
                raise RuntimeError("Model usage begin did not persist")
            _validate_existing(dict(row), proposed)
        return _decoded(row)

    def finish_model_usage(
        self, tenant_id: str, attempt_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        finished = _final_values(values)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM model_usage_receipts WHERE tenant_id = ? AND attempt_id = ?",
                (tenant_id, attempt_id),
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            existing = dict(row)
            if existing["usage_status"] != "IN_FLIGHT":
                if existing["finalization_hash"] != finished["finalization_hash"]:
                    raise ValueError("Model usage receipt is already finalized differently")
                return _decoded(existing)
            updated = db.execute(
                "UPDATE run_steps SET status = ?, output_content = ?, metadata = ? "
                "WHERE id = ? AND tenant_id = ? AND run_id = ? AND status = 'running'",
                (
                    values["step_status"], redact_record_text(values["output_content"]),
                    json.dumps(redact(values["step_metadata"]), ensure_ascii=False),
                    existing["step_id"], tenant_id, existing["run_id"],
                ),
            ).rowcount
            if updated != 1:
                raise RuntimeError("Model Step was not available for atomic finalization")
            assignments = ", ".join(f"{key} = ?" for key in _FINAL_COLUMNS)
            db.execute(
                f"UPDATE model_usage_receipts SET {assignments} "
                "WHERE tenant_id = ? AND attempt_id = ? AND usage_status = 'IN_FLIGHT'",
                (*[finished[key] for key in _FINAL_COLUMNS], tenant_id, attempt_id),
            )
            result = db.execute(
                "SELECT * FROM model_usage_receipts WHERE tenant_id = ? AND attempt_id = ?",
                (tenant_id, attempt_id),
            ).fetchone()
        return _decoded(result)

    def get_model_usage(self, tenant_id: str, attempt_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM model_usage_receipts WHERE tenant_id = ? AND attempt_id = ?",
                (tenant_id, attempt_id),
            ).fetchone()
        return _decoded(row) if row is not None else None

    def list_model_usage(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM model_usage_receipts WHERE tenant_id = ? AND run_id = ? "
                "ORDER BY iteration, created_at, attempt_id",
                (tenant_id, run_id),
            ).fetchall()
        return [_decoded(row) for row in rows]


class PostgresModelUsageRepositoryMixin:
    def begin_model_usage(self, values: dict[str, Any]) -> dict[str, Any]:
        from sqlalchemy import text

        proposed = _begin_values(values)
        with self.engine.begin() as connection:
            owned = connection.execute(
                text("SELECT 1 FROM agent_runs WHERE tenant_id = :tenant_id AND id = :run_id "
                     "AND request_id = :request_id"),
                values,
            ).first()
            if owned is None:
                raise KeyError(values["run_id"])
            columns = ", ".join(_BEGIN_COLUMNS)
            marks = ", ".join(f":{key}" for key in _BEGIN_COLUMNS)
            inserted = connection.execute(
                text(f"INSERT INTO model_usage_receipts({columns}) VALUES ({marks}) "
                     "ON CONFLICT(attempt_id) DO NOTHING"),
                proposed,
            ).rowcount
            if inserted:
                connection.execute(
                    text("INSERT INTO run_steps(id, run_id, tenant_id, sequence, kind, name, status, "
                         "input_content, output_content, metadata, created_at) "
                         "VALUES (:step_id, :run_id, :tenant_id, :sequence, 'model', :model, "
                         "'running', :input_content, '', :step_metadata, :created_at)"),
                    {
                        **values,
                        "input_content": redact_record_text(values["input_content"]),
                        "step_metadata": json.dumps(redact(values["step_metadata"]), ensure_ascii=False),
                        "created_at": proposed["created_at"],
                    },
                )
            row = connection.execute(
                text("SELECT * FROM model_usage_receipts WHERE attempt_id = :attempt_id"),
                {"attempt_id": values["attempt_id"]},
            ).mappings().first()
            if row is None:
                raise RuntimeError("Model usage begin did not persist")
            _validate_existing(dict(row), proposed)
        return _decoded(row)

    def finish_model_usage(
        self, tenant_id: str, attempt_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        from sqlalchemy import text

        finished = _final_values(values)
        with self.engine.begin() as connection:
            row = connection.execute(
                text("SELECT * FROM model_usage_receipts WHERE tenant_id = :tenant_id "
                     "AND attempt_id = :attempt_id FOR UPDATE"),
                {"tenant_id": tenant_id, "attempt_id": attempt_id},
            ).mappings().first()
            if row is None:
                raise KeyError(attempt_id)
            existing = dict(row)
            if existing["usage_status"] != "IN_FLIGHT":
                if existing["finalization_hash"] != finished["finalization_hash"]:
                    raise ValueError("Model usage receipt is already finalized differently")
                return _decoded(existing)
            updated = connection.execute(
                text("UPDATE run_steps SET status = :step_status, "
                     "output_content = :output_content, metadata = :step_metadata "
                     "WHERE id = :step_id AND tenant_id = :tenant_id "
                     "AND run_id = :run_id AND status = 'running'"),
                {
                    "step_status": values["step_status"],
                    "output_content": redact_record_text(values["output_content"]),
                    "step_metadata": json.dumps(redact(values["step_metadata"]), ensure_ascii=False),
                    "step_id": existing["step_id"], "tenant_id": tenant_id,
                    "run_id": existing["run_id"],
                },
            ).rowcount
            if updated != 1:
                raise RuntimeError("Model Step was not available for atomic finalization")
            assignments = ", ".join(f"{key} = :{key}" for key in _FINAL_COLUMNS)
            connection.execute(
                text(f"UPDATE model_usage_receipts SET {assignments} "
                     "WHERE tenant_id = :tenant_id AND attempt_id = :attempt_id "
                     "AND usage_status = 'IN_FLIGHT'"),
                {**finished, "tenant_id": tenant_id, "attempt_id": attempt_id},
            )
            result = connection.execute(
                text("SELECT * FROM model_usage_receipts WHERE tenant_id = :tenant_id "
                     "AND attempt_id = :attempt_id"),
                {"tenant_id": tenant_id, "attempt_id": attempt_id},
            ).mappings().first()
        return _decoded(result)

    def get_model_usage(self, tenant_id: str, attempt_id: str) -> dict[str, Any] | None:
        value = self._one(
            "SELECT * FROM model_usage_receipts WHERE tenant_id = :tenant_id AND attempt_id = :attempt_id",
            {"tenant_id": tenant_id, "attempt_id": attempt_id},
        )
        return _decoded(value) if value is not None else None

    def list_model_usage(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        values = self._many(
            "SELECT * FROM model_usage_receipts WHERE tenant_id = :tenant_id AND run_id = :run_id "
            "ORDER BY iteration, created_at, attempt_id",
            {"tenant_id": tenant_id, "run_id": run_id},
        )
        return [_decoded(value) for value in values]
