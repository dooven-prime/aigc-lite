"""Relational persistence for the execution authority proposal ledger."""

from __future__ import annotations

import json
from typing import Any

from .common import decode_list as _decode_list
from .common import decode_metadata as _decode_metadata
from .common import utc_now


def _decode_policy_proposal(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["base_policy_revision"] = int(value.get("base_policy_revision") or 0)
    value["candidate_policy_revision"] = int(
        value.get("candidate_policy_revision") or 0
    )
    value["expands_authority"] = bool(value.get("expands_authority"))
    value["expansion_count"] = int(value.get("expansion_count") or 0)
    value["reduction_count"] = int(value.get("reduction_count") or 0)
    value["base_policy"] = _decode_metadata(value.get("base_policy"))
    value["candidate_policy"] = _decode_metadata(value.get("candidate_policy"))
    value["permission_diff"] = _decode_metadata(value.get("permission_diff"))
    return value


def _decode_enforcement_receipt(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["policy_revision"] = int(value.get("policy_revision") or 0)
    value["observed_effects"] = _decode_list(value.get("observed_effects"))
    value["denied_effects"] = _decode_list(value.get("denied_effects"))
    value["credential_bindings"] = _decode_list(value.get("credential_bindings"))
    value["attestation"] = _decode_metadata(value.get("attestation"))
    return value


def _proposal_row(
    tenant_id: str, values: dict[str, Any], created_at: str
) -> dict[str, Any]:
    return {
        "id": values["id"],
        "tenant_id": tenant_id,
        "contract_version": values["contract_version"],
        "target_type": values["target_type"],
        "target_id": values["target_id"],
        "state": values["state"],
        "base_policy_id": values["base_policy_id"],
        "base_policy_revision": int(values["base_policy_revision"]),
        "base_policy_hash": values["base_policy_hash"],
        "candidate_policy_id": values["candidate_policy_id"],
        "candidate_policy_revision": int(values["candidate_policy_revision"]),
        "candidate_policy_hash": values["candidate_policy_hash"],
        "base_policy": json.dumps(values["base_policy"], ensure_ascii=False),
        "candidate_policy": json.dumps(values["candidate_policy"], ensure_ascii=False),
        "permission_diff": json.dumps(values["permission_diff"], ensure_ascii=False),
        "diff_hash": values["diff_hash"],
        "expands_authority": 1 if values["expands_authority"] else 0,
        "expansion_count": int(values["expansion_count"]),
        "reduction_count": int(values["reduction_count"]),
        "rationale": values.get("rationale") or "",
        "requested_by": values.get("requested_by"),
        "created_at": created_at,
    }


def _receipt_row(
    tenant_id: str, values: dict[str, Any], created_at: str
) -> dict[str, Any]:
    return {
        "id": values["id"],
        "tenant_id": tenant_id,
        "contract_version": values["contract_version"],
        "run_id": values["run_id"],
        "step_id": values.get("step_id"),
        "proposal_id": values.get("proposal_id"),
        "issuer_id": values["issuer_id"],
        "backend_id": values["backend_id"],
        "enforcement_identity": values["enforcement_identity"],
        "trust_domain": values["trust_domain"],
        "attestation_type": values["attestation_type"],
        "policy_id": values["policy_id"],
        "policy_revision": int(values["policy_revision"]),
        "policy_hash": values["policy_hash"],
        "execution_envelope_hash": values["execution_envelope_hash"],
        "tool_spec_hash": values["tool_spec_hash"],
        "arguments_digest": values["arguments_digest"],
        "decision": values["decision"],
        "outcome": values["outcome"],
        "observed_effects": json.dumps(
            values.get("observed_effects") or [], ensure_ascii=False
        ),
        "denied_effects": json.dumps(
            values.get("denied_effects") or [], ensure_ascii=False
        ),
        "credential_bindings": json.dumps(
            values.get("credential_bindings") or [], ensure_ascii=False
        ),
        "image_digest": values.get("image_digest"),
        "toolchain_digest": values.get("toolchain_digest"),
        "sandbox_id": values.get("sandbox_id"),
        "workload_id": values.get("workload_id"),
        "external_signature": values.get("external_signature"),
        "attestation": json.dumps(values.get("attestation") or {}, ensure_ascii=False),
        "issued_at": values["issued_at"],
        "receipt_hash": values["receipt_hash"],
        "created_at": created_at,
    }


class SQLiteEnforcementRepositoryMixin:
    def create_policy_proposal(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        row = _proposal_row(tenant_id, values, utc_now())
        with self._connect() as db:
            db.execute(
                "INSERT INTO execution_policy_proposals(id, tenant_id, contract_version, "
                "target_type, target_id, state, base_policy_id, base_policy_revision, "
                "base_policy_hash, candidate_policy_id, candidate_policy_revision, "
                "candidate_policy_hash, base_policy, candidate_policy, permission_diff, "
                "diff_hash, expands_authority, expansion_count, reduction_count, rationale, "
                "requested_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(row.values()),
            )
        return _decode_policy_proposal(row)

    def get_policy_proposal(
        self, tenant_id: str, proposal_id: str
    ) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM execution_policy_proposals "
                "WHERE tenant_id = ? AND id = ?",
                (tenant_id, proposal_id),
            ).fetchone()
        return _decode_policy_proposal(row) if row is not None else None

    def list_policy_proposals(
        self,
        tenant_id: str,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        statement = "SELECT * FROM execution_policy_proposals WHERE tenant_id = ?"
        parameters: list[Any] = [tenant_id]
        for column, value in (("target_type", target_type), ("target_id", target_id)):
            if value is not None:
                statement += f" AND {column} = ?"
                parameters.append(value)
        statement += " ORDER BY created_at DESC, id DESC LIMIT ?"
        parameters.append(max(1, min(limit, 200)))
        with self._connect() as db:
            rows = db.execute(statement, parameters).fetchall()
        return [_decode_policy_proposal(row) for row in rows]

    def create_enforcement_receipt(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        row = _receipt_row(tenant_id, values, utc_now())
        with self._connect() as db:
            owned_run = db.execute(
                "SELECT 1 FROM agent_runs WHERE tenant_id = ? AND id = ?",
                (tenant_id, row["run_id"]),
            ).fetchone()
            if owned_run is None:
                raise KeyError(row["run_id"])
            if row["step_id"] is not None:
                owned_step = db.execute(
                    "SELECT 1 FROM run_steps WHERE tenant_id = ? AND id = ? AND run_id = ?",
                    (tenant_id, row["step_id"], row["run_id"]),
                ).fetchone()
                if owned_step is None:
                    raise KeyError(row["step_id"])
            if row["proposal_id"] is not None:
                proposal = db.execute(
                    "SELECT candidate_policy_hash FROM execution_policy_proposals "
                    "WHERE tenant_id = ? AND id = ?",
                    (tenant_id, row["proposal_id"]),
                ).fetchone()
                if proposal is None or proposal["candidate_policy_hash"] != row["policy_hash"]:
                    raise KeyError(row["proposal_id"])
            db.execute(
                "INSERT INTO enforcement_receipts(id, tenant_id, contract_version, run_id, "
                "step_id, proposal_id, issuer_id, backend_id, enforcement_identity, "
                "trust_domain, attestation_type, policy_id, policy_revision, policy_hash, "
                "execution_envelope_hash, tool_spec_hash, arguments_digest, decision, outcome, "
                "observed_effects, denied_effects, credential_bindings, image_digest, "
                "toolchain_digest, sandbox_id, workload_id, external_signature, attestation, "
                "issued_at, receipt_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, receipt_hash) DO NOTHING",
                tuple(row.values()),
            )
            stored = db.execute(
                "SELECT * FROM enforcement_receipts WHERE tenant_id = ? AND receipt_hash = ?",
                (tenant_id, row["receipt_hash"]),
            ).fetchone()
        assert stored is not None
        result = _decode_enforcement_receipt(stored)
        result["deduplicated"] = result["id"] != row["id"]
        return result

    def get_enforcement_receipt(
        self, tenant_id: str, receipt_id: str
    ) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM enforcement_receipts WHERE tenant_id = ? AND id = ?",
                (tenant_id, receipt_id),
            ).fetchone()
        return _decode_enforcement_receipt(row) if row is not None else None

    def list_enforcement_receipts(
        self,
        tenant_id: str,
        *,
        run_id: str | None = None,
        proposal_id: str | None = None,
        backend_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        statement = "SELECT * FROM enforcement_receipts WHERE tenant_id = ?"
        parameters: list[Any] = [tenant_id]
        for column, value in (
            ("run_id", run_id),
            ("proposal_id", proposal_id),
            ("backend_id", backend_id),
        ):
            if value is not None:
                statement += f" AND {column} = ?"
                parameters.append(value)
        statement += " ORDER BY issued_at DESC, id DESC LIMIT ?"
        parameters.append(max(1, min(limit, 500)))
        with self._connect() as db:
            rows = db.execute(statement, parameters).fetchall()
        return [_decode_enforcement_receipt(row) for row in rows]


class PostgresEnforcementRepositoryMixin:
    def create_policy_proposal(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        from sqlalchemy import text

        row = _proposal_row(tenant_id, values, utc_now())
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO execution_policy_proposals(id, tenant_id, contract_version, "
                    "target_type, target_id, state, base_policy_id, base_policy_revision, "
                    "base_policy_hash, candidate_policy_id, candidate_policy_revision, "
                    "candidate_policy_hash, base_policy, candidate_policy, permission_diff, "
                    "diff_hash, expands_authority, expansion_count, reduction_count, rationale, "
                    "requested_by, created_at) VALUES (:id, :tenant_id, :contract_version, "
                    ":target_type, :target_id, :state, :base_policy_id, "
                    ":base_policy_revision, :base_policy_hash, :candidate_policy_id, "
                    ":candidate_policy_revision, :candidate_policy_hash, :base_policy, "
                    ":candidate_policy, :permission_diff, :diff_hash, :expands_authority, "
                    ":expansion_count, :reduction_count, :rationale, :requested_by, :created_at)"
                ),
                row,
            )
        return _decode_policy_proposal(row)

    def get_policy_proposal(
        self, tenant_id: str, proposal_id: str
    ) -> dict | None:
        row = self._one(
            "SELECT * FROM execution_policy_proposals "
            "WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": proposal_id},
        )
        return _decode_policy_proposal(row) if row is not None else None

    def list_policy_proposals(
        self,
        tenant_id: str,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        statement = "SELECT * FROM execution_policy_proposals WHERE tenant_id = :tenant_id"
        values: dict[str, Any] = {"tenant_id": tenant_id, "limit": max(1, min(limit, 200))}
        for column, value in (("target_type", target_type), ("target_id", target_id)):
            if value is not None:
                statement += f" AND {column} = :{column}"
                values[column] = value
        statement += " ORDER BY created_at DESC, id DESC LIMIT :limit"
        return [_decode_policy_proposal(row) for row in self._many(statement, values)]

    def create_enforcement_receipt(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict:
        from sqlalchemy import text

        row = _receipt_row(tenant_id, values, utc_now())
        with self.engine.begin() as connection:
            owned_run = connection.execute(
                text(
                    "SELECT 1 FROM agent_runs WHERE tenant_id = :tenant_id AND id = :run_id"
                ),
                row,
            ).first()
            if owned_run is None:
                raise KeyError(row["run_id"])
            if row["step_id"] is not None:
                owned_step = connection.execute(
                    text(
                        "SELECT 1 FROM run_steps WHERE tenant_id = :tenant_id AND id = :step_id "
                        "AND run_id = :run_id"
                    ),
                    row,
                ).first()
                if owned_step is None:
                    raise KeyError(row["step_id"])
            if row["proposal_id"] is not None:
                proposal = connection.execute(
                    text(
                        "SELECT candidate_policy_hash FROM execution_policy_proposals "
                        "WHERE tenant_id = :tenant_id AND id = :proposal_id"
                    ),
                    row,
                ).mappings().first()
                if proposal is None or proposal["candidate_policy_hash"] != row["policy_hash"]:
                    raise KeyError(row["proposal_id"])
            connection.execute(
                text(
                    "INSERT INTO enforcement_receipts(id, tenant_id, contract_version, run_id, "
                    "step_id, proposal_id, issuer_id, backend_id, enforcement_identity, "
                    "trust_domain, attestation_type, policy_id, policy_revision, policy_hash, "
                    "execution_envelope_hash, tool_spec_hash, arguments_digest, decision, outcome, "
                    "observed_effects, denied_effects, credential_bindings, image_digest, "
                    "toolchain_digest, sandbox_id, workload_id, external_signature, attestation, "
                    "issued_at, receipt_hash, created_at) VALUES (:id, :tenant_id, "
                    ":contract_version, :run_id, :step_id, :proposal_id, :issuer_id, "
                    ":backend_id, :enforcement_identity, :trust_domain, :attestation_type, "
                    ":policy_id, :policy_revision, :policy_hash, :execution_envelope_hash, "
                    ":tool_spec_hash, :arguments_digest, :decision, :outcome, :observed_effects, "
                    ":denied_effects, :credential_bindings, :image_digest, :toolchain_digest, "
                    ":sandbox_id, :workload_id, :external_signature, :attestation, :issued_at, "
                    ":receipt_hash, :created_at) ON CONFLICT(tenant_id, receipt_hash) DO NOTHING"
                ),
                row,
            )
            stored = connection.execute(
                text(
                    "SELECT * FROM enforcement_receipts WHERE tenant_id = :tenant_id "
                    "AND receipt_hash = :receipt_hash"
                ),
                row,
            ).mappings().one()
        result = _decode_enforcement_receipt(stored)
        result["deduplicated"] = result["id"] != row["id"]
        return result

    def get_enforcement_receipt(
        self, tenant_id: str, receipt_id: str
    ) -> dict | None:
        row = self._one(
            "SELECT * FROM enforcement_receipts WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": receipt_id},
        )
        return _decode_enforcement_receipt(row) if row is not None else None

    def list_enforcement_receipts(
        self,
        tenant_id: str,
        *,
        run_id: str | None = None,
        proposal_id: str | None = None,
        backend_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        statement = "SELECT * FROM enforcement_receipts WHERE tenant_id = :tenant_id"
        values: dict[str, Any] = {"tenant_id": tenant_id, "limit": max(1, min(limit, 500))}
        for column, value in (
            ("run_id", run_id),
            ("proposal_id", proposal_id),
            ("backend_id", backend_id),
        ):
            if value is not None:
                statement += f" AND {column} = :{column}"
                values[column] = value
        statement += " ORDER BY issued_at DESC, id DESC LIMIT :limit"
        return [_decode_enforcement_receipt(row) for row in self._many(statement, values)]
