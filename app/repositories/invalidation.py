"""Immutable invalidation evidence and atomic current-use transitions."""

from __future__ import annotations

import json
from typing import Any

from ..core.authority import AuthorityContext
from ..core.contracts import RequestContext
from ..core.errors import AuthorityDeniedError
from .common import lock_qualification_authority_workspace


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


def _dependent_binding_ids(
    binding_rows: list[dict[str, Any]], direct_ids: list[str]
) -> list[str]:
    """Find current hard dependents of a directly invalidated binding."""

    affected = set(direct_ids)
    affected_keys = {
        (
            row["claim_revision_id"], row["profile_id"], row["use_scope"],
            row["qualification_receipt_id"],
        )
        for row in binding_rows if row["id"] in affected
    }
    while True:
        added = False
        for row in binding_rows:
            if row["id"] in affected:
                continue
            closure = json.loads(row["evidence_closure"])
            for node in closure.get("nodes", []):
                if node.get("node_type") != "dependency_binding":
                    continue
                frozen = node.get("payload") or {}
                key = (
                    node.get("node_id"), frozen.get("profile_id"),
                    frozen.get("use_scope"), frozen.get("qualification_receipt_id"),
                )
                if key in affected_keys:
                    affected.add(row["id"])
                    affected_keys.add(
                        (
                            row["claim_revision_id"], row["profile_id"],
                            row["use_scope"], row["qualification_receipt_id"],
                        )
                    )
                    added = True
                    break
        if not added:
            break
    return sorted(affected - set(direct_ids))


