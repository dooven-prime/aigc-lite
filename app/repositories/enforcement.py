"""Relational persistence for the execution authority proposal ledger."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ..core.authority import AuthorityContext
from ..core.contracts import RequestContext
from ..core.errors import AuthorityDeniedError, InvalidExecutionPolicyError
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
    value["signature_verified"] = bool(value.get("signature_verified"))
    value["signature_verification"] = _decode_metadata(
        value.get("signature_verification")
    )
    value["signed_payload"] = _decode_metadata(value.get("signed_payload"))
    return value


def _decode_enforcement_dispatch(row: Any) -> dict[str, Any]:
    return dict(row)


_INVOCATION_MATCH_FIELDS = (
    "grant_id", "grant_call_number", "actor_id", "request_id", "run_id",
    "action", "target", "provider_id", "native_name", "public_name", "source",
    "binding_id", "binding_hash", "adapter_id", "proposal_id", "policy_hash",
    "adapter_target_type", "adapter_target_id", "tool_spec_hash",
    "tool_timeout_seconds",
    "arguments_digest", "invocation_digest",
)


def _checked_invocation_claim(
    invocation: dict[str, Any], tenant_id: str, dispatch_id: str, claimed_at: str
) -> dict[str, Any]:
    return {
        "invocation_id": invocation["id"],
        "tenant_id": tenant_id,
        "dispatch_id": dispatch_id,
        "claimed_at": claimed_at,
        **{field: invocation[field] for field in _INVOCATION_MATCH_FIELDS},
    }


def _assert_reconciliation_root(
    row: dict[str, Any], original: Any, expected: dict[str, Any] | None
) -> None:
    if expected is None or original["original_dispatch_id"] is not None:
        raise InvalidExecutionPolicyError(
            "original_dispatch_id", "Reconciliation requires the exact root dispatch"
        )
    if any(
        original[field] != expected.get(field)
        for field in (
            "id", "tenant_id", "request_hash", "updated_at", "state",
            "receipt_id", "workload_id",
        )
    ) or any(
        original[field] != row[field]
        for field in (
            "adapter_id", "issuer_id", "target_type", "target_id", "run_id",
            "step_id", "proposal_id", "tool_spec_hash", "arguments_digest",
        )
    ):
        raise InvalidExecutionPolicyError(
            "original_dispatch_id", "Reconciliation root changed or does not match"
        )
    if original["state"] not in {"dispatching", "indeterminate"}:
        raise InvalidExecutionPolicyError(
            "original_dispatch_id", "Reconciliation root is already terminal"
        )
    if original["state"] == "dispatching":
        expiry = datetime.fromisoformat(original["expires_at"].replace("Z", "+00:00"))
        if datetime.now(UTC) <= expiry.astimezone(UTC):
            raise InvalidExecutionPolicyError(
                "original_dispatch_id", "Reconciliation root is still active"
            )


def _dispatch_within_frozen_timeout(
    row: dict[str, Any], invocation: dict[str, Any]
) -> bool:
    try:
        start = datetime.fromisoformat(row["requested_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        return (
            start.tzinfo is not None
            and end.tzinfo is not None
            and 0 < (end - start).total_seconds()
            <= float(invocation["tool_timeout_seconds"]) + 0.000001
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def _assert_dispatch_finish(
    dispatch: Any,
    *,
    state: str,
    receipt: Any | None,
    reconciliation: Any | None,
    workload_id: str | None,
    last_error_code: str | None,
) -> None:
    current = dispatch["state"]
    if state == "indeterminate":
        if current != "dispatching":
            raise InvalidExecutionPolicyError(
                "state", "Only an active dispatch can become indeterminate"
            )
        if receipt is None:
            if workload_id is not None:
                raise InvalidExecutionPolicyError(
                    "workload_id", "Unverified observation cannot name a workload"
                )
            return
        if receipt["outcome"] not in {"timed_out", "indeterminate"}:
            raise InvalidExecutionPolicyError(
                "receipt_id", "Receipt outcome is not indeterminate"
            )
    elif state == "terminal":
        if current != "dispatching" or receipt is None or last_error_code is not None:
            raise InvalidExecutionPolicyError(
                "state", "Terminal dispatch requires a verified receipt"
            )
        if receipt["outcome"] in {"timed_out", "indeterminate"}:
            raise InvalidExecutionPolicyError(
                "receipt_id", "Indeterminate receipt cannot release the slot"
            )
    elif state == "reconciled":
        if (
            current not in {"dispatching", "indeterminate"}
            or receipt is None
            or reconciliation is None
            or last_error_code is not None
        ):
            raise InvalidExecutionPolicyError(
                "state", "Reconciliation requires a terminal observation"
            )
        root_id = dispatch["original_dispatch_id"] or dispatch["id"]
        if (
            reconciliation["original_dispatch_id"] != root_id
            or reconciliation["state"] != "terminal"
            or reconciliation["receipt_id"] != receipt["id"]
        ):
            raise InvalidExecutionPolicyError(
                "receipt_id", "Receipt is not from a terminal root reconciliation"
            )
        if workload_id != receipt["workload_id"]:
            raise InvalidExecutionPolicyError(
                "workload_id", "Reconciliation workload does not match receipt"
            )
        return
    else:
        raise InvalidExecutionPolicyError("state", "Unsupported dispatch transition")

    if receipt is None:
        return
    if not bool(receipt["signature_verified"]) or any(
        receipt[receipt_field] != dispatch[dispatch_field]
        for receipt_field, dispatch_field in (
            ("enforcement_request_id", "id"),
            ("enforcement_request_hash", "request_hash"),
            ("run_id", "run_id"),
            ("step_id", "step_id"),
            ("proposal_id", "proposal_id"),
            ("issuer_id", "issuer_id"),
            ("execution_envelope_hash", "execution_envelope_hash"),
            ("tool_spec_hash", "tool_spec_hash"),
            ("arguments_digest", "arguments_digest"),
        )
    ) or workload_id != receipt["workload_id"]:
        raise InvalidExecutionPolicyError(
            "receipt_id", "Verified receipt is not bound to this dispatch"
        )


def _dispatch_row(
    tenant_id: str, values: dict[str, Any], created_at: str
) -> dict[str, Any]:
    return {
        "id": values["id"],
        "tenant_id": tenant_id,
        "contract_version": values["contract_version"],
        "binding_id": values.get("binding_id"),
        "adapter_id": values["adapter_id"],
        "issuer_id": values["issuer_id"],
        "target_type": values["target_type"],
        "target_id": values["target_id"],
        "run_id": values["run_id"],
        "step_id": values.get("step_id"),
        "proposal_id": values["proposal_id"],
        "original_dispatch_id": values.get("original_dispatch_id"),
        "request_hash": values["request_hash"],
        "execution_envelope_hash": values["execution_envelope_hash"],
        "tool_spec_hash": values["tool_spec_hash"],
        "arguments_digest": values["arguments_digest"],
        "requested_at": values["requested_at"],
        "expires_at": values["expires_at"],
        "state": values["state"],
        "receipt_id": values.get("receipt_id"),
        "workload_id": values.get("workload_id"),
        "last_error_code": values.get("last_error_code"),
        "created_at": created_at,
        "updated_at": created_at,
    }


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
        "signature_verified": 1 if values.get("signature_verified") else 0,
        "enforcement_request_id": values.get("enforcement_request_id"),
        "enforcement_request_hash": values.get("enforcement_request_hash"),
        "signature_algorithm": values.get("signature_algorithm"),
        "signing_key_id": values.get("signing_key_id"),
        "signed_payload_hash": values.get("signed_payload_hash"),
        "signature_verified_at": values.get("signature_verified_at"),
        "signature_verifier_id": values.get("signature_verifier_id"),
        "signed_payload": json.dumps(
            values.get("signed_payload") or {}, ensure_ascii=False
        ),
        "signature_verification": json.dumps(
            values.get("signature_verification") or {}, ensure_ascii=False
        ),
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

    def create_enforcement_dispatch(
        self,
        tenant_id: str,
        values: dict[str, Any],
        *,
        context: RequestContext | None = None,
        authority: AuthorityContext | None = None,
        authorization_invocation: dict[str, Any] | None = None,
    ) -> dict:
        row = _dispatch_row(tenant_id, values, utc_now())
        scope = "enforcement:reconcile"
        if (
            row["state"] != "dispatching"
            or row["receipt_id"] is not None
            or row["workload_id"] is not None
            or row["last_error_code"] is not None
        ):
            raise InvalidExecutionPolicyError(
                "state", "New dispatch must begin without a result in dispatching state"
            )
        if row["original_dispatch_id"] is None and authorization_invocation is None:
            raise InvalidExecutionPolicyError(
                "authorization_invocation_id", "Initial dispatch requires a consumed grant"
            )
        if context is None or context.workspace_id != tenant_id:
            raise AuthorityDeniedError(scope)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if authorization_invocation is None:
                self._assert_admin_write_authority(db, context, authority, scope)
            else:
                if row["original_dispatch_id"] is not None or row["binding_id"] is None:
                    raise InvalidExecutionPolicyError(
                        "authorization_invocation_id", "Invalid dispatch handoff"
                    )
                if (
                    row["step_id"] is not None
                    or not _dispatch_within_frozen_timeout(row, authorization_invocation)
                    or authorization_invocation["actor_id"] != context.principal_id
                    or authorization_invocation["request_id"] != context.request_id
                    or authorization_invocation["action"] != authorization_invocation["native_name"]
                    or any(
                        row[field] != authorization_invocation[field]
                        for field in (
                            "run_id", "proposal_id", "adapter_id", "binding_id",
                            "tool_spec_hash", "arguments_digest",
                        )
                    )
                    or row["target_type"] != authorization_invocation["adapter_target_type"]
                    or row["target_id"] != authorization_invocation["adapter_target_id"]
                ):
                    raise InvalidExecutionPolicyError(
                        "authorization_invocation_id", "Dispatch differs from consumed invocation"
                    )
                claim = _checked_invocation_claim(
                    authorization_invocation, tenant_id, row["id"], utc_now()
                )
                matched = db.execute(
                    "UPDATE authorization_invocations SET dispatch_id = ?, claimed_at = ? "
                    "WHERE id = ? AND tenant_id = ? AND dispatch_id IS NULL "
                    "AND claim_expires_at > ? AND "
                    + " AND ".join(f"{field} = ?" for field in _INVOCATION_MATCH_FIELDS),
                    (
                        claim["dispatch_id"], claim["claimed_at"], claim["invocation_id"],
                        tenant_id, claim["claimed_at"],
                        *(claim[field] for field in _INVOCATION_MATCH_FIELDS),
                    ),
                )
                if matched.rowcount != 1:
                    raise InvalidExecutionPolicyError(
                        "authorization_invocation_id",
                        "Consumed grant does not match this dispatch or was already claimed",
                    )
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
            proposal = db.execute(
                "SELECT 1 FROM execution_policy_proposals WHERE tenant_id = ? AND id = ?",
                (tenant_id, row["proposal_id"]),
            ).fetchone()
            if proposal is None:
                raise KeyError(row["proposal_id"])
            if row["original_dispatch_id"] is not None:
                original = db.execute(
                    "SELECT * FROM enforcement_dispatches WHERE tenant_id = ? AND id = ?",
                    (tenant_id, row["original_dispatch_id"]),
                ).fetchone()
                if original is None:
                    raise KeyError(row["original_dispatch_id"])
                _assert_reconciliation_root(
                    row, original, values.get("expected_original_dispatch")
                )
            db.execute(
                "INSERT INTO enforcement_dispatches(id, tenant_id, contract_version, "
                "binding_id, adapter_id, issuer_id, target_type, target_id, run_id, "
                "step_id, proposal_id, original_dispatch_id, request_hash, "
                "execution_envelope_hash, tool_spec_hash, arguments_digest, "
                "requested_at, expires_at, state, "
                "receipt_id, workload_id, last_error_code, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(row.values()),
            )
        return _decode_enforcement_dispatch(row)

    def get_enforcement_dispatch(
        self, tenant_id: str, dispatch_id: str
    ) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM enforcement_dispatches WHERE tenant_id = ? AND id = ?",
                (tenant_id, dispatch_id),
            ).fetchone()
        return _decode_enforcement_dispatch(row) if row is not None else None

    def list_enforcement_dispatches(
        self,
        tenant_id: str,
        *,
        adapter_id: str | None = None,
        proposal_id: str | None = None,
        state: str | None = None,
        original_dispatch_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        statement = "SELECT * FROM enforcement_dispatches WHERE tenant_id = ?"
        parameters: list[Any] = [tenant_id]
        for column, value in (
            ("adapter_id", adapter_id),
            ("proposal_id", proposal_id),
            ("state", state),
            ("original_dispatch_id", original_dispatch_id),
        ):
            if value is not None:
                statement += f" AND {column} = ?"
                parameters.append(value)
        statement += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        parameters.append(max(1, min(limit, 500)))
        with self._connect() as db:
            rows = db.execute(statement, parameters).fetchall()
        return [_decode_enforcement_dispatch(row) for row in rows]

    def finish_enforcement_dispatch(
        self,
        tenant_id: str,
        dispatch_id: str,
        *,
        state: str,
        receipt_id: str | None = None,
        workload_id: str | None = None,
        last_error_code: str | None = None,
    ) -> dict | None:
        updated_at = utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            dispatch = db.execute(
                "SELECT * FROM enforcement_dispatches WHERE tenant_id = ? AND id = ?",
                (tenant_id, dispatch_id),
            ).fetchone()
            if dispatch is None:
                return None
            receipt = (
                db.execute(
                    "SELECT * FROM enforcement_receipts WHERE tenant_id = ? AND id = ?",
                    (tenant_id, receipt_id),
                ).fetchone()
                if receipt_id is not None else None
            )
            if receipt_id is not None and receipt is None:
                raise InvalidExecutionPolicyError("receipt_id", "Receipt does not exist")
            reconciliation = (
                db.execute(
                    "SELECT * FROM enforcement_dispatches WHERE tenant_id = ? "
                    "AND receipt_id = ? AND state = 'terminal' "
                    "AND original_dispatch_id IS NOT NULL",
                    (tenant_id, receipt_id),
                ).fetchone()
                if state == "reconciled" and receipt_id is not None else None
            )
            _assert_dispatch_finish(
                dispatch, state=state, receipt=receipt,
                reconciliation=reconciliation, workload_id=workload_id,
                last_error_code=last_error_code,
            )
            result = db.execute(
                "UPDATE enforcement_dispatches SET state = ?, receipt_id = ?, "
                "workload_id = ?, last_error_code = ?, updated_at = ? "
                "WHERE tenant_id = ? AND id = ? AND state = ?",
                (
                    state,
                    receipt_id,
                    workload_id,
                    last_error_code,
                    updated_at,
                    tenant_id,
                    dispatch_id,
                    dispatch["state"],
                ),
            )
            if result.rowcount != 1:
                raise InvalidExecutionPolicyError("state", "Dispatch transition raced")
            row = db.execute(
                "SELECT * FROM enforcement_dispatches WHERE tenant_id = ? AND id = ?",
                (tenant_id, dispatch_id),
            ).fetchone()
        return _decode_enforcement_dispatch(row) if row is not None else None

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
                "signature_verified, enforcement_request_id, enforcement_request_hash, "
                "signature_algorithm, signing_key_id, signed_payload_hash, "
                "signature_verified_at, signature_verifier_id, signed_payload, "
                "signature_verification, "
                "issued_at, receipt_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?) "
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

    def create_enforcement_dispatch(
        self,
        tenant_id: str,
        values: dict[str, Any],
        *,
        context: RequestContext | None = None,
        authority: AuthorityContext | None = None,
        authorization_invocation: dict[str, Any] | None = None,
    ) -> dict:
        from sqlalchemy import text

        row = _dispatch_row(tenant_id, values, utc_now())
        scope = "enforcement:reconcile"
        if (
            row["state"] != "dispatching"
            or row["receipt_id"] is not None
            or row["workload_id"] is not None
            or row["last_error_code"] is not None
        ):
            raise InvalidExecutionPolicyError(
                "state", "New dispatch must begin without a result in dispatching state"
            )
        if row["original_dispatch_id"] is None and authorization_invocation is None:
            raise InvalidExecutionPolicyError(
                "authorization_invocation_id", "Initial dispatch requires a consumed grant"
            )
        if context is None or context.workspace_id != tenant_id:
            raise AuthorityDeniedError(scope)
        with self.engine.begin() as connection:
            if authorization_invocation is None:
                self._assert_admin_write_authority(connection, context, authority, scope)
            else:
                if row["original_dispatch_id"] is not None or row["binding_id"] is None:
                    raise InvalidExecutionPolicyError(
                        "authorization_invocation_id", "Invalid dispatch handoff"
                    )
                if (
                    row["step_id"] is not None
                    or not _dispatch_within_frozen_timeout(row, authorization_invocation)
                    or authorization_invocation["actor_id"] != context.principal_id
                    or authorization_invocation["request_id"] != context.request_id
                    or authorization_invocation["action"] != authorization_invocation["native_name"]
                    or any(
                        row[field] != authorization_invocation[field]
                        for field in (
                            "run_id", "proposal_id", "adapter_id", "binding_id",
                            "tool_spec_hash", "arguments_digest",
                        )
                    )
                    or row["target_type"] != authorization_invocation["adapter_target_type"]
                    or row["target_id"] != authorization_invocation["adapter_target_id"]
                ):
                    raise InvalidExecutionPolicyError(
                        "authorization_invocation_id", "Dispatch differs from consumed invocation"
                    )
                claim = _checked_invocation_claim(
                    authorization_invocation, tenant_id, row["id"], utc_now()
                )
                matched = connection.execute(
                    text(
                        "UPDATE authorization_invocations SET dispatch_id = :dispatch_id, "
                        "claimed_at = :claimed_at WHERE id = :invocation_id "
                        "AND tenant_id = :tenant_id AND dispatch_id IS NULL "
                        "AND claim_expires_at > :claimed_at AND "
                        + " AND ".join(
                            f"{field} = :{field}" for field in _INVOCATION_MATCH_FIELDS
                        )
                    ),
                    claim,
                )
                if matched.rowcount != 1:
                    raise InvalidExecutionPolicyError(
                        "authorization_invocation_id",
                        "Consumed grant does not match this dispatch or was already claimed",
                    )
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
            proposal = connection.execute(
                text(
                    "SELECT 1 FROM execution_policy_proposals "
                    "WHERE tenant_id = :tenant_id AND id = :proposal_id"
                ),
                row,
            ).first()
            if proposal is None:
                raise KeyError(row["proposal_id"])
            if row["original_dispatch_id"] is not None:
                original = connection.execute(
                    text(
                        "SELECT * FROM enforcement_dispatches "
                        "WHERE tenant_id = :tenant_id AND id = :original_dispatch_id "
                        "FOR UPDATE"
                    ),
                    row,
                ).mappings().first()
                if original is None:
                    raise KeyError(row["original_dispatch_id"])
                _assert_reconciliation_root(
                    row, original, values.get("expected_original_dispatch")
                )
            connection.execute(
                text(
                    "INSERT INTO enforcement_dispatches(id, tenant_id, contract_version, "
                    "binding_id, adapter_id, issuer_id, target_type, target_id, run_id, "
                    "step_id, proposal_id, original_dispatch_id, request_hash, "
                    "execution_envelope_hash, tool_spec_hash, arguments_digest, "
                    "requested_at, expires_at, state, "
                    "receipt_id, workload_id, last_error_code, created_at, updated_at) "
                    "VALUES (:id, :tenant_id, :contract_version, :binding_id, :adapter_id, "
                    ":issuer_id, :target_type, :target_id, :run_id, :step_id, :proposal_id, "
                    ":original_dispatch_id, :request_hash, :execution_envelope_hash, "
                    ":tool_spec_hash, :arguments_digest, :requested_at, :expires_at, :state, "
                    ":receipt_id, :workload_id, "
                    ":last_error_code, :created_at, :updated_at)"
                ),
                row,
            )
        return _decode_enforcement_dispatch(row)

    def get_enforcement_dispatch(
        self, tenant_id: str, dispatch_id: str
    ) -> dict | None:
        row = self._one(
            "SELECT * FROM enforcement_dispatches "
            "WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": dispatch_id},
        )
        return _decode_enforcement_dispatch(row) if row is not None else None

    def list_enforcement_dispatches(
        self,
        tenant_id: str,
        *,
        adapter_id: str | None = None,
        proposal_id: str | None = None,
        state: str | None = None,
        original_dispatch_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        statement = "SELECT * FROM enforcement_dispatches WHERE tenant_id = :tenant_id"
        values: dict[str, Any] = {
            "tenant_id": tenant_id,
            "limit": max(1, min(limit, 500)),
        }
        for column, value in (
            ("adapter_id", adapter_id),
            ("proposal_id", proposal_id),
            ("state", state),
            ("original_dispatch_id", original_dispatch_id),
        ):
            if value is not None:
                statement += f" AND {column} = :{column}"
                values[column] = value
        statement += " ORDER BY updated_at DESC, id DESC LIMIT :limit"
        return [
            _decode_enforcement_dispatch(row)
            for row in self._many(statement, values)
        ]

    def finish_enforcement_dispatch(
        self,
        tenant_id: str,
        dispatch_id: str,
        *,
        state: str,
        receipt_id: str | None = None,
        workload_id: str | None = None,
        last_error_code: str | None = None,
    ) -> dict | None:
        from sqlalchemy import text

        values = {
            "tenant_id": tenant_id,
            "id": dispatch_id,
            "state": state,
            "receipt_id": receipt_id,
            "workload_id": workload_id,
            "last_error_code": last_error_code,
            "updated_at": utc_now(),
        }
        with self.engine.begin() as connection:
            dispatch = connection.execute(
                text(
                    "SELECT * FROM enforcement_dispatches "
                    "WHERE tenant_id = :tenant_id AND id = :id FOR UPDATE"
                ),
                values,
            ).mappings().first()
            if dispatch is None:
                return None
            receipt = (
                connection.execute(
                    text(
                        "SELECT * FROM enforcement_receipts "
                        "WHERE tenant_id = :tenant_id AND id = :receipt_id"
                    ),
                    values,
                ).mappings().first()
                if receipt_id is not None else None
            )
            if receipt_id is not None and receipt is None:
                raise InvalidExecutionPolicyError("receipt_id", "Receipt does not exist")
            reconciliation = (
                connection.execute(
                    text(
                        "SELECT * FROM enforcement_dispatches "
                        "WHERE tenant_id = :tenant_id AND receipt_id = :receipt_id "
                        "AND state = 'terminal' AND original_dispatch_id IS NOT NULL"
                    ),
                    values,
                ).mappings().first()
                if state == "reconciled" and receipt_id is not None else None
            )
            _assert_dispatch_finish(
                dispatch, state=state, receipt=receipt,
                reconciliation=reconciliation, workload_id=workload_id,
                last_error_code=last_error_code,
            )
            values["previous_state"] = dispatch["state"]
            result = connection.execute(
                text(
                    "UPDATE enforcement_dispatches SET state = :state, "
                    "receipt_id = :receipt_id, workload_id = :workload_id, "
                    "last_error_code = :last_error_code, updated_at = :updated_at "
                    "WHERE tenant_id = :tenant_id AND id = :id "
                    "AND state = :previous_state"
                ),
                values,
            )
            if result.rowcount != 1:
                raise InvalidExecutionPolicyError("state", "Dispatch transition raced")
            row = connection.execute(
                text(
                    "SELECT * FROM enforcement_dispatches "
                    "WHERE tenant_id = :tenant_id AND id = :id"
                ),
                values,
            ).mappings().first()
        return _decode_enforcement_dispatch(row) if row is not None else None

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
                    "signature_verified, enforcement_request_id, enforcement_request_hash, "
                    "signature_algorithm, signing_key_id, signed_payload_hash, "
                    "signature_verified_at, signature_verifier_id, signed_payload, "
                    "signature_verification, "
                    "issued_at, receipt_hash, created_at) VALUES (:id, :tenant_id, "
                    ":contract_version, :run_id, :step_id, :proposal_id, :issuer_id, "
                    ":backend_id, :enforcement_identity, :trust_domain, :attestation_type, "
                    ":policy_id, :policy_revision, :policy_hash, :execution_envelope_hash, "
                    ":tool_spec_hash, :arguments_digest, :decision, :outcome, :observed_effects, "
                    ":denied_effects, :credential_bindings, :image_digest, :toolchain_digest, "
                    ":sandbox_id, :workload_id, :external_signature, :attestation, "
                    ":signature_verified, :enforcement_request_id, :enforcement_request_hash, "
                    ":signature_algorithm, :signing_key_id, :signed_payload_hash, "
                    ":signature_verified_at, :signature_verifier_id, :signed_payload, "
                    ":signature_verification, "
                    ":issued_at, "
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
