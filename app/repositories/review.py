"""Review Workbench relational repository implementations."""

from __future__ import annotations

import json
from typing import Any

from .common import decode_list as _decode_list
from .common import decode_metadata as _decode_metadata
from .common import utc_now


def _decode_review_run(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["profile_version"] = int(value.get("profile_version") or 1)
    value["finding_count"] = int(value.get("finding_count") or 0)
    value["input_snapshot"] = _decode_metadata(value.get("input_snapshot"))
    value["reviewer_lineage"] = _decode_metadata(value.get("reviewer_lineage"))
    value["severity_counts"] = _decode_metadata(value.get("severity_counts"))
    return value


def _decode_review_finding(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["profile_version"] = int(value.get("profile_version") or 1)
    value["ordinal"] = int(value.get("ordinal") or 0)
    value["evidence_refs"] = _decode_list(value.get("evidence_refs"))
    return value


def _run_value(tenant_id: str, values: dict[str, Any], created_at: str) -> dict[str, Any]:
    return {
        "id": values["id"],
        "tenant_id": tenant_id,
        "profile_id": values["profile_id"],
        "profile_version": int(values["profile_version"]),
        "profile_hash": values["profile_hash"],
        "subject_type": values["subject_type"],
        "subject_id": values["subject_id"],
        "subject_digest": values["subject_digest"],
        "input_snapshot": json.dumps(values["input_snapshot"], ensure_ascii=False),
        "reviewer_origin": values["reviewer_origin"],
        "reviewer_lineage": json.dumps(values["reviewer_lineage"], ensure_ascii=False),
        "status": values["status"],
        "finding_count": int(values["finding_count"]),
        "severity_counts": json.dumps(values["severity_counts"], ensure_ascii=False),
        "requested_by": values.get("requested_by"),
        "created_at": created_at,
    }


def _finding_value(
    tenant_id: str,
    review_run: dict[str, Any],
    values: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    return {
        "id": values["id"],
        "tenant_id": tenant_id,
        "review_run_id": review_run["id"],
        "profile_id": review_run["profile_id"],
        "profile_version": review_run["profile_version"],
        "subject_type": review_run["subject_type"],
        "subject_id": review_run["subject_id"],
        "ordinal": int(values["ordinal"]),
        "rule_id": values["rule_id"],
        "severity": values["severity"],
        "origin": values["origin"],
        "title": values["title"],
        "summary": values["summary"],
        "suggestion": values["suggestion"],
        "evidence_refs": json.dumps(values.get("evidence_refs") or [], ensure_ascii=False),
        "status": values["status"],
        "finding_hash": values["finding_hash"],
        "created_at": created_at,
    }


class SQLiteReviewRepositoryMixin:
    """SQLite implementation of the advisory review ledger."""

    def create_review_run(
        self,
        tenant_id: str,
        values: dict[str, Any],
        findings: list[dict[str, Any]],
    ) -> dict:
        created_at = utc_now()
        run = _run_value(tenant_id, values, created_at)
        finding_rows = [
            _finding_value(tenant_id, run, finding, created_at) for finding in findings
        ]
        with self._connect() as db:
            if run["subject_type"] != "agent_run" or db.execute(
                "SELECT 1 FROM agent_runs WHERE tenant_id = ? AND id = ?",
                (tenant_id, run["subject_id"]),
            ).fetchone() is None:
                raise KeyError(run["subject_id"])
            db.execute(
                "INSERT INTO review_runs(id, tenant_id, profile_id, profile_version, "
                "profile_hash, subject_type, subject_id, subject_digest, input_snapshot, "
                "reviewer_origin, reviewer_lineage, status, finding_count, severity_counts, "
                "requested_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?)",
                tuple(run.values()),
            )
            if finding_rows:
                db.executemany(
                    "INSERT INTO review_findings(id, tenant_id, review_run_id, profile_id, "
                    "profile_version, subject_type, subject_id, ordinal, rule_id, severity, origin, "
                    "title, summary, suggestion, evidence_refs, status, finding_hash, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [tuple(item.values()) for item in finding_rows],
                )
        result = _decode_review_run(run)
        result["findings"] = [_decode_review_finding(item) for item in finding_rows]
        return result

    def get_review_run(self, tenant_id: str, review_run_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM review_runs WHERE tenant_id = ? AND id = ?",
                (tenant_id, review_run_id),
            ).fetchone()
            if row is None:
                return None
            findings = db.execute(
                "SELECT * FROM review_findings WHERE tenant_id = ? AND review_run_id = ? "
                "ORDER BY ordinal",
                (tenant_id, review_run_id),
            ).fetchall()
        value = _decode_review_run(row)
        value["findings"] = [_decode_review_finding(item) for item in findings]
        return value

    def list_review_runs(
        self,
        tenant_id: str,
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
        profile_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        statement = "SELECT * FROM review_runs WHERE tenant_id = ?"
        parameters: list[Any] = [tenant_id]
        for column, value in (
            ("subject_type", subject_type),
            ("subject_id", subject_id),
            ("profile_id", profile_id),
        ):
            if value is not None:
                statement += f" AND {column} = ?"
                parameters.append(value)
        statement += " ORDER BY created_at DESC, id DESC LIMIT ?"
        parameters.append(max(1, min(limit, 200)))
        with self._connect() as db:
            rows = db.execute(statement, parameters).fetchall()
        return [_decode_review_run(row) for row in rows]

    def get_review_finding(self, tenant_id: str, finding_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM review_findings WHERE tenant_id = ? AND id = ?",
                (tenant_id, finding_id),
            ).fetchone()
        return _decode_review_finding(row) if row is not None else None

    def list_review_findings(
        self,
        tenant_id: str,
        *,
        review_run_id: str | None = None,
        subject_id: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        statement = "SELECT * FROM review_findings WHERE tenant_id = ?"
        parameters: list[Any] = [tenant_id]
        for column, value in (
            ("review_run_id", review_run_id),
            ("subject_id", subject_id),
            ("severity", severity),
            ("status", status),
        ):
            if value is not None:
                statement += f" AND {column} = ?"
                parameters.append(value)
        statement += " ORDER BY created_at DESC, id DESC LIMIT ?"
        parameters.append(max(1, min(limit, 500)))
        with self._connect() as db:
            rows = db.execute(statement, parameters).fetchall()
        return [_decode_review_finding(row) for row in rows]


class PostgresReviewRepositoryMixin:
    """PostgreSQL implementation of the advisory review ledger."""

    def create_review_run(
        self,
        tenant_id: str,
        values: dict[str, Any],
        findings: list[dict[str, Any]],
    ) -> dict:
        from sqlalchemy import text

        created_at = utc_now()
        run = _run_value(tenant_id, values, created_at)
        finding_rows = [
            _finding_value(tenant_id, run, finding, created_at) for finding in findings
        ]
        with self.engine.begin() as connection:
            owned = connection.execute(
                text(
                    "SELECT 1 FROM agent_runs WHERE tenant_id = :tenant_id AND id = :subject_id"
                ),
                run,
            ).first()
            if run["subject_type"] != "agent_run" or owned is None:
                raise KeyError(run["subject_id"])
            connection.execute(
                text(
                    "INSERT INTO review_runs(id, tenant_id, profile_id, profile_version, "
                    "profile_hash, subject_type, subject_id, subject_digest, input_snapshot, "
                    "reviewer_origin, reviewer_lineage, status, finding_count, severity_counts, "
                    "requested_by, created_at) VALUES (:id, :tenant_id, :profile_id, "
                    ":profile_version, :profile_hash, :subject_type, :subject_id, "
                    ":subject_digest, :input_snapshot, :reviewer_origin, :reviewer_lineage, "
                    ":status, :finding_count, :severity_counts, :requested_by, :created_at)"
                ),
                run,
            )
            if finding_rows:
                connection.execute(
                    text(
                        "INSERT INTO review_findings(id, tenant_id, review_run_id, profile_id, "
                        "profile_version, subject_type, subject_id, ordinal, rule_id, severity, origin, "
                        "title, summary, suggestion, evidence_refs, status, finding_hash, "
                        "created_at) VALUES (:id, :tenant_id, :review_run_id, :profile_id, "
                        ":profile_version, :subject_type, :subject_id, :ordinal, :rule_id, :severity, "
                        ":origin, :title, :summary, :suggestion, :evidence_refs, :status, "
                        ":finding_hash, :created_at)"
                    ),
                    finding_rows,
                )
        result = _decode_review_run(run)
        result["findings"] = [_decode_review_finding(item) for item in finding_rows]
        return result

    def get_review_run(self, tenant_id: str, review_run_id: str) -> dict | None:
        row = self._one(
            "SELECT * FROM review_runs WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": review_run_id},
        )
        if row is None:
            return None
        value = _decode_review_run(row)
        value["findings"] = [
            _decode_review_finding(item)
            for item in self._many(
                "SELECT * FROM review_findings WHERE tenant_id = :tenant_id "
                "AND review_run_id = :id ORDER BY ordinal",
                {"tenant_id": tenant_id, "id": review_run_id},
            )
        ]
        return value

    def list_review_runs(
        self,
        tenant_id: str,
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
        profile_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        statement = "SELECT * FROM review_runs WHERE tenant_id = :tenant_id"
        values: dict[str, Any] = {"tenant_id": tenant_id, "limit": max(1, min(limit, 200))}
        for column, value in (
            ("subject_type", subject_type),
            ("subject_id", subject_id),
            ("profile_id", profile_id),
        ):
            if value is not None:
                statement += f" AND {column} = :{column}"
                values[column] = value
        statement += " ORDER BY created_at DESC, id DESC LIMIT :limit"
        return [_decode_review_run(row) for row in self._many(statement, values)]

    def get_review_finding(self, tenant_id: str, finding_id: str) -> dict | None:
        row = self._one(
            "SELECT * FROM review_findings WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": finding_id},
        )
        return _decode_review_finding(row) if row is not None else None

    def list_review_findings(
        self,
        tenant_id: str,
        *,
        review_run_id: str | None = None,
        subject_id: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        statement = "SELECT * FROM review_findings WHERE tenant_id = :tenant_id"
        values: dict[str, Any] = {"tenant_id": tenant_id, "limit": max(1, min(limit, 500))}
        for column, value in (
            ("review_run_id", review_run_id),
            ("subject_id", subject_id),
            ("severity", severity),
            ("status", status),
        ):
            if value is not None:
                statement += f" AND {column} = :{column}"
                values[column] = value
        statement += " ORDER BY created_at DESC, id DESC LIMIT :limit"
        return [_decode_review_finding(row) for row in self._many(statement, values)]
