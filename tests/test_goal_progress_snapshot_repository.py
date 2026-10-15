"""The Goal evidence reader returns bounded facts without refreshing authority."""

from __future__ import annotations

import sqlite3
from uuid import uuid4

import pytest

from app.repositories.goal_progress_snapshot import (
    GoalProgressSnapshotError,
    read_goal_progress_rows,
)
from app.repository import SQLiteRepository


def test_sqlite_goal_progress_snapshot_does_not_create_missing_database(tmp_path) -> None:
    path = tmp_path / "missing" / "goal-progress.db"
    repository = SQLiteRepository(path)
    with pytest.raises(GoalProgressSnapshotError, match="database_unavailable"):
        repository.read_goal_progress_snapshot("workspace-a", "run-a", "claim-a", "profile-a")
    assert not path.exists()
    assert not path.parent.exists()


def test_sqlite_goal_progress_run_hint_does_not_create_missing_database(tmp_path) -> None:
    path = tmp_path / "missing" / "goal-progress.db"
    repository = SQLiteRepository(path)
    with pytest.raises(GoalProgressSnapshotError, match="database_unavailable"):
        repository.read_goal_progress_run_hint("workspace-a", "run-a")
    assert not path.exists()
    assert not path.parent.exists()


def test_sqlite_goal_progress_snapshot_works_with_query_only_connection(tmp_path) -> None:
    path = tmp_path / "goal-progress.db"
    writer = SQLiteRepository(path)
    writer.init()
    workspace_id = str(uuid4())
    writer.create_tenant("Goal progress snapshot", workspace_id)
    session_id = writer.create_session(workspace_id, "snapshot")["id"]
    run = writer.create_run(workspace_id, session_id, str(uuid4()), None, "test-model")

    def read_only_connection() -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    reader = SQLiteRepository(path, connect_factory=read_only_connection)
    hint = reader.read_goal_progress_run_hint(workspace_id, run["id"])
    assert hint is not None
    assert hint["id"] == run["id"]
    claim_id = str(uuid4())
    snapshot = reader.read_goal_progress_snapshot(
        workspace_id, run["id"], claim_id, "math.formal.v1"
    )
    assert snapshot is not None
    assert snapshot["run"]["id"] == run["id"]
    assert snapshot["goal_protocols"] == []
    assert snapshot["subjects"][claim_id]["claim"] is None
    assert snapshot["subjects"][claim_id]["missing_references"] == [
        {"kind": "claim_revision", "id": claim_id}
    ]


def test_malformed_run_goal_json_fails_closed_before_followup_reads() -> None:
    statements: list[str] = []

    def rows(statement: str, _values: dict) -> list[dict]:
        statements.append(statement)
        return [{"id": "run-a", "tenant_id": "workspace-a", "goal_json": "{bad"}]

    with pytest.raises(GoalProgressSnapshotError, match="malformed_json:run.goal_json"):
        read_goal_progress_rows(rows, "workspace-a", "run-a", "claim-a", "profile-a")
    assert len(statements) == 1
