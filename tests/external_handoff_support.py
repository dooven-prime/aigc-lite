"""Test-only current receipt and narrow grant for external handoff tests.

The synthetic receipt isolates execution authorization from domain qualification.
Callers should stub the unrelated semantic-currentness refresh in the gate;
the repository still requires the exact CURRENT receipt binding on consumption.
"""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

from authority_support import provision_admin

from app.core.contracts import RequestContext


def provision_external_grant(
    repository,
    context: RequestContext,
    *,
    action: str,
    target: str,
    max_calls: int = 10,
    scope: dict | None = None,
    conditions: dict | None = None,
    budget: dict | None = None,
) -> dict:
    """Install a synthetic admitted receipt, then issue a real admin-held grant."""

    admin_context, authority = provision_admin(repository, context)
    receipt_id = str(uuid4())
    evaluation_id = str(uuid4())
    claim_id = str(uuid4())
    receipt_hash = sha256(receipt_id.encode()).hexdigest()
    admission_hash = sha256(claim_id.encode()).hexdigest()
    now = "2026-09-29T00:00:00+00:00"
    with repository._connect() as connection:
        connection.execute(
            "INSERT INTO qualification_evaluations(id, tenant_id, claim_revision_id, "
            "claim_semantic_hash, profile_id, profile_version, profile_hash, "
            "profile_snapshot, evidence_closure_hash, evidence_closure, policy_version, "
            "policy_hash, verdict, criteria, blockers, evidence_vector, "
            "independence_summary, evaluated_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, '{}', ?, '{\"nodes\":[]}', ?, ?, "
            "'ADMITTED', '[]', '[]', '{}', '{}', NULL, ?)",
            (
                evaluation_id,
                context.workspace_id,
                claim_id,
                "a" * 64,
                "contract.profile.v1",
                "9" * 64,
                "b" * 64,
                "contract-policy-v1",
                "c" * 64,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO qualification_receipts(id, tenant_id, evaluation_id, "
            "claim_revision_id, claim_semantic_hash, profile_id, profile_version, "
            "evidence_closure_hash, policy_version, policy_hash, verdict, criteria, "
            "blockers, evidence_vector, independence_summary, receipt_hash, issued_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 'ADMITTED', '[]', '[]', '{}', '{}', ?, ?)",
            (
                receipt_id,
                context.workspace_id,
                evaluation_id,
                claim_id,
                "a" * 64,
                "contract.profile.v1",
                "b" * 64,
                "contract-policy-v1",
                "c" * 64,
                receipt_hash,
                now,
            ),
        )
    repository.admit_knowledge(
        context.workspace_id,
        {
            "id": str(uuid4()),
            "contract_version": "knowledge.admission.v1",
            "qualification_receipt_id": receipt_id,
            "qualification_receipt_hash": receipt_hash,
            "claim_revision_id": claim_id,
            "claim_semantic_hash": "a" * 64,
            "profile_id": "contract.profile.v1",
            "profile_version": 1,
            "use_scope": "knowledge",
            "admission_policy_id": "knowledge.default.v1",
            "admission_policy_version": 1,
            "admission_policy_hash": "f" * 64,
            "approved_by": admin_context.principal_id,
            "rationale": "Test-only currentness fixture for dispatch handoff.",
            "receipt_hash": admission_hash,
            "issued_at": now,
        },
        context=admin_context,
        authority=authority,
    )
    return repository.create_authorization_grant(
        context.workspace_id,
        {
            "qualification_receipt_id": receipt_id,
            "actor_id": context.principal_id,
            "action": action,
            "target": target,
            "scope": scope or {},
            "conditions": conditions or {},
            "budget": budget or {},
            "max_calls": max_calls,
            "policy_version": "authorization.policy.v1",
            "grant_receipt": sha256(str(uuid4()).encode()).hexdigest(),
            "created_by": admin_context.principal_id,
        },
        context=admin_context,
        authority=authority,
    )
