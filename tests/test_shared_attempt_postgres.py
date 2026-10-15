"""PostgreSQL authority ordering for one proof selected by two receipts."""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from authority_support import provision_admin
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError

from app.core.contracts import RequestContext
from app.core.errors import InvalidEvidenceError
from app.repository import PostgresRepository

POSTGRES_URL = os.getenv("AIGC_LITE_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="AIGC_LITE_TEST_POSTGRES_URL is not configured"
)


def _seed() -> tuple[PostgresRepository, RequestContext, object, dict, dict]:
    repository = PostgresRepository(POSTGRES_URL)
    repository.init()
    workspace_id = f"shared-attempt-{uuid4()}"
    context, authority = provision_admin(
        repository, RequestContext(str(uuid4()), workspace_id, "admin")
    )
    claim_id, attempt_id = str(uuid4()), str(uuid4())
    receipt_a, receipt_b = str(uuid4()), str(uuid4())
    now = datetime.now(UTC).isoformat()
    with repository.engine.begin() as connection:
        for receipt_id in (receipt_a, receipt_b):
            evaluation_id = str(uuid4())
            connection.execute(
                text(
                    "INSERT INTO qualification_evaluations(id, tenant_id, claim_revision_id, "
                    "claim_semantic_hash, profile_id, profile_version, profile_hash, "
                    "profile_snapshot, evidence_closure_hash, evidence_closure, policy_version, "
                    "policy_hash, verdict, criteria, blockers, evidence_vector, "
                    "independence_summary, evaluated_by, created_at) "
                    "VALUES (:id, :tenant_id, :claim_id, :semantic_hash, 'math.formal.v1', "
                    "1, :profile_hash, '{}', :closure_hash, '{\"nodes\":[]}', "
                    "'math-policy-v1', :policy_hash, 'ADMITTED', '[]', '[]', '{}', '{}', "
                    "NULL, :created_at)"
                ),
                {
                    "id": evaluation_id, "tenant_id": workspace_id,
                    "claim_id": claim_id, "semantic_hash": "a" * 64,
                    "profile_hash": "9" * 64, "closure_hash": "b" * 64,
                    "policy_hash": "c" * 64, "created_at": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO qualification_receipts(id, tenant_id, evaluation_id, "
                    "claim_revision_id, claim_semantic_hash, profile_id, profile_version, "
                    "evidence_closure_hash, policy_version, policy_hash, verdict, criteria, "
                    "blockers, evidence_vector, independence_summary, receipt_hash, issued_at) "
                    "VALUES (:id, :tenant_id, :evaluation_id, :claim_id, :semantic_hash, "
                    "'math.formal.v1', 1, :closure_hash, 'math-policy-v1', :policy_hash, "
                    "'ADMITTED', '[]', '[]', '{}', '{}', :receipt_hash, :issued_at)"
                ),
                {
                    "id": receipt_id, "tenant_id": workspace_id,
                    "evaluation_id": evaluation_id, "claim_id": claim_id,
                    "semantic_hash": "a" * 64, "closure_hash": "b" * 64,
                    "policy_hash": "c" * 64, "receipt_hash": uuid4().hex * 2,
                    "issued_at": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO qualification_receipt_attempt_selections "
                    "(tenant_id, qualification_receipt_id, attempt_id, selection_version) "
                    "VALUES (:tenant_id, :receipt_id, :attempt_id, "
                    "'statement-identity-kernel-attempt.v1')"
                ),
                {"tenant_id": workspace_id, "receipt_id": receipt_id,
                 "attempt_id": attempt_id},
            )
    admission = repository.admit_knowledge(
        workspace_id,
        {
            "id": str(uuid4()), "contract_version": "knowledge.admission.v1",
            "qualification_receipt_id": receipt_b,
            "qualification_receipt_hash": repository.get_qualification_receipt(
                workspace_id, receipt_b
            )["receipt_hash"],
            "claim_revision_id": claim_id, "claim_semantic_hash": "a" * 64,
            "profile_id": "math.formal.v1", "profile_version": 1,
            "use_scope": "knowledge", "admission_policy_id": "knowledge.default.v1",
            "admission_policy_version": 1, "admission_policy_hash": "d" * 64,
            "approved_by": context.principal_id, "rationale": "Synthetic lock-order fixture.",
            "receipt_hash": uuid4().hex * 2, "issued_at": now,
        },
        context=context, authority=authority,
    )
    grant = repository.create_authorization_grant(
        workspace_id,
        {
            "qualification_receipt_id": receipt_b, "actor_id": context.principal_id,
            "action": "robot_navigate_to", "target": "provider:robot",
            "max_calls": 1, "policy_version": "authorization.policy.v1",
            "grant_receipt": uuid4().hex * 2,
        },
        context=context, authority=authority,
    )
    dependent_claim_id, dependent_receipt_id, dependent_evaluation_id = (
        str(uuid4()), str(uuid4()), str(uuid4())
    )
    dependent_closure = json.dumps({"nodes": [{
        "node_type": "dependency_binding", "node_id": claim_id,
        "payload": {
            "profile_id": "math.formal.v1", "use_scope": "knowledge",
            "qualification_receipt_id": receipt_b,
        },
    }]})
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO qualification_evaluations(id, tenant_id, claim_revision_id, "
                "claim_semantic_hash, profile_id, profile_version, profile_hash, "
                "profile_snapshot, evidence_closure_hash, evidence_closure, policy_version, "
                "policy_hash, verdict, criteria, blockers, evidence_vector, "
                "independence_summary, evaluated_by, created_at) "
                "VALUES (:id, :tenant_id, :claim_id, :semantic_hash, 'math.formal.v1', "
                "1, :profile_hash, '{}', :closure_hash, :closure, 'math-policy-v1', "
                ":policy_hash, 'ADMITTED', '[]', '[]', '{}', '{}', NULL, :created_at)"
            ),
            {
                "id": dependent_evaluation_id, "tenant_id": workspace_id,
                "claim_id": dependent_claim_id, "semantic_hash": "f" * 64,
                "profile_hash": "9" * 64, "closure_hash": "b" * 64,
                "closure": dependent_closure, "policy_hash": "c" * 64,
                "created_at": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO qualification_receipts(id, tenant_id, evaluation_id, "
                "claim_revision_id, claim_semantic_hash, profile_id, profile_version, "
                "evidence_closure_hash, policy_version, policy_hash, verdict, criteria, "
                "blockers, evidence_vector, independence_summary, receipt_hash, issued_at) "
                "VALUES (:id, :tenant_id, :evaluation_id, :claim_id, :semantic_hash, "
                "'math.formal.v1', 1, :closure_hash, 'math-policy-v1', :policy_hash, "
                "'ADMITTED', '[]', '[]', '{}', '{}', :receipt_hash, :issued_at)"
            ),
            {
                "id": dependent_receipt_id, "tenant_id": workspace_id,
                "evaluation_id": dependent_evaluation_id, "claim_id": dependent_claim_id,
                "semantic_hash": "f" * 64, "closure_hash": "b" * 64,
                "policy_hash": "c" * 64, "receipt_hash": uuid4().hex * 2,
                "issued_at": now,
            },
        )
    dependent_admission = repository.admit_knowledge(
        workspace_id,
        {
            "id": str(uuid4()), "contract_version": "knowledge.admission.v1",
            "qualification_receipt_id": dependent_receipt_id,
            "qualification_receipt_hash": repository.get_qualification_receipt(
                workspace_id, dependent_receipt_id
            )["receipt_hash"],
            "claim_revision_id": dependent_claim_id, "claim_semantic_hash": "f" * 64,
            "profile_id": "math.formal.v1", "profile_version": 1,
            "use_scope": "knowledge", "admission_policy_id": "knowledge.default.v1",
            "admission_policy_version": 1, "admission_policy_hash": "d" * 64,
            "approved_by": context.principal_id, "rationale": "Synthetic hard dependency.",
            "receipt_hash": uuid4().hex * 2, "issued_at": now,
        },
        context=context, authority=authority,
    )
    dependent_grant = repository.create_authorization_grant(
        workspace_id,
        {
            "qualification_receipt_id": dependent_receipt_id,
            "actor_id": context.principal_id,
            "action": "publish", "target": "knowledge/dependent", "max_calls": 1,
            "policy_version": "authorization.policy.v1",
            "grant_receipt": uuid4().hex * 2,
        },
        context=context, authority=authority,
    )
    decision = {
        "id": str(uuid4()), "notice_verification_id": str(uuid4()),
        "target_claim_revision_id": claim_id, "target_claim_semantic_hash": "a" * 64,
        "target_receipt_id": receipt_a, "target_attempt_id": attempt_id,
        "target_scope": "proof_attempt", "reason_code": "PROOF_INVALIDATED",
        "evidence_artifact_ids": [], "evidence_artifact_hashes": {},
        "policy_id": "invalidation.pinned-source-admin.v1",
        "policy_hash": "e" * 64, "rationale": "Synthetic selected attempt defect.",
        "decided_by": context.principal_id, "effect_state": "stale",
        "decision_hash": uuid4().hex * 2, "decided_at": now,
    }
    return repository, context, authority, {
        "grant": grant, "binding": admission["current_use_binding"],
        "receipt_b": receipt_b, "dependent_grant": dependent_grant,
        "dependent_binding": dependent_admission["current_use_binding"],
        "dependent_claim_id": dependent_claim_id,
    }, decision


@pytest.mark.parametrize("first", ["invalidation", "consumption"])
@pytest.mark.parametrize("grant_kind", ["source", "dependent"])
def test_shared_attempt_invalidation_and_grant_consumption_serialize(
    first: str, grant_kind: str
) -> None:
    repository, context, authority, seeded, decision = _seed()
    locked = threading.Event()
    release = threading.Event()
    second_started = threading.Event()

    def pause_after_first_claim_lock(conn, cursor, statement, parameters, ctx, many):
        if (
            threading.current_thread().name == "paused-first"
            and "pg_advisory_xact_lock" in statement
        ):
            locked.set()
            assert release.wait(15), "first transaction was never released"

    def invalidate():
        return repository.apply_invalidation_decision(
            context.workspace_id, decision, context=context, authority=authority
        )

    def consume():
        selected_grant = (
            seeded["grant"] if grant_kind == "source" else seeded["dependent_grant"]
        )
        action, target = (
            ("robot_navigate_to", "provider:robot")
            if grant_kind == "source" else ("publish", "knowledge/dependent")
        )
        return repository.consume_authorization_grant(
            context.workspace_id, context.principal_id, action,
            target, datetime.now(UTC).isoformat(),
            grant_id=selected_grant["id"],
        )

    operations = {"invalidation": invalidate, "consumption": consume}

    def first_call():
        threading.current_thread().name = "paused-first"
        return operations[first]()

    def second_call():
        second_started.set()
        return operations["consumption" if first == "invalidation" else "invalidation"]()

    event.listen(repository.engine, "after_cursor_execute", pause_after_first_claim_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_result = executor.submit(first_call)
            assert locked.wait(10), "first operation did not acquire the claim lock"
            second_result = executor.submit(second_call)
            assert second_started.wait(10)
            time.sleep(0.1)
            assert not second_result.done(), "second operation bypassed the claim lock"
            release.set()
            completed_first = first_result.result(timeout=15)
            completed_second = second_result.result(timeout=15)
    finally:
        release.set()
        event.remove(repository.engine, "after_cursor_execute", pause_after_first_claim_lock)

    consumed = completed_second if first == "invalidation" else completed_first
    applied = completed_first if first == "invalidation" else completed_second
    assert applied["affected_binding_ids"] == [
        seeded["binding"]["id"], seeded["dependent_binding"]["id"]
    ]
    if first == "invalidation":
        assert consumed is None
    else:
        assert consumed is not None
    assert repository.get_current_use_binding(
        context.workspace_id, decision["target_claim_revision_id"], "math.formal.v1"
    )["state"] == "stale"
    assert repository.get_current_use_binding(
        context.workspace_id, seeded["dependent_claim_id"], "math.formal.v1"
    )["state"] == "stale"
    selected_grant = seeded["grant"] if grant_kind == "source" else seeded["dependent_grant"]
    grant = next(
        item for item in repository.list_authorization_grants(context.workspace_id)
        if item["id"] == selected_grant["id"]
    )
    assert grant["calls_used"] == (0 if first == "invalidation" else 1)
    with pytest.raises(InvalidEvidenceError, match="non-invalidated binding"):
        repository.create_authorization_grant(
            context.workspace_id,
            {
                "qualification_receipt_id": seeded["receipt_b"],
                "actor_id": context.principal_id, "action": "robot_navigate_to",
                "target": "provider:robot", "max_calls": 1,
                "policy_version": "authorization.policy.v1",
                "grant_receipt": uuid4().hex * 2,
            },
            context=context, authority=authority,
        )
    with (
        create_engine(POSTGRES_URL).begin() as connection,
        pytest.raises(DBAPIError, match="invalidated qualification"),
    ):
        connection.execute(
            text("UPDATE current_use_bindings SET state = 'current' WHERE id = :id"),
            {"id": seeded["binding"]["id"]},
        )
