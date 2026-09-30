from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import database, main
from app.core.contracts import RequestContext
from app.core.enforcement import (
    EnforcementDecision,
    EnforcementIssuer,
    EnforcementOutcome,
    EnforcementReceiptDraft,
    EnforcementTargetType,
    EnforcementTrustDomain,
    ExecutionPolicySnapshot,
    PermissionDomain,
    PolicyPermission,
    calculate_permission_diff,
)
from app.core.errors import InvalidExecutionPolicyError, ResourceNotFoundError
from app.repository import SQLiteRepository
from app.services.enforcement import EnforcementIssuerRegistry, EnforcementService

HASH_A = "a" * 64
HASH_B = "b" * 64


def _repository(tmp_path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "enforcement.db")
    repository.init()
    return repository


def _policy(revision: int = 1) -> ExecutionPolicySnapshot:
    return ExecutionPolicySnapshot(
        policy_id="sandbox.default",
        revision=revision,
        permissions=(
            PolicyPermission(
                domain=PermissionDomain.NETWORK,
                resource="https://api.example.test:443",
                actions=("connect",),
                constraints={"methods": ["POST"]},
            ),
        ),
        limits={"wall_time_seconds": 30},
    )


def _run(repository: SQLiteRepository, workspace_id: str) -> tuple[dict, dict]:
    session = repository.create_session(workspace_id, "Enforced run")
    run = repository.create_run(
        workspace_id, session["id"], f"request-{workspace_id}", None, "test-model"
    )
    step = repository.append_run_step(
        workspace_id,
        run["id"],
        1,
        "tool",
        "external_tool",
        "succeeded",
        "{}",
        "{}",
        {},
    )
    repository.finish_run(workspace_id, run["id"], "succeeded")
    return run, step


def test_permission_diff_is_deterministic_and_conservative() -> None:
    base = ExecutionPolicySnapshot(
        policy_id="sandbox.default",
        revision=3,
        permissions=(
            PolicyPermission(
                PermissionDomain.FILESYSTEM,
                "/workspace",
                ("read",),
                {"follow_symlinks": False},
            ),
            PolicyPermission(
                PermissionDomain.PROCESS,
                "/usr/bin/git",
                ("execute",),
            ),
        ),
        limits={"wall_time_seconds": 30},
    )
    candidate = ExecutionPolicySnapshot(
        policy_id="sandbox.default",
        revision=4,
        permissions=(
            PolicyPermission(
                PermissionDomain.NETWORK,
                "https://api.example.test:443",
                ("connect",),
            ),
            PolicyPermission(
                PermissionDomain.FILESYSTEM,
                "/workspace",
                ("write", "read"),
                {"follow_symlinks": True},
            ),
        ),
        limits={"wall_time_seconds": 60, "max_output_bytes": 1000},
    )

    value = calculate_permission_diff(base, candidate).as_dict()

    assert value["expands_authority"] is True
    assert value["expansion_count"] == 4
    assert value["reduction_count"] == 2
    assert [item["change_type"] for item in value["changes"]] == [
        "actions_added",
        "constraints_changed",
        "permission_added",
        "permission_removed",
        "limit_added",
        "limit_increased",
    ]
    assert value == calculate_permission_diff(base, candidate).as_dict()
    assert len(value["diff_hash"]) == 64


def test_policy_proposal_is_server_diffed_immutable_and_tenant_scoped(tmp_path) -> None:
    repository = _repository(tmp_path)
    service = EnforcementService(lambda: repository)
    alpha = RequestContext(
        request_id="proposal-a",
        workspace_id="workspace-a",
        principal_id="admin-a",
    )
    beta = RequestContext(request_id="proposal-b", workspace_id="workspace-b")

    proposal = service.propose_policy(
        alpha,
        target_type=EnforcementTargetType.TOOL_EXECUTION_BACKEND,
        target_id="bounded-process",
        candidate_policy=_policy(),
        rationale="Allow one endpoint for the provider.",
    )

    assert proposal["state"] == "proposed"
    assert proposal["requested_by"] == "admin-a"
    assert proposal["base_policy_revision"] == 0
    assert proposal["candidate_policy_revision"] == 1
    assert proposal["expands_authority"] is True
    assert proposal["permission_diff"]["diff_hash"] == proposal["diff_hash"]
    assert service.get_policy_proposal(alpha, proposal["id"]) == proposal
    assert service.list_policy_proposals(beta) == []
    with pytest.raises(ResourceNotFoundError):
        service.get_policy_proposal(beta, proposal["id"])

    with sqlite3.connect(repository.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE execution_policy_proposals SET state = 'proposed' WHERE id = ?",
                (proposal["id"],),
            )

    duplicate = ExecutionPolicySnapshot(
        policy_id="sandbox.default",
        revision=1,
        permissions=(_policy().permissions[0], _policy().permissions[0]),
    )
    with pytest.raises(InvalidExecutionPolicyError, match="must be unique"):
        service.propose_policy(
            alpha,
            target_type=EnforcementTargetType.TOOL_EXECUTION_BACKEND,
            target_id="bounded-process",
            candidate_policy=duplicate,
        )


