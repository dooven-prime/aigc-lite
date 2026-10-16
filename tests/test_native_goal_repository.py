"""Native Goal creation is atomic and the stored binding cannot be replaced."""

from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import app.repository as repository_module
from app.core.contracts import NativeGoalDraft, RequestContext
from app.repository import PostgresRepository, SQLiteRepository
from app.services.ledger_projection import LedgerProjectionService

_FIELDS = (
    "goal_contract", "goal_protocol_id", "goal_protocol_version",
    "goal_protocol_sha256", "goal_sha256", "goal_json",
)


def _repository(backend: str, tmp_path: Path):
    if backend == "postgres":
        url = os.getenv("AIGC_LITE_TEST_POSTGRES_URL", "")
        if not url:
            pytest.skip("AIGC_LITE_TEST_POSTGRES_URL is not configured")
        repository = PostgresRepository(url)
    else:
        repository = SQLiteRepository(tmp_path / "native-goal.db")
    repository.init()
    return repository


def _execute(repository, statement: str, values: dict) -> None:
    if isinstance(repository, SQLiteRepository):
        with repository._connect() as db:
            db.execute(statement, values)
    else:
        repository._execute(statement, values)


def _count_runs(repository, workspace_id: str) -> int:
    statement = "SELECT COUNT(*) AS count FROM agent_runs WHERE tenant_id = :tenant_id"
    if isinstance(repository, SQLiteRepository):
        with repository._connect() as db:
            return db.execute(statement, {"tenant_id": workspace_id}).fetchone()[0]
    return repository._one(statement, {"tenant_id": workspace_id})["count"]


def _protocol(repository, workspace_id: str, *, status: str = "frozen") -> dict:
    return repository.create_evidence_protocol(workspace_id, {
        "profile": "native.goal.test", "name": "Goal source", "version": 3,
        "status": status, "purpose": "Prove a concrete target",
        "scope": "One local run", "completion_predicate": "Result checked",
        "stop_conditions": ["budget exhausted"], "budget": {"calls": 2},
        "content_hash": sha256(str(uuid4()).encode()).hexdigest(),
    })


def _draft(protocol: dict, workspace_id: str) -> NativeGoalDraft:
    snapshot = {
        "id": protocol["id"], "workspace_id": workspace_id,
        "version": protocol["version"], "status": "frozen",
        "purpose": protocol["purpose"], "scope": protocol["scope"],
        "completion_predicate": protocol["completion_predicate"],
        "stop_conditions": protocol["stop_conditions"], "budget": protocol["budget"],
    }
    return NativeGoalDraft(
        protocol_id=protocol["id"], protocol_version=protocol["version"],
        goal={
            "contract": "test.goal.v1", "protocol": snapshot,
            "target": "Verify example", "policy": {"max_calls": 2},
        },
    )


def _create_run(repository, workspace_id: str, session_id: str, goal_binding=None) -> dict:
    return repository.create_run(
        workspace_id, session_id, str(uuid4()), None, "test-model",
        goal_binding=goal_binding,
    )


