"""Frozen qualification dependencies must remain usable through the whole graph."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
from sqlalchemy import create_engine

from app.core.errors import InvalidEvidenceError
from app.repositories.qualification import (
    _assert_postgres_current_dependencies,
    _assert_sqlite_current_dependencies,
)

_TABLES = (
    "CREATE TABLE qualification_evaluations (id TEXT, tenant_id TEXT, evidence_closure TEXT)",
    "CREATE TABLE qualification_receipts "
    "(id TEXT, tenant_id TEXT, evaluation_id TEXT, claim_revision_id TEXT, profile_id TEXT)",
    "CREATE TABLE current_use_bindings "
    "(id TEXT, tenant_id TEXT, claim_revision_id TEXT, profile_id TEXT, use_scope TEXT, "
    "qualification_receipt_id TEXT, state TEXT)",
    "CREATE TABLE invalidation_decisions "
    "(tenant_id TEXT, target_claim_revision_id TEXT, target_scope TEXT, "
    "target_receipt_id TEXT, target_attempt_id TEXT)",
    "CREATE TABLE qualification_receipt_attempt_selections "
    "(tenant_id TEXT, qualification_receipt_id TEXT, attempt_id TEXT)",
)


def _closure(*dependencies: tuple[str, str]) -> str:
    return json.dumps(
        {
            "nodes": [
                {
                    "node_type": "dependency_binding",
                    "node_id": claim_id,
                    "payload": {
                        "profile_id": "profile",
                        "use_scope": "knowledge",
                        "qualification_receipt_id": receipt_id,
                    },
                }
                for claim_id, receipt_id in dependencies
            ]
        }
    )


def _execute(backend: str, db: Any, statement: str, parameters: tuple = ()) -> None:
    if backend == "sqlite":
        db.execute(statement, parameters)
    else:
        db.exec_driver_sql(statement, parameters)


def _check(backend: str, db: Any) -> None:
    if backend == "sqlite":
        _assert_sqlite_current_dependencies(db, "tenant", "receipt-a")
    else:
        _assert_postgres_current_dependencies(db, "tenant", "receipt-a")


def _seed(backend: str, db: Any, *, cycle: bool = False) -> None:
    for statement in _TABLES:
        _execute(backend, db, statement)
    for suffix, dependencies in (
        ("a", (("claim-b", "receipt-b"),)),
        ("b", (("claim-a", "receipt-a"),) if cycle else (("claim-c", "receipt-c"),)),
        ("c", ()),
        ("c-new", ()),
    ):
        _execute(
            backend,
            db,
            "INSERT INTO qualification_evaluations VALUES (?, ?, ?)",
            (f"evaluation-{suffix}", "tenant", _closure(*dependencies)),
        )
        _execute(
            backend,
            db,
            "INSERT INTO qualification_receipts VALUES (?, ?, ?, ?, ?)",
            (
                f"receipt-{suffix}",
                "tenant",
                f"evaluation-{suffix}",
                f"claim-{suffix.removesuffix('-new')}",
                "profile",
            ),
        )
    for suffix in ("a", "b", "c"):
        _execute(
            backend,
            db,
            "INSERT INTO current_use_bindings VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"binding-{suffix}",
                "tenant",
                f"claim-{suffix}",
                "profile",
                "knowledge",
                f"receipt-{suffix}",
                "current",
            ),
        )


@pytest.mark.parametrize("backend", ["sqlite", "sqlalchemy"])
def test_transitively_rebound_dependency_is_rejected(backend: str) -> None:
    if backend == "sqlite":
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
    else:
        engine = create_engine("sqlite:///:memory:")
        db = engine.connect()
    try:
        _seed(backend, db)
        _check(backend, db)
        _execute(
            backend,
            db,
            "UPDATE current_use_bindings SET qualification_receipt_id = ? "
            "WHERE claim_revision_id = ?",
            ("receipt-c-new", "claim-c"),
        )
        with pytest.raises(InvalidEvidenceError, match="Frozen dependency"):
            _check(backend, db)
    finally:
        db.close()


@pytest.mark.parametrize("backend", ["sqlite", "sqlalchemy"])
def test_dependency_cycle_fails_closed(backend: str) -> None:
    if backend == "sqlite":
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
    else:
        engine = create_engine("sqlite:///:memory:")
        db = engine.connect()
    try:
        _seed(backend, db, cycle=True)
        with pytest.raises(InvalidEvidenceError, match="cycle"):
            _check(backend, db)
    finally:
        db.close()
