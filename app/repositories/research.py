"""Research registry relational repository implementations."""

from __future__ import annotations

import json
import uuid
from typing import Any

from .common import decode_list as _decode_list
from .common import decode_metadata as _decode_metadata
from .common import utc_now


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


class SQLiteResearchRepositoryMixin:
    """SQLite implementation of the research repository port."""

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


class PostgresResearchRepositoryMixin:
    """PostgreSQL implementation of the research repository port."""

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