def test_registered_backend_records_immutable_deduplicated_receipt(tmp_path) -> None:
    repository = _repository(tmp_path)
    issuer = EnforcementIssuer(
        issuer_id="openshell.local",
        backend_id="openshell",
        identity="spiffe://example.test/enforcer/openshell",
        trust_domain=EnforcementTrustDomain.EXTERNAL_RUNTIME,
        attestation_type="signed-runtime-report",
    )
    service = EnforcementService(
        lambda: repository, EnforcementIssuerRegistry([issuer])
    )
    context = RequestContext(
        request_id="proposal",
        workspace_id="workspace-a",
        principal_id="admin-a",
    )
    proposal = service.propose_policy(
        context,
        target_type=EnforcementTargetType.TOOL_EXECUTION_BACKEND,
        target_id="openshell",
        candidate_policy=_policy(),
    )
    run, step = _run(repository, context.workspace_id)
    draft = EnforcementReceiptDraft(
        run_id=run["id"],
        step_id=step["id"],
        proposal_id=proposal["id"],
        policy_id=proposal["candidate_policy_id"],
        policy_revision=proposal["candidate_policy_revision"],
        policy_hash=proposal["candidate_policy_hash"],
        execution_envelope={"run_id": run["id"], "tool": "external_tool"},
        tool_spec_hash=HASH_A,
        arguments_digest=HASH_B,
        decision=EnforcementDecision.ALLOW,
        outcome=EnforcementOutcome.SUCCEEDED,
        observed_effects=({"domain": "network", "host": "api.example.test"},),
        credential_bindings=({"credential_reference": "ref", "token": "secret"},),
        sandbox_id="sandbox-1",
        workload_id="workload-1",
        external_signature="signature-value",
        attestation={"report_digest": "c" * 64},
        issued_at="2026-09-30T10:00:00+08:00",
    )

    receipt = service.record_receipt(
        context.workspace_id, issuer_id=issuer.issuer_id, draft=draft
    )

    assert receipt["backend_id"] == "openshell"
    assert receipt["enforcement_identity"] == issuer.identity
    assert receipt["trust_domain"] == "external_runtime"
    assert receipt["issued_at"] == "2026-09-30T02:00:00+00:00"
    assert receipt["credential_bindings"][0]["token"] == "***"
    assert len(receipt["execution_envelope_hash"]) == 64
    assert len(receipt["receipt_hash"]) == 64
    assert receipt["deduplicated"] is False

    duplicate = service.record_receipt(
        context.workspace_id, issuer_id=issuer.issuer_id, draft=draft
    )
    assert duplicate["id"] == receipt["id"]
    assert duplicate["deduplicated"] is True
    assert len(service.list_receipts(context, run_id=run["id"])) == 1

    with sqlite3.connect(repository.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM enforcement_receipts WHERE id = ?", (receipt["id"],)
            )


def test_receipt_rejects_unknown_issuer_and_proposal_substitution(tmp_path) -> None:
    repository = _repository(tmp_path)
    issuer = EnforcementIssuer(
        issuer_id="runtime",
        backend_id="runtime",
        identity="runtime-instance-1",
        trust_domain=EnforcementTrustDomain.EXTERNAL_RUNTIME,
    )
    service = EnforcementService(
        lambda: repository, EnforcementIssuerRegistry([issuer])
    )
    context = RequestContext(request_id="x", workspace_id="workspace-a")
    proposal = service.propose_policy(
        context,
        target_type=EnforcementTargetType.TOOL_EXECUTION_BACKEND,
        target_id="runtime",
        candidate_policy=_policy(),
    )
    run, _step = _run(repository, context.workspace_id)
    draft = EnforcementReceiptDraft(
        run_id=run["id"],
        proposal_id=proposal["id"],
        policy_id="sandbox.default",
        policy_revision=1,
        policy_hash="f" * 64,
        execution_envelope={"run_id": run["id"]},
        tool_spec_hash=HASH_A,
        arguments_digest=HASH_B,
        decision=EnforcementDecision.DENY,
        outcome=EnforcementOutcome.DENIED,
        issued_at="2026-09-30T00:00:00Z",
    )
    with pytest.raises(ResourceNotFoundError):
        service.record_receipt(
            context.workspace_id, issuer_id="untrusted-model", draft=draft
        )
    with pytest.raises(InvalidExecutionPolicyError, match="frozen proposal"):
        service.record_receipt(
            context.workspace_id, issuer_id=issuer.issuer_id, draft=draft
        )


def test_enforcement_http_exposes_proposals_and_read_only_receipts(
    tmp_path, monkeypatch
) -> None:
    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "enforcement-api.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    with TestClient(main.app) as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "email": "enforcer@example.test",
                "password": "enforcement-pass-123",
                "name": "Enforcer",
                "workspace_name": "Enforcement workspace",
            },
        )
        headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
        payload = {
            "target_type": "tool_execution_backend",
            "target_id": "bounded-process",
            "candidate_policy": {
                "policy_id": "sandbox.default",
                "revision": 1,
                "permissions": [
                    {
                        "domain": "network",
                        "resource": "https://api.example.test:443",
                        "actions": ["connect"],
                    }
                ],
                "limits": {"wall_time_seconds": 30},
            },
        }
        created = client.post(
            "/api/enforcement/policy-proposals", headers=headers, json=payload
        )
        assert created.status_code == 201
        proposal = created.json()
        assert proposal["permission_diff"]["expands_authority"] is True
        assert client.get(
            f"/api/enforcement/policy-proposals/{proposal['id']}", headers=headers
        ).json() == proposal
        assert len(
            client.get(
                "/api/enforcement/policy-proposals", headers=headers
            ).json()
        ) == 1
        assert client.get("/api/enforcement/receipts", headers=headers).json() == []
        assert client.get("/api/enforcement/issuers", headers=headers).json() == []

        hostile = {**payload, "permission_diff": {"expands_authority": False}}
        assert client.post(
            "/api/enforcement/policy-proposals", headers=headers, json=hostile
        ).status_code == 422

    methods = main.app.openapi()["paths"]["/api/enforcement/receipts"]
    assert "get" in methods
    assert "post" not in methods