class SQLiteInvalidationRepositoryMixin:
    def record_notice_verification(
        self, tenant_id: str, values: dict, *,
        context: RequestContext, authority: AuthorityContext,
    ) -> dict:
        row = {**values, "tenant_id": tenant_id}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if tenant_id != context.workspace_id:
                raise AuthorityDeniedError("qualification:invalidate")
            self._assert_admin_write_authority(
                db, context, authority, "qualification:invalidate"
            )
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

    def apply_invalidation_decision(
        self, tenant_id: str, values: dict, *,
        context: RequestContext, authority: AuthorityContext,
    ) -> dict:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if tenant_id != context.workspace_id:
                raise AuthorityDeniedError("qualification:invalidate")
            self._assert_admin_write_authority(
                db, context, authority, "qualification:invalidate"
            )
            parameters: list[Any] = [tenant_id, values["target_claim_revision_id"]]
            query = (
                "SELECT id FROM current_use_bindings WHERE tenant_id = ? "
                "AND claim_revision_id = ? AND state = 'current'"
            )
            if values["target_scope"] == "receipt":
                query += " AND qualification_receipt_id = ?"
                parameters.append(values["target_receipt_id"])
            elif values["target_scope"] == "proof_attempt":
                query += (
                    " AND EXISTS (SELECT 1 FROM qualification_receipt_attempt_selections s "
                    "WHERE s.tenant_id = current_use_bindings.tenant_id "
                    "AND s.qualification_receipt_id = "
                    "current_use_bindings.qualification_receipt_id "
                    "AND s.attempt_id = ?)"
                )
                parameters.append(values["target_attempt_id"])
            direct_ids = [row["id"] for row in db.execute(query, parameters).fetchall()]
            binding_rows = [
                dict(item) for item in db.execute(
                    "SELECT b.id, b.claim_revision_id, b.profile_id, b.use_scope, "
                    "b.qualification_receipt_id, "
                    "e.evidence_closure FROM current_use_bindings b "
                    "JOIN qualification_receipts r ON r.tenant_id = b.tenant_id "
                    "AND r.id = b.qualification_receipt_id "
                    "JOIN qualification_evaluations e ON e.tenant_id = r.tenant_id "
                    "AND e.id = r.evaluation_id "
                    "WHERE b.tenant_id = ? AND b.state = 'current'",
                    (tenant_id,),
                ).fetchall()
            ]
            affected_ids = direct_ids + _dependent_binding_ids(binding_rows, direct_ids)
            row = _decision_row({**values, "tenant_id": tenant_id}, affected_ids)
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
                        values["effect_state"] if binding_id in direct_ids else "stale",
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
    def record_notice_verification(
        self, tenant_id: str, values: dict, *,
        context: RequestContext, authority: AuthorityContext,
    ) -> dict:
        row = {**values, "tenant_id": tenant_id}
        from sqlalchemy import text

        with self.engine.begin() as connection:
            if tenant_id != context.workspace_id:
                raise AuthorityDeniedError("qualification:invalidate")
            self._assert_admin_write_authority(
                connection, context, authority, "qualification:invalidate"
            )
            connection.execute(
                text(
                    "INSERT INTO notice_verifications(id, tenant_id, source_repository, "
                    "source_commit, source_path, source_artifact_id, source_hash, "
                    "verification_method, verification_hash, requested_by, verified_at) "
                    "VALUES (:id, :tenant_id, :source_repository, :source_commit, :source_path, "
                    ":source_artifact_id, :source_hash, :verification_method, "
                    ":verification_hash, :requested_by, :verified_at)"
                ),
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

    def apply_invalidation_decision(
        self, tenant_id: str, values: dict, *,
        context: RequestContext, authority: AuthorityContext,
    ) -> dict:
        from sqlalchemy import text

        parameters = {
            "tenant_id": tenant_id,
            "claim_id": values["target_claim_revision_id"],
            "receipt_id": values["target_receipt_id"],
            "attempt_id": values["target_attempt_id"],
        }
        query = (
            "SELECT id FROM current_use_bindings WHERE tenant_id = :tenant_id "
            "AND claim_revision_id = :claim_id AND state = 'current'"
        )
        if values["target_scope"] == "receipt":
            query += " AND qualification_receipt_id = :receipt_id"
        elif values["target_scope"] == "proof_attempt":
            query += (
                " AND EXISTS (SELECT 1 FROM qualification_receipt_attempt_selections s "
                "WHERE s.tenant_id = current_use_bindings.tenant_id "
                "AND s.qualification_receipt_id = "
                "current_use_bindings.qualification_receipt_id "
                "AND s.attempt_id = :attempt_id)"
            )
        query += " FOR UPDATE"
        with self.engine.begin() as connection:
            if tenant_id != context.workspace_id:
                raise AuthorityDeniedError("qualification:invalidate")
            self._assert_admin_write_authority(
                connection, context, authority, "qualification:invalidate"
            )
            lock_qualification_authority_workspace(connection, tenant_id)
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"{tenant_id}:{values['target_claim_revision_id']}"},
            )
            direct_ids = [
                item["id"] for item in connection.execute(text(query), parameters).mappings().all()
            ]
            binding_rows = [
                dict(item) for item in connection.execute(
                    text(
                        "SELECT b.id, b.claim_revision_id, b.profile_id, b.use_scope, "
                        "b.qualification_receipt_id, "
                        "e.evidence_closure FROM current_use_bindings b "
                        "JOIN qualification_receipts r ON r.tenant_id = b.tenant_id "
                        "AND r.id = b.qualification_receipt_id "
                        "JOIN qualification_evaluations e ON e.tenant_id = r.tenant_id "
                        "AND e.id = r.evaluation_id "
                        "WHERE b.tenant_id = :tenant_id AND b.state = 'current'"
                    ),
                    {"tenant_id": tenant_id},
                ).mappings().all()
            ]
            affected_ids = direct_ids + _dependent_binding_ids(binding_rows, direct_ids)
            row = _decision_row({**values, "tenant_id": tenant_id}, affected_ids)
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
                        "state": values["effect_state"] if binding_id in direct_ids else "stale",
                        "reason": f"invalidation_decision:{values['id']}",
                        "updated_at": values["decided_at"],
                        "tenant_id": tenant_id,
                        "binding_id": binding_id,
                    },
                )
                if updated.rowcount != 1:
                    raise RuntimeError("Current-use binding changed during invalidation")
        return _decode_decision(row)
