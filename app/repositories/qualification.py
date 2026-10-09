"""Qualification and authorization relational repository implementations."""

from __future__ import annotations

import json
import uuid
from typing import Any

from .common import authorization_not_expired as _authorization_not_expired
from .common import decode_list as _decode_list
from .common import decode_metadata as _decode_metadata
from .common import utc_now


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


def _decode_knowledge_admission_receipt(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["profile_version"] = int(value.get("profile_version") or 1)
    value["admission_policy_version"] = int(
        value.get("admission_policy_version") or 1
    )
    return value


class SQLiteQualificationRepositoryMixin:
    """SQLite implementation of the qualification repository port."""

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
            "knowledge_admission_receipt_id": values.get(
                "knowledge_admission_receipt_id"
            ),
            "state": values.get("state") or "current",
            "stale_reason": values.get("stale_reason"),
            "bound_by": values.get("bound_by"),
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as db:
            db.execute(
                "INSERT INTO current_use_bindings(id, tenant_id, claim_revision_id, "
                "profile_id, use_scope, qualification_receipt_id, "
                "knowledge_admission_receipt_id, state, stale_reason, bound_by, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, claim_revision_id, profile_id, use_scope) DO UPDATE "
                "SET qualification_receipt_id = excluded.qualification_receipt_id, "
                "knowledge_admission_receipt_id = excluded.knowledge_admission_receipt_id, "
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

    def admit_knowledge(self, tenant_id: str, values: dict[str, Any]) -> dict:
        """Atomically append an admission receipt and select its qualification."""

        now = utc_now()
        admission = {
            "id": values["id"],
            "tenant_id": tenant_id,
            "contract_version": values["contract_version"],
            "qualification_receipt_id": values["qualification_receipt_id"],
            "qualification_receipt_hash": values["qualification_receipt_hash"],
            "claim_revision_id": values["claim_revision_id"],
            "claim_semantic_hash": values["claim_semantic_hash"],
            "profile_id": values["profile_id"],
            "profile_version": int(values["profile_version"]),
            "use_scope": values["use_scope"],
            "admission_policy_id": values["admission_policy_id"],
            "admission_policy_version": int(values["admission_policy_version"]),
            "admission_policy_hash": values["admission_policy_hash"],
            "approved_by": values["approved_by"],
            "rationale": values["rationale"],
            "receipt_hash": values["receipt_hash"],
            "issued_at": values["issued_at"],
        }
        binding = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "claim_revision_id": admission["claim_revision_id"],
            "profile_id": admission["profile_id"],
            "use_scope": admission["use_scope"],
            "qualification_receipt_id": admission["qualification_receipt_id"],
            "knowledge_admission_receipt_id": admission["id"],
            "state": "current",
            "stale_reason": None,
            "bound_by": admission["approved_by"],
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO knowledge_admission_receipts(id, tenant_id, "
                "contract_version, qualification_receipt_id, qualification_receipt_hash, claim_revision_id, "
                "claim_semantic_hash, profile_id, profile_version, use_scope, "
                "admission_policy_id, admission_policy_version, admission_policy_hash, "
                "approved_by, rationale, receipt_hash, issued_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(admission.values()),
            )
            db.execute(
                "INSERT INTO current_use_bindings(id, tenant_id, claim_revision_id, "
                "profile_id, use_scope, qualification_receipt_id, "
                "knowledge_admission_receipt_id, state, stale_reason, bound_by, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, claim_revision_id, profile_id, use_scope) DO UPDATE "
                "SET qualification_receipt_id = excluded.qualification_receipt_id, "
                "knowledge_admission_receipt_id = excluded.knowledge_admission_receipt_id, "
                "state = excluded.state, stale_reason = excluded.stale_reason, "
                "bound_by = excluded.bound_by, updated_at = excluded.updated_at",
                tuple(binding.values()),
            )
            selected = db.execute(
                "SELECT * FROM current_use_bindings WHERE tenant_id = ? "
                "AND claim_revision_id = ? AND profile_id = ? AND use_scope = ?",
                (
                    tenant_id,
                    binding["claim_revision_id"],
                    binding["profile_id"],
                    binding["use_scope"],
                ),
            ).fetchone()
        return {
            "knowledge_admission_receipt": _decode_knowledge_admission_receipt(
                admission
            ),
            "current_use_binding": dict(selected),
        }

    def list_knowledge_admission_receipts(
        self, tenant_id: str, claim_id: str | None = None
    ) -> list[dict]:
        sql = "SELECT * FROM knowledge_admission_receipts WHERE tenant_id = ?"
        parameters: list[Any] = [tenant_id]
        if claim_id is not None:
            sql += " AND claim_revision_id = ?"
            parameters.append(claim_id)
        sql += " ORDER BY issued_at, id"
        with self._connect() as db:
            rows = db.execute(sql, parameters).fetchall()
        return [_decode_knowledge_admission_receipt(row) for row in rows]

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
        self,
        tenant_id: str,
        profile_id: str,
        state: str = "current",
        use_scope: str = "knowledge",
    ) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM current_use_bindings WHERE tenant_id = ? "
                "AND profile_id = ? AND state = ? AND use_scope = ? "
                "ORDER BY updated_at DESC",
                (tenant_id, profile_id, state, use_scope),
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

    def consume_authorization_grant(
        self,
        tenant_id: str,
        actor_id: str,
        action: str,
        target: str,
        used_at: str,
        *,
        grant_id: str | None = None,
    ) -> dict | None:
        """Atomically consume one grant backed by the exact current receipt."""

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            grant_filter = " AND g.id = ?" if grant_id is not None else ""
            rows = db.execute(
                "SELECT g.* FROM authorization_grants g "
                "JOIN qualification_receipts r ON r.tenant_id = g.tenant_id "
                "AND r.id = g.qualification_receipt_id "
                "WHERE g.tenant_id = ? AND g.actor_id = ? AND g.action = ? "
                "AND g.target = ? AND g.state = 'active' AND g.calls_used < g.max_calls "
                "AND EXISTS (SELECT 1 FROM current_use_bindings b "
                "WHERE b.tenant_id = g.tenant_id "
                "AND b.claim_revision_id = r.claim_revision_id "
                "AND b.profile_id = r.profile_id "
                "AND b.qualification_receipt_id = g.qualification_receipt_id "
                "AND b.state = 'current') "
                + grant_filter
                + " "
                "ORDER BY g.created_at, g.id",
                (tenant_id, actor_id, action, target, *([grant_id] if grant_id else [])),
            ).fetchall()
            for row in rows:
                grant = dict(row)
                if not _authorization_not_expired(grant.get("expires_at"), used_at):
                    db.execute(
                        "UPDATE authorization_grants SET state = 'expired' "
                        "WHERE tenant_id = ? AND id = ? AND state = 'active'",
                        (tenant_id, grant["id"]),
                    )
                    continue
                updated = db.execute(
                    "UPDATE authorization_grants SET calls_used = calls_used + 1 "
                    "WHERE tenant_id = ? AND id = ? AND state = 'active' "
                    "AND calls_used = ? AND calls_used < max_calls",
                    (tenant_id, grant["id"], grant["calls_used"]),
                )
                if updated.rowcount == 1:
                    grant["calls_used"] = int(grant["calls_used"]) + 1
                    return _decode_authorization_grant(grant)
        return None


class PostgresQualificationRepositoryMixin:
    """PostgreSQL implementation of the qualification repository port."""

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
            "knowledge_admission_receipt_id": values.get(
                "knowledge_admission_receipt_id"
            ),
            "state": values.get("state") or "current",
            "stale_reason": values.get("stale_reason"),
            "bound_by": values.get("bound_by"),
            "created_at": now,
            "updated_at": now,
        }
        self._execute(
            "INSERT INTO current_use_bindings(id, tenant_id, claim_revision_id, profile_id, "
            "use_scope, qualification_receipt_id, knowledge_admission_receipt_id, state, "
            "stale_reason, bound_by, created_at, updated_at) VALUES (:id, :tenant_id, "
            ":claim_revision_id, :profile_id, :use_scope, :qualification_receipt_id, "
            ":knowledge_admission_receipt_id, :state, :stale_reason, :bound_by, "
            ":created_at, :updated_at) ON CONFLICT(tenant_id, claim_revision_id, "
            "profile_id, use_scope) DO UPDATE SET qualification_receipt_id = "
            "excluded.qualification_receipt_id, knowledge_admission_receipt_id = "
            "excluded.knowledge_admission_receipt_id, state = excluded.state, stale_reason = "
            "excluded.stale_reason, bound_by = excluded.bound_by, updated_at = excluded.updated_at",
            value,
        )
        return (
            self.get_current_use_binding(
                tenant_id, value["claim_revision_id"], value["profile_id"], value["use_scope"]
            )
            or value
        )

    def admit_knowledge(self, tenant_id: str, values: dict[str, Any]) -> dict:
        """Atomically append an admission receipt and select its qualification."""

        from sqlalchemy import text

        now = utc_now()
        admission = {
            "id": values["id"],
            "tenant_id": tenant_id,
            "contract_version": values["contract_version"],
            "qualification_receipt_id": values["qualification_receipt_id"],
            "qualification_receipt_hash": values["qualification_receipt_hash"],
            "claim_revision_id": values["claim_revision_id"],
            "claim_semantic_hash": values["claim_semantic_hash"],
            "profile_id": values["profile_id"],
            "profile_version": int(values["profile_version"]),
            "use_scope": values["use_scope"],
            "admission_policy_id": values["admission_policy_id"],
            "admission_policy_version": int(values["admission_policy_version"]),
            "admission_policy_hash": values["admission_policy_hash"],
            "approved_by": values["approved_by"],
            "rationale": values["rationale"],
            "receipt_hash": values["receipt_hash"],
            "issued_at": values["issued_at"],
        }
        binding = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "claim_revision_id": admission["claim_revision_id"],
            "profile_id": admission["profile_id"],
            "use_scope": admission["use_scope"],
            "qualification_receipt_id": admission["qualification_receipt_id"],
            "knowledge_admission_receipt_id": admission["id"],
            "state": "current",
            "stale_reason": None,
            "bound_by": admission["approved_by"],
            "created_at": now,
            "updated_at": now,
        }
        with self.engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"{tenant_id}:{admission['claim_revision_id']}"},
            )
            connection.execute(
                text(
                    "INSERT INTO knowledge_admission_receipts(id, tenant_id, "
                    "contract_version, qualification_receipt_id, qualification_receipt_hash, "
                    "claim_revision_id, claim_semantic_hash, profile_id, profile_version, "
                    "use_scope, admission_policy_id, admission_policy_version, "
                    "admission_policy_hash, approved_by, rationale, receipt_hash, issued_at) "
                    "VALUES (:id, :tenant_id, :contract_version, :qualification_receipt_id, "
                    ":qualification_receipt_hash, :claim_revision_id, :claim_semantic_hash, "
                    ":profile_id, :profile_version, :use_scope, :admission_policy_id, "
                    ":admission_policy_version, :admission_policy_hash, :approved_by, "
                    ":rationale, :receipt_hash, :issued_at)"
                ),
                admission,
            )
            connection.execute(
                text(
                    "INSERT INTO current_use_bindings(id, tenant_id, claim_revision_id, "
                    "profile_id, use_scope, qualification_receipt_id, "
                    "knowledge_admission_receipt_id, state, stale_reason, bound_by, "
                    "created_at, updated_at) VALUES (:id, :tenant_id, :claim_revision_id, "
                    ":profile_id, :use_scope, :qualification_receipt_id, "
                    ":knowledge_admission_receipt_id, :state, :stale_reason, :bound_by, "
                    ":created_at, :updated_at) ON CONFLICT(tenant_id, claim_revision_id, "
                    "profile_id, use_scope) DO UPDATE SET qualification_receipt_id = "
                    "excluded.qualification_receipt_id, knowledge_admission_receipt_id = "
                    "excluded.knowledge_admission_receipt_id, state = excluded.state, "
                    "stale_reason = excluded.stale_reason, bound_by = excluded.bound_by, "
                    "updated_at = excluded.updated_at"
                ),
                binding,
            )
            row = connection.execute(
                text(
                    "SELECT * FROM current_use_bindings WHERE tenant_id = :tenant_id "
                    "AND claim_revision_id = :claim_revision_id "
                    "AND profile_id = :profile_id AND use_scope = :use_scope"
                ),
                binding,
            ).mappings().one()
        return {
            "knowledge_admission_receipt": _decode_knowledge_admission_receipt(
                admission
            ),
            "current_use_binding": dict(row),
        }

    def list_knowledge_admission_receipts(
        self, tenant_id: str, claim_id: str | None = None
    ) -> list[dict]:
        statement = (
            "SELECT * FROM knowledge_admission_receipts WHERE tenant_id = :tenant_id"
        )
        parameters: dict[str, Any] = {"tenant_id": tenant_id}
        if claim_id is not None:
            statement += " AND claim_revision_id = :claim_id"
            parameters["claim_id"] = claim_id
        statement += " ORDER BY issued_at, id"
        return [
            _decode_knowledge_admission_receipt(row)
            for row in self._many(statement, parameters)
        ]

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
        self,
        tenant_id: str,
        profile_id: str,
        state: str = "current",
        use_scope: str = "knowledge",
    ) -> list[dict]:
        return self._many(
            "SELECT * FROM current_use_bindings WHERE tenant_id = :tenant_id "
            "AND profile_id = :profile_id AND state = :state "
            "AND use_scope = :use_scope ORDER BY updated_at DESC",
            {
                "tenant_id": tenant_id,
                "profile_id": profile_id,
                "state": state,
                "use_scope": use_scope,
            },
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

    def consume_authorization_grant(
        self,
        tenant_id: str,
        actor_id: str,
        action: str,
        target: str,
        used_at: str,
        *,
        grant_id: str | None = None,
    ) -> dict | None:
        """Atomically consume one grant backed by the exact current receipt."""

        from sqlalchemy import text

        parameters = {
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "action": action,
            "target": target,
            "grant_id": grant_id,
        }
        grant_filter = " AND g.id = :grant_id" if grant_id is not None else ""
        with self.engine.begin() as connection:
            rows = connection.execute(
                text(
                    "SELECT g.* FROM authorization_grants g "
                    "JOIN qualification_receipts r ON r.tenant_id = g.tenant_id "
                    "AND r.id = g.qualification_receipt_id "
                    "WHERE g.tenant_id = :tenant_id AND g.actor_id = :actor_id "
                    "AND g.action = :action AND g.target = :target "
                    "AND g.state = 'active' AND g.calls_used < g.max_calls "
                    "AND EXISTS (SELECT 1 FROM current_use_bindings b "
                    "WHERE b.tenant_id = g.tenant_id "
                    "AND b.claim_revision_id = r.claim_revision_id "
                    "AND b.profile_id = r.profile_id "
                    "AND b.qualification_receipt_id = g.qualification_receipt_id "
                    "AND b.state = 'current') "
                    + grant_filter
                    + " "
                    "ORDER BY g.created_at, g.id FOR UPDATE OF g"
                ),
                parameters,
            ).mappings().all()
            for row in rows:
                grant = dict(row)
                if not _authorization_not_expired(grant.get("expires_at"), used_at):
                    connection.execute(
                        text(
                            "UPDATE authorization_grants SET state = 'expired' "
                            "WHERE tenant_id = :tenant_id AND id = :id "
                            "AND state = 'active'"
                        ),
                        {"tenant_id": tenant_id, "id": grant["id"]},
                    )
                    continue
                updated = connection.execute(
                    text(
                        "UPDATE authorization_grants SET calls_used = calls_used + 1 "
                        "WHERE tenant_id = :tenant_id AND id = :id "
                        "AND state = 'active' AND calls_used = :calls_used "
                        "AND calls_used < max_calls RETURNING *"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "id": grant["id"],
                        "calls_used": grant["calls_used"],
                    },
                ).mappings().first()
                if updated is not None:
                    return _decode_authorization_grant(updated)
        return None
