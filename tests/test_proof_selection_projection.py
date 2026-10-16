"""Historical proof selection must retain the profile's verifier eligibility."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from authority_support import provision_admin

from app.core.contracts import RequestContext
from app.core.invalidation import selected_kernel_attempt_ids_v1
from app.core.qualification import claim_semantic_hash
from app.repository import SQLiteRepository
from app.services.qualification import QualificationService


def _attempt(
    attempt_id: str,
    *,
    principal: str,
    outcome: str = "passed",
    runtime_derived: bool = True,
    model_route: str | None = None,
    run_id: str | None = "run-1",
    invalidated_by: list[str] | None = None,
) -> dict:
    return {
        "node_type": "verification_attempt",
        "node_id": attempt_id,
        "payload": {
            "validation_modality": "kernel_check",
            "artifact_ids": ["cert-1"],
            "outcome": outcome,
            "run_id": run_id,
            "invalidated_by": invalidated_by or [],
            "verifier_lineage": {
                "runtime_derived": runtime_derived,
                "principal_id": principal,
                "model_route": model_route,
            },
        },
    }


@pytest.mark.parametrize(
    ("profile_id", "valid_id", "invalid_id"),
    [
        ("math.formal.v1", "a-valid", "z-invalid"),
        ("math.formal.project.v2", "m-valid", "a-invalid"),
    ],
)
def test_historical_selection_ignores_ineligible_certificate_reusers(
    profile_id: str, valid_id: str, invalid_id: str
) -> None:
    principal = (
        "system:verifier:math-project:comparator"
        if profile_id == "math.formal.project.v2"
        else "system:verifier:lean-kernel"
    )
    criteria = [
        {"code": "statement_identity", "evidence_node_ids": ["cert-1"]},
        # Pre-0029 receipts need not identify the single selected Attempt.
        {"code": "kernel_check", "evidence_node_ids": ["cert-1"]},
    ]
    closure = {
        "nodes": sorted(
            [
                _attempt(valid_id, principal=principal),
                _attempt(invalid_id, principal=principal, outcome="failed"),
                _attempt("b-invalidated", principal=principal, invalidated_by=["decision"]),
                _attempt("c-model", principal=principal, model_route="llm/model"),
                _attempt("d-not-runtime", principal=principal, runtime_derived=False),
                _attempt("e-wrong-principal", principal="user:forged"),
                _attempt("f-no-run", principal=principal, run_id=None),
            ],
            key=lambda item: item["node_id"],
        )
    }
    if profile_id == "math.formal.v1":
        # A theorem kernel check does not require a Run ID.
        closure["nodes"] = [
            item for item in closure["nodes"] if item["node_id"] != "f-no-run"
        ]
    expected = frozenset({valid_id})
    assert selected_kernel_attempt_ids_v1(criteria, closure, profile_id) == expected
    migration = import_module("app.alembic.versions.0029_shared_attempt_invalidation")
    assert migration._selected_kernel_attempt_ids_v1(criteria, closure, profile_id) == expected


@pytest.mark.parametrize(
    "closure_order",
    [("attempt-r1", "attempt-r2"), ("attempt-r2", "attempt-r1")],
)
def test_legacy_theorem_receipt_retains_both_eligible_certificate_attempts(
    closure_order: tuple[str, str],
) -> None:
    # Statement identity names the shared certificate, while the old kernel
    # criterion lists both eligible Attempts. Verifier vintages selected
    # opposite ends, so neither can safely be ruled out during backfill.
    criteria = [
        {"code": "statement_identity", "evidence_node_ids": ["cert-1"]},
        {"code": "kernel_check", "evidence_node_ids": list(closure_order)},
    ]
    closure = {
        "nodes": [
            _attempt(attempt_id, principal="system:verifier:lean-kernel")
            for attempt_id in closure_order
        ]
    }
    expected = frozenset(closure_order)
    assert selected_kernel_attempt_ids_v1(criteria, closure, "math.formal.v1") == expected
    migration = import_module("app.alembic.versions.0029_shared_attempt_invalidation")
    assert (
        migration._selected_kernel_attempt_ids_v1(criteria, closure, "math.formal.v1")
        == expected
    )


@pytest.mark.parametrize("selected_id", ["attempt-r1", "attempt-r2"])
def test_current_theorem_receipt_keeps_exact_selected_attempt(selected_id: str) -> None:
    criteria = [
        {"code": "statement_identity", "evidence_node_ids": ["cert-1"]},
        {"code": "kernel_check", "evidence_node_ids": [selected_id]},
    ]
    closure = {
        "nodes": [
            _attempt(attempt_id, principal="system:verifier:lean-kernel")
            for attempt_id in ("attempt-r1", "attempt-r2")
        ]
    }
    expected = frozenset({selected_id})
    assert selected_kernel_attempt_ids_v1(criteria, closure, "math.formal.v1") == expected
    migration = import_module("app.alembic.versions.0029_shared_attempt_invalidation")
    assert (
        migration._selected_kernel_attempt_ids_v1(criteria, closure, "math.formal.v1")
        == expected
    )


@pytest.mark.parametrize(
    ("profile_id", "expected_stale"),
    [
        ("math.formal.v1", True),
        ("math.formal.project.v2", True),
        ("logic.propositional.ab.v1", False),
    ],
)
def test_admitted_receipt_with_no_reconstructible_math_attempt_fails_closed(
    profile_id: str, expected_stale: bool
) -> None:
    claim = {
        "id": "claim-1",
        "claim_key": "HISTORICAL-PROOF",
        "claim_kind": "mathematical_theorem",
        "statement": "A = A",
        "scope": "Frozen historical statement.",
    }
    repository = MagicMock()
    service = QualificationService(lambda: repository)
    profile = service._registry.get(profile_id).profile
    closure = {
        "nodes": [
            _attempt("attempt-1", principal="system:verifier:lean-kernel")
        ]
    }
    # The frozen statement-identity criterion points at a certificate absent
    # from every eligible Attempt, so the possible-selection set is empty.
    criteria = [
        {"code": "statement_identity", "evidence_node_ids": ["unmatched-cert"]},
        {"code": "kernel_check", "evidence_node_ids": ["attempt-1"]},
    ]
    receipt = {
        "id": "receipt-1",
        "claim_revision_id": claim["id"],
        "claim_semantic_hash": claim_semantic_hash(claim),
        "profile_id": profile_id,
        "profile_version": profile.version,
        "policy_version": profile.policy_version,
        "evaluation_id": "evaluation-1",
        "verdict": "ADMITTED",
        "criteria": criteria,
    }
    repository.get_research_claim.return_value = claim
    repository.get_qualification_receipt.return_value = receipt
    repository.list_invalidation_decisions.return_value = []
    repository.list_qualification_evaluations.return_value = [
        {
            "id": "evaluation-1",
            "profile_hash": profile.content_hash,
            "evidence_closure": closure,
        }
    ]
    repository.upsert_current_use_binding.side_effect = lambda _workspace, value: value
    context = RequestContext("historical-proof", "workspace-a", "admin")

    assert selected_kernel_attempt_ids_v1(criteria, closure, profile_id) == frozenset()
    reasons = service._receipt_stale_reasons(context, receipt)
    assert ("proof_selection_unresolved" in reasons) is expected_stale
    binding = {
        "claim_revision_id": claim["id"],
        "profile_id": profile_id,
        "qualification_receipt_id": receipt["id"],
        "state": "current",
    }
    refreshed = service._refresh_binding(context, binding)
    assert (refreshed["state"] == "stale") is expected_stale


@pytest.mark.parametrize("invalidated_attempt_id", ["attempt-r1", "attempt-r2"])
def test_legacy_receipt_is_invalidated_by_either_eligible_attempt(
    tmp_path: Path, invalidated_attempt_id: str
) -> None:
    repository = SQLiteRepository(tmp_path / "legacy-proof-selection.db")
    repository.init()
    context, authority = provision_admin(
        repository, RequestContext("legacy-selection", "workspace-a", "admin")
    )
    app_root = Path(__file__).resolve().parents[1] / "app"
    config = Config(str(app_root / "alembic.ini"))
    config.set_main_option("script_location", str(app_root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{repository.database_path.as_posix()}")
    command.downgrade(config, "0028_native_goal_binding")

    claim_id, evaluation_id, receipt_id = (str(uuid4()) for _ in range(3))
    now = datetime.now(UTC).isoformat()
    criteria = [
        {"code": "statement_identity", "evidence_node_ids": ["cert-1"]},
        {"code": "kernel_check", "evidence_node_ids": ["attempt-r1", "attempt-r2"]},
    ]
    closure = {
        "nodes": [
            _attempt(attempt_id, principal="system:verifier:lean-kernel")
            for attempt_id in ("attempt-r1", "attempt-r2")
        ]
    }
    with repository._connect() as db:
        db.execute(
            "INSERT INTO qualification_evaluations(id, tenant_id, claim_revision_id, "
            "claim_semantic_hash, profile_id, profile_version, profile_hash, "
            "profile_snapshot, evidence_closure_hash, evidence_closure, policy_version, "
            "policy_hash, verdict, criteria, blockers, evidence_vector, "
            "independence_summary, created_at) VALUES (?, ?, ?, ?, ?, 1, ?, '{}', "
            "?, ?, ?, ?, 'ADMITTED', ?, '[]', '{}', '{}', ?)",
            (
                evaluation_id, context.workspace_id, claim_id, "a" * 64,
                "math.formal.v1", "b" * 64, "c" * 64, json.dumps(closure),
                "historical-policy", "d" * 64, json.dumps(criteria), now,
            ),
        )
        db.execute(
            "INSERT INTO qualification_receipts(id, tenant_id, evaluation_id, "
            "claim_revision_id, claim_semantic_hash, profile_id, profile_version, "
            "evidence_closure_hash, policy_version, policy_hash, verdict, criteria, "
            "blockers, evidence_vector, independence_summary, receipt_hash, issued_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 'ADMITTED', ?, '[]', '{}', '{}', ?, ?)",
            (
                receipt_id, context.workspace_id, evaluation_id, claim_id, "a" * 64,
                "math.formal.v1", "c" * 64, "historical-policy", "d" * 64,
                json.dumps(criteria), "e" * 64, now,
            ),
        )
    # Materialize old-runtime rows at the old schema. Current repository
    # authority checks intentionally require 0030 auth_version columns and
    # cannot be used against a downgraded database.
    admission_id = str(uuid4())
    binding_id = str(uuid4())
    with repository._connect() as db:
        db.execute(
            "INSERT INTO knowledge_admission_receipts(id, tenant_id, "
            "contract_version, qualification_receipt_id, qualification_receipt_hash, "
            "claim_revision_id, claim_semantic_hash, profile_id, profile_version, "
            "use_scope, admission_policy_id, admission_policy_version, "
            "admission_policy_hash, approved_by, rationale, receipt_hash, issued_at) "
            "VALUES (?, ?, 'knowledge.admission.v1', ?, ?, ?, ?, "
            "'math.formal.v1', 1, 'knowledge', 'knowledge.default.v1', 1, "
            "?, ?, ?, ?, ?)",
            (
                admission_id, context.workspace_id, receipt_id, "e" * 64,
                claim_id, "a" * 64, "f" * 64, context.principal_id,
                "Historical receipt selected for current use.", uuid4().hex * 2, now,
            ),
        )
        db.execute(
            "INSERT INTO current_use_bindings(id, tenant_id, claim_revision_id, "
            "profile_id, use_scope, qualification_receipt_id, "
            "knowledge_admission_receipt_id, state, stale_reason, bound_by, "
            "created_at, updated_at) VALUES (?, ?, ?, 'math.formal.v1', "
            "'knowledge', ?, ?, 'current', NULL, ?, ?, ?)",
            (
                binding_id, context.workspace_id, claim_id, receipt_id,
                admission_id, context.principal_id, now, now,
            ),
        )
    command.upgrade(config, "head")

    with repository._connect() as db:
        selected_rows = {
            (row["attempt_id"], row["selection_version"])
            for row in db.execute(
                "SELECT attempt_id, selection_version "
                "FROM qualification_receipt_attempt_selections "
                "WHERE tenant_id = ? AND qualification_receipt_id = ?",
                (context.workspace_id, receipt_id),
            )
        }
    assert selected_rows == {
        ("attempt-r1", "historical-possible-kernel-attempts.v1"),
        ("attempt-r2", "historical-possible-kernel-attempts.v1"),
    }

    decision_id = str(uuid4())
    decision = repository.apply_invalidation_decision(
        context.workspace_id,
        {
            "id": decision_id,
            "notice_verification_id": str(uuid4()),
            "target_claim_revision_id": claim_id,
            "target_claim_semantic_hash": "a" * 64,
            "target_receipt_id": receipt_id,
            "target_attempt_id": invalidated_attempt_id,
            "target_scope": "proof_attempt",
            "reason_code": "PROOF_INVALIDATED",
            "evidence_artifact_ids": [],
            "evidence_artifact_hashes": {},
            "policy_id": "invalidation.pinned-source-admin.v1",
            "policy_hash": "f" * 64,
            "rationale": "Historical candidate attempt was invalidated.",
            "decided_by": context.principal_id,
            "effect_state": "stale",
            "decision_hash": uuid4().hex * 2,
            "decided_at": datetime.now(UTC).isoformat(),
        },
        context=context,
        authority=authority,
    )
    assert decision["affected_binding_ids"] == [binding_id]
    binding = repository.get_current_use_binding(
        context.workspace_id, claim_id, "math.formal.v1"
    )
    assert binding["state"] == "stale"
    assert binding["stale_reason"] == f"invalidation_decision:{decision_id}"
