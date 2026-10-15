"""Historical proof selection must retain the profile's verifier eligibility."""

from __future__ import annotations

from importlib import import_module

import pytest

from app.core.invalidation import selected_kernel_attempt_ids_v1


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