def _projection(repository, workspace_id: str, run_id: str) -> dict:
    context = RequestContext(request_id=str(uuid4()), workspace_id=workspace_id)
    return LedgerProjectionService(
        repository_provider=lambda: repository,
    ).export_run(context, run_id)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_bound_and_unbound_runs_persist_creation_identity(backend: str, tmp_path: Path) -> None:
    repository = _repository(backend, tmp_path)
    workspace_id = str(uuid4())
    repository.create_tenant("Native Goal", workspace_id)
    session_id = repository.create_session(workspace_id, "goal")["id"]
    protocol = _protocol(repository, workspace_id)
    draft = _draft(protocol, workspace_id)
    assert repository.get_evidence_protocol_row(workspace_id, protocol["id"])["id"] == (
        protocol["id"]
    )

    unbound = _create_run(repository, workspace_id, session_id)
    assert all(unbound[field] is None for field in _FIELDS)
    assert all(repository.get_run(workspace_id, unbound["id"])[field] is None for field in _FIELDS)

    bound = _create_run(repository, workspace_id, session_id, draft)
    stored = repository.get_run(workspace_id, bound["id"])
    assert stored is not None
    assert {field: stored[field] for field in _FIELDS} == {
        field: bound[field] for field in _FIELDS
    }
    goal_json = json.dumps(draft.goal, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    protocol_json = json.dumps(
        draft.goal["protocol"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert stored["goal_contract"] == "test.goal.v1"
    assert stored["goal_protocol_id"] == protocol["id"]
    assert stored["goal_protocol_version"] == 3
    assert stored["goal_json"] == goal_json
    assert stored["goal_sha256"] == sha256(
        b"aigc-lite.native-goal.v0\n" + goal_json.encode()
    ).hexdigest()
    assert stored["goal_protocol_sha256"] == sha256(
        b"aigc-lite.native-goal-protocol.v0\n" + protocol_json.encode()
    ).hexdigest()

    repository.finish_run(workspace_id, bound["id"], "succeeded")
    assert repository.get_run(workspace_id, bound["id"])["goal_json"] == goal_json


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_binding_rejects_missing_unfrozen_and_mismatched_protocols(
    backend: str, tmp_path: Path
) -> None:
    repository = _repository(backend, tmp_path)
    workspace_id, other_workspace = str(uuid4()), str(uuid4())
    repository.create_tenant("Native Goal A", workspace_id)
    repository.create_tenant("Native Goal B", other_workspace)
    session_id = repository.create_session(workspace_id, "goal")["id"]
    other_protocol = _protocol(repository, other_workspace)
    with pytest.raises(KeyError):
        _create_run(repository, workspace_id, session_id, _draft(other_protocol, other_workspace))

    missing = _draft(other_protocol, workspace_id)
    missing = NativeGoalDraft(str(uuid4()), missing.protocol_version, missing.goal)
    with pytest.raises(KeyError):
        _create_run(repository, workspace_id, session_id, missing)

    draft_protocol = _protocol(repository, workspace_id, status="draft")
    with pytest.raises(ValueError, match="frozen"):
        _create_run(repository, workspace_id, session_id, _draft(draft_protocol, workspace_id))

    frozen = _protocol(repository, workspace_id)
    valid = _draft(frozen, workspace_id)
    with pytest.raises(ValueError, match="version"):
        _create_run(repository, workspace_id, session_id, NativeGoalDraft(
            frozen["id"], 4, valid.goal,
        ))
    altered = {**valid.goal, "protocol": {**valid.goal["protocol"], "purpose": "altered"}}
    with pytest.raises(ValueError, match="snapshot"):
        _create_run(repository, workspace_id, session_id, NativeGoalDraft(
            frozen["id"], 3, altered,
        ))
    assert _count_runs(repository, workspace_id) == 0


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_database_rejects_partial_or_post_creation_binding(backend: str, tmp_path: Path) -> None:
    repository = _repository(backend, tmp_path)
    workspace_id = str(uuid4())
    repository.create_tenant("Native Goal", workspace_id)
    session_id = repository.create_session(workspace_id, "goal")["id"]
    protocol = _protocol(repository, workspace_id)
    bound = _create_run(repository, workspace_id, session_id, _draft(protocol, workspace_id))
    unbound = _create_run(repository, workspace_id, session_id)

    with pytest.raises(Exception, match="agent_runs_goal_all_or_none|CHECK constraint"):
        _execute(repository,
                 "INSERT INTO agent_runs(id, tenant_id, session_id, request_id, status, "
                 "selected_model, created_at, goal_contract) "
                 "VALUES (:id, :tenant_id, :session_id, :request_id, 'running', "
                 "'test-model', 'now', 'test.goal.v1')",
                 {"id": str(uuid4()), "tenant_id": workspace_id,
                  "session_id": session_id, "request_id": str(uuid4())})

    with pytest.raises(Exception, match="immutable"):
        _execute(repository,
                 "UPDATE agent_runs SET goal_sha256 = :digest WHERE id = :id",
                 {"digest": "0" * 64, "id": bound["id"]})
    with pytest.raises(Exception, match="immutable"):
        _execute(repository,
                 "UPDATE agent_runs SET goal_contract = :contract WHERE id = :id",
                 {"contract": "late.goal.v1", "id": unbound["id"]})
    assert repository.get_run(workspace_id, bound["id"])["goal_sha256"] == bound["goal_sha256"]
    assert repository.get_run(workspace_id, unbound["id"])["goal_contract"] is None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_profile_change_keeps_native_binding_but_changes_source_row_hash(
    backend: str, tmp_path: Path,
) -> None:
    repository = _repository(backend, tmp_path)
    workspace_id = str(uuid4())
    repository.create_tenant("Native Goal", workspace_id)
    session_id = repository.create_session(workspace_id, "goal")["id"]
    protocol = _protocol(repository, workspace_id)
    bound = _create_run(repository, workspace_id, session_id, _draft(protocol, workspace_id))

    before = _projection(repository, workspace_id, bound["id"])
    _execute(
        repository,
        "UPDATE evidence_protocols SET profile = :profile "
        "WHERE tenant_id = :tenant_id AND id = :id",
        {"profile": "different.profile", "tenant_id": workspace_id, "id": protocol["id"]},
    )
    after = _projection(repository, workspace_id, bound["id"])

    # profile is outside the frozen Goal snapshot in the v0 contract.
    assert before["signals"]["goal_binding"] == "NATIVE_BOUND"
    assert after["signals"]["goal_binding"] == "NATIVE_BOUND"
    assert after["native_goal"]["binding"] == before["native_goal"]["binding"]
    assert after["source"]["rows"]["agent_runs"] == before["source"]["rows"]["agent_runs"]
    assert after["source"]["rows"]["goal_protocols"][0]["sha256"] != (
        before["source"]["rows"]["goal_protocols"][0]["sha256"]
    )
    assert after["native_goal"]["source_protocol_row_sha256"] != (
        before["native_goal"]["source_protocol_row_sha256"]
    )
    assert after["source"]["sha256"] != before["source"]["sha256"]
    assert after["sha256"] != before["sha256"]


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("mutation", ["semantics", "revoke", "delete"])
def test_run_creation_serializes_protocol_mutation(
    backend: str, mutation: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(backend, tmp_path)
    workspace_id = str(uuid4())
    repository.create_tenant("Native Goal", workspace_id)
    session_id = repository.create_session(workspace_id, "goal")["id"]
    protocol = _protocol(repository, workspace_id)
    draft = _draft(protocol, workspace_id)
    selected, release = Event(), Event()
    original_prepare = repository_module.prepare_native_goal_binding

    def pause_after_protocol_select(goal, protocol_row, selected_workspace):
        selected.set()
        if not release.wait(timeout=15):
            raise AssertionError("Run creation did not resume")
        return original_prepare(goal, protocol_row, selected_workspace)

    monkeypatch.setattr(repository_module, "prepare_native_goal_binding", pause_after_protocol_select)
    statements = {
        "semantics": "UPDATE evidence_protocols SET scope = 'changed during creation' "
                     "WHERE tenant_id = :tenant_id AND id = :id",
        "revoke": "UPDATE evidence_protocols SET status = 'failed' "
                  "WHERE tenant_id = :tenant_id AND id = :id",
        "delete": "DELETE FROM evidence_protocols "
                  "WHERE tenant_id = :tenant_id AND id = :id",
    }
    statement = statements[mutation]
    identity = {"tenant_id": workspace_id, "id": protocol["id"]}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_create_run, repository, workspace_id, session_id, draft)
        try:
            assert selected.wait(timeout=10), "Run never reached the locked Protocol read"
            if backend == "sqlite":
                with closing(sqlite3.connect(repository.database_path, timeout=0)) as db:
                    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                        db.execute(statement, identity)
            else:
                with pytest.raises(DBAPIError, match="lock timeout"):
                    with repository.engine.begin() as connection:
                        connection.execute(text("SET LOCAL lock_timeout = '750ms'"))
                        connection.execute(text(statement), identity)
        finally:
            release.set()
        bound = future.result(timeout=15)

    # The blocked competing write could not interleave with Run insertion.
    before = _projection(repository, workspace_id, bound["id"])
    assert before["signals"]["goal_binding"] == "NATIVE_BOUND"
    _execute(repository, statement, identity)
    after = _projection(repository, workspace_id, bound["id"])
    assert after["signals"]["goal_binding"] == "INCONSISTENT"
    assert after["run"]["effective_status"] == "indeterminate"
    assert repository.get_run(workspace_id, bound["id"])["goal_sha256"] == (
        bound["goal_sha256"]
    )
    if mutation == "delete":
        assert repository.get_evidence_protocol_row(workspace_id, protocol["id"]) is None
        assert after["native_goal"]["source_protocol_row_sha256"] is None
    else:
        assert after["native_goal"]["source_protocol_row_sha256"] != (
            before["native_goal"]["source_protocol_row_sha256"]
        )


def test_sqlite_upgrade_keeps_older_runs_unbound(tmp_path: Path) -> None:
    database_path = tmp_path / "older-runs.db"
    app_root = Path(__file__).resolve().parents[1] / "app"
    config = Config(str(app_root / "alembic.ini"))
    config.set_main_option("script_location", str(app_root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0027_stream_lifecycle")
    with sqlite3.connect(database_path) as db:
        db.execute(
            "INSERT INTO agent_runs(id, tenant_id, session_id, request_id, status, "
            "selected_model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("older-run", "older-workspace", "older-session", "older-request",
             "succeeded", "test-model", "2026-01-01T00:00:00+00:00"),
        )
    repository = SQLiteRepository(database_path)
    context = RequestContext(request_id="projection-read", workspace_id="older-workspace")
    before = LedgerProjectionService(
        repository_provider=lambda: repository,
    ).export_run(context, "older-run")
    command.upgrade(config, "head")
    after = LedgerProjectionService(
        repository_provider=lambda: repository,
    ).export_run(context, "older-run")
    assert after == before
    with sqlite3.connect(database_path) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM agent_runs WHERE id = 'older-run'").fetchone()
        assert row is not None
        assert row["status"] == "succeeded"
        assert all(row[field] is None for field in _FIELDS)
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            "0030_auth_lifecycle"
        )

    command.downgrade(config, "0027_stream_lifecycle")
    with sqlite3.connect(database_path) as db:
        row = db.execute("SELECT status FROM agent_runs WHERE id = 'older-run'").fetchone()
        columns = {item[1] for item in db.execute("PRAGMA table_info(agent_runs)")}
        assert row == ("succeeded",)
        assert set(_FIELDS).isdisjoint(columns)
