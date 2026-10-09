"""Immutable invalidation evidence and atomic current-use transitions."""

from __future__ import annotations

import json
from typing import Any


def _decode_decision(value: Any) -> dict[str, Any]:
    result = dict(value)
    for field in ("evidence_artifact_ids", "evidence_artifact_hashes", "affected_binding_ids"):
        if isinstance(result[field], str):
            result[field] = json.loads(result[field])
    return result


def _decision_row(values: dict[str, Any], affected_ids: list[str]) -> dict[str, Any]:
    return {
        **values,
        "evidence_artifact_ids": json.dumps(values["evidence_artifact_ids"]),
        "evidence_artifact_hashes": json.dumps(values["evidence_artifact_hashes"]),
        "affected_binding_ids": json.dumps(affected_ids),
    }


_DECISION_COLUMNS = (
    "id, tenant_id, notice_verification_id, target_claim_revision_id, "
    "target_claim_semantic_hash, target_receipt_id, target_attempt_id, target_scope, "
    "reason_code, evidence_artifact_ids, evidence_artifact_hashes, policy_id, "
    "policy_hash, rationale, decided_by, "
    "effect_state, affected_binding_ids, decision_hash, decided_at"
)
_DECISION_NAMES = tuple(name.strip() for name in _DECISION_COLUMNS.split(","))
_NOTICE_NAMES = (
    "id",
    "tenant_id",
    "source_repository",
    "source_commit",
    "source_path",
    "source_artifact_id",
    "source_hash",
    "verification_method",
    "verification_hash",
    "requested_by",
    "verified_at",
)


class SQLiteInvalidationRepositoryMixin:
    def record_notice_verification(self, tenant_id: str, values: dict) -> dict:
        row = {"tenant_id": tenant_id, **values}
        with self._connect() as db:
            db.execute(
                "INSERT INTO notice_verifications(id, tenant_id, source_repository, "
                "source_commit, source_path, source_artifact_id, source_hash, "
                "verification_method, verification_hash, requested_by, verified_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(row[name] for name in _NOTICE_NAMES),
            )
        return row

    def get_notice_verification(self, tenant_id: str, verification_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM notice_verifications WHERE tenant_id = ? AND id = ?",
                (tenant_id, verification_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_invalidation_decisions(self, tenant_id: str, claim_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM invalidation_decisions WHERE tenant_id = ? "
                "AND target_claim_revision_id = ? ORDER BY decided_at, id",
                (tenant_id, claim_id),
            ).fetchall()
        return [_decode_decision(row) for row in rows]

    def apply_invalidation_decision(self, tenant_id: str, values: dict) -> dict:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            parameters: list[Any] = [tenant_id, values["target_claim_revision_id"]]
            query = (
                "SELECT id FROM current_use_bindings WHERE tenant_id = ? "
                "AND claim_revision_id = ? AND state = 'current'"
            )
            if values["target_scope"] != "claim_revision":
                query += " AND qualification_receipt_id = ?"
                parameters.append(values["target_receipt_id"])
            affected_ids = [row["id"] for row in db.execute(query, parameters).fetchall()]
            row = _decision_row({"tenant_id": tenant_id, **values}, affected_ids)
            db.execute(
                f"INSERT INTO invalidation_decisions({_DECISION_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(row[name] for name in _DECISION_NAMES),
            )
            for binding_id in affected_ids:
                updated = db.execute(
                    "UPDATE current_use_bindings SET state = ?, stale_reason = ?, "
                    "bound_by = ?, updated_at = ? WHERE tenant_id = ? AND id = ? "
                    "AND state = 'current'",
                    (
                        values["effect_state"],
                        f"invalidation_decision:{values['id']}",
                        "system:invalidation-decision",
                        values["decided_at"],
                        tenant_id,
                        binding_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise RuntimeError("Current-use binding changed during invalidation")
        return _decode_decision(row)


class PostgresInvalidationRepositoryMixin:
    def record_notice_verification(self, tenant_id: str, values: dict) -> dict:
        row = {"tenant_id": tenant_id, **values}
        self._execute(
            "INSERT INTO notice_verifications(id, tenant_id, source_repository, "
            "source_commit, source_path, source_artifact_id, source_hash, "
            "verification_method, verification_hash, requested_by, verified_at) "
            "VALUES (:id, :tenant_id, :source_repository, :source_commit, :source_path, "
            ":source_artifact_id, :source_hash, :verification_method, "
            ":verification_hash, :requested_by, :verified_at)",
            row,
        )
        return row

    def get_notice_verification(self, tenant_id: str, verification_id: str) -> dict | None:
        return self._one(
            "SELECT * FROM notice_verifications WHERE tenant_id = :tenant_id "
            "AND id = :verification_id",
            {"tenant_id": tenant_id, "verification_id": verification_id},
        )

    def list_invalidation_decisions(self, tenant_id: str, claim_id: str) -> list[dict]:
        return [
            _decode_decision(row)
            for row in self._many(
                "SELECT * FROM invalidation_decisions WHERE tenant_id = :tenant_id "
                "AND target_claim_revision_id = :claim_id ORDER BY decided_at, id",
                {"tenant_id": tenant_id, "claim_id": claim_id},
            )
        ]

    def apply_invalidation_decision(self, tenant_id: str, values: dict) -> dict:
        from sqlalchemy import text

        parameters = {
            "tenant_id": tenant_id,
            "claim_id": values["target_claim_revision_id"],
            "receipt_id": values["target_receipt_id"],
        }
        query = (
            "SELECT id FROM current_use_bindings WHERE tenant_id = :tenant_id "
            "AND claim_revision_id = :claim_id AND state = 'current'"
        )
        if values["target_scope"] != "claim_revision":
            query += " AND qualification_receipt_id = :receipt_id"
        query += " FOR UPDATE"
        with self.engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"{tenant_id}:{values['target_claim_revision_id']}"},
            )
            affected_ids = [
                item["id"] for item in connection.execute(text(query), parameters).mappings().all()
            ]
            row = _decision_row({"tenant_id": tenant_id, **values}, affected_ids)
            placeholders = ", ".join(f":{name}" for name in _DECISION_NAMES)
            connection.execute(
                text(
                    f"INSERT INTO invalidation_decisions({_DECISION_COLUMNS}) VALUES ({placeholders})"
                ),
                row,
            )
            for binding_id in affected_ids:
                updated = connection.execute(
                    text(
                        "UPDATE current_use_bindings SET state = :state, stale_reason = :reason, "
                        "bound_by = 'system:invalidation-decision', updated_at = :updated_at "
                        "WHERE tenant_id = :tenant_id AND id = :binding_id AND state = 'current'"
                    ),
                    {
                        "state": values["effect_state"],
                        "reason": f"invalidation_decision:{values['id']}",
                        "updated_at": values["decided_at"],
                        "tenant_id": tenant_id,
                        "binding_id": binding_id,
                    },
                )
                if updated.rowcount != 1:
                    raise RuntimeError("Current-use binding changed during invalidation")
        return _decode_decision(row)
