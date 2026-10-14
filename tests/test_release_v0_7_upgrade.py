"""Release smoke: upgrade a populated v0.6.0 database copy to v0.7.0.

The v0.6.0 tag's Alembic revisions 0001-0020 are byte-identical to the
revisions retained in v0.7.0. This test stops at its published head before
copying the database and applying the new migrations to the copy only.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import tarfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.repository import SQLiteRepository

OLD_HEAD = "0020_knowledge_admission"
NEW_HEAD = "0025_invocation_timeout"
WORKSPACE = "upgrade-workspace"
STAMP = "2026-09-30T12:00:00+00:00"
HASH = {
    "semantic": "a" * 64,
    "closure": "b" * 64,
    "policy": "c" * 64,
    "qualification": "d" * 64,
    "admission": "e" * 64,
    "dispatch": "f" * 64,
}
HISTORICAL_TABLES = (
    "qualification_receipts",
    "knowledge_admission_receipts",
    "current_use_bindings",
    "authorization_grants",
    "enforcement_dispatches",
)
SEED_TABLES = (
    "tenants", "sessions", "agent_runs", *HISTORICAL_TABLES,
)
V0_6_COMMIT = "0ca02fe91dcd6e2188c91fc159eb0aab60f559f2"


def _published_v0_6_app_root(tmp_path: Path) -> Path:
    """Require the published release tag, never substitute current migrations."""

    current = Path(__file__).resolve().parents[1] / "app"
    commit = subprocess.run(
        ["git", "rev-parse", "--verify", "v0.6.0^{commit}"],
        cwd=current.parent, capture_output=True, text=True, check=False,
    )
    if commit.returncode != 0:
        pytest.fail("v0.6.0 tag is required for historical upgrade acceptance")
    assert commit.stdout.strip() == V0_6_COMMIT
    archive = subprocess.run(
        ["git", "archive", "--format=tar", "v0.6.0", "app/alembic", "app/alembic.ini"],
        cwd=current.parent, capture_output=True, check=True,
    )
    root = tmp_path / "published-v0.6.0"
    with tarfile.open(fileobj=BytesIO(archive.stdout), mode="r:") as bundle:
        for member in bundle:
            if member.isdir():
                continue
            assert member.isfile()
            assert member.name == "app/alembic.ini" or member.name.startswith(
                "app/alembic/"
            )
            target = (root / member.name).resolve()
            assert target.is_relative_to(root.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            assert source is not None
            target.write_bytes(source.read())
    # Release-owned migration history must not be silently rewritten later.
    for published_file in (root / "app" / "alembic").rglob("*.py"):
        matching = current / "alembic" / published_file.relative_to(root / "app" / "alembic")
        assert published_file.read_bytes() == matching.read_bytes()
    return root / "app"


def _config(path: Path | str, app_root: Path | None = None) -> Config:
    app_root = app_root or Path(__file__).resolve().parents[1] / "app"
    config = Config(str(app_root / "alembic.ini"))
    config.set_main_option("script_location", str(app_root / "alembic"))
    url = f"sqlite:///{path.as_posix()}" if isinstance(path, Path) else path
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _revision(path: Path) -> str:
    with sqlite3.connect(path) as db:
        return db.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def _historical_rows(path: Path) -> dict[str, list[dict]]:
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return {
            table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in HISTORICAL_TABLES
        }


def _seed_v0_6_history(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO tenants(id, name, created_at) VALUES (?, ?, ?)",
            (WORKSPACE, "Upgrade smoke", STAMP),
        )
        db.execute(
            "INSERT INTO sessions(id, tenant_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("session-v06", WORKSPACE, "Historical run", STAMP, STAMP),
        )
        db.execute(
            "INSERT INTO agent_runs(id, tenant_id, session_id, request_id, status, "
            "selected_model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("run-v06", WORKSPACE, "session-v06", "request-v06", "running", "fixture", STAMP),
        )
        db.execute(
            "INSERT INTO qualification_receipts(id, tenant_id, evaluation_id, "
            "claim_revision_id, claim_semantic_hash, profile_id, profile_version, "
            "evidence_closure_hash, policy_version, policy_hash, verdict, criteria, "
            "blockers, evidence_vector, independence_summary, receipt_hash, issued_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "qualification-v06", WORKSPACE, "evaluation-v06", "claim-revision-v06",
                HASH["semantic"], "math.formal.v1", 1, HASH["closure"],
                "math.formal.policy.v1", HASH["policy"], "ADMITTED", "[]", "[]",
                '{"local_correctness":"satisfied"}', "{}", HASH["qualification"], STAMP,
            ),
        )
        db.execute(
            "INSERT INTO knowledge_admission_receipts(id, tenant_id, contract_version, "
            "qualification_receipt_id, qualification_receipt_hash, claim_revision_id, "
            "claim_semantic_hash, profile_id, profile_version, use_scope, "
            "admission_policy_id, admission_policy_version, admission_policy_hash, "
            "approved_by, rationale, receipt_hash, issued_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "admission-v06", WORKSPACE, "knowledge.admission.v1", "qualification-v06",
                HASH["qualification"], "claim-revision-v06", HASH["semantic"],
                "math.formal.v1", 1, "knowledge", "knowledge.admission.admin.v1", 1,
                HASH["policy"], "admin-v06", "Explicit historical admission",
                HASH["admission"], STAMP,
            ),
        )
        db.execute(
            "INSERT INTO current_use_bindings(id, tenant_id, claim_revision_id, "
            "profile_id, use_scope, qualification_receipt_id, state, stale_reason, "
            "bound_by, created_at, updated_at, knowledge_admission_receipt_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "binding-v06", WORKSPACE, "claim-revision-v06", "math.formal.v1",
                "knowledge", "qualification-v06", "current", None, "admin-v06", STAMP,
                STAMP, "admission-v06",
            ),
        )
        db.execute(
            "INSERT INTO authorization_grants(id, tenant_id, qualification_receipt_id, "
            "actor_id, action, target, scope, conditions, expires_at, budget, "
            "max_calls, calls_used, policy_version, state, grant_receipt, created_by, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "grant-v06", WORKSPACE, "qualification-v06", "actor-v06", "inspect",
                "provider:fixture", '{"paths":["/read-only"]}', "{}",
                "2027-01-01T00:00:00+00:00", '{"max_cost":1}', 3, 1,
                "authorization.policy.v1", "active", '{"historical":true}',
                "admin-v06", STAMP,
            ),
        )
        for dispatch_id, adapter_id, state in (
            ("pending-v06", "adapter-pending", "dispatching"),
            ("uncertain-v06", "adapter-uncertain", "indeterminate"),
        ):
            db.execute(
                "INSERT INTO enforcement_dispatches(id, tenant_id, contract_version, "
                "binding_id, adapter_id, issuer_id, target_type, target_id, run_id, "
                "step_id, proposal_id, original_dispatch_id, request_hash, "
                "execution_envelope_hash, tool_spec_hash, arguments_digest, requested_at, "
                "expires_at, state, receipt_id, workload_id, last_error_code, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dispatch_id, WORKSPACE, "external.dispatch.v1", "binding-v06",
                    adapter_id, "issuer-v06", "tool_provider", "fixture", "run-v06",
                    None, "proposal-v06", None, HASH["dispatch"], HASH["closure"],
                    HASH["policy"], HASH["semantic"], STAMP,
                    "2026-09-30T12:05:00+00:00", state, None, None,
                    "no_stop_confirmation" if state == "indeterminate" else None,
                    STAMP, STAMP,
                ),
            )


def test_v0_6_0_history_upgrades_on_a_copy_without_authority_reinterpretation(
    tmp_path: Path,
) -> None:
    historical = tmp_path / "v0.6.0-original.db"
    upgraded = tmp_path / "v0.7.0-upgrade-copy.db"
    command.upgrade(_config(historical, _published_v0_6_app_root(tmp_path)), OLD_HEAD)
    assert _revision(historical) == OLD_HEAD
    _seed_v0_6_history(historical)
    before = _historical_rows(historical)
    assert [len(before[table]) for table in HISTORICAL_TABLES] == [1, 1, 1, 1, 2]

    # SQLite's backup API makes a consistent copy even if the source is in WAL mode.
    with sqlite3.connect(historical) as source, sqlite3.connect(upgraded) as target:
        source.backup(target)
    assert _historical_rows(upgraded) == before

    command.upgrade(_config(upgraded), NEW_HEAD)
    assert _revision(historical) == OLD_HEAD
    assert _historical_rows(historical) == before
    assert _revision(upgraded) == NEW_HEAD
    assert _historical_rows(upgraded) == before

    repository = SQLiteRepository(upgraded)
    receipt = repository.get_qualification_receipt(WORKSPACE, "qualification-v06")
    binding = repository.get_current_use_binding(
        WORKSPACE, "claim-revision-v06", "math.formal.v1"
    )
    grant = repository.list_authorization_grants(WORKSPACE)[0]
    pending = repository.get_enforcement_dispatch(WORKSPACE, "pending-v06")
    uncertain = repository.get_enforcement_dispatch(WORKSPACE, "uncertain-v06")
    assert receipt is not None and receipt["verdict"] == "ADMITTED"
    assert receipt["claim_semantic_hash"] == HASH["semantic"]
    assert binding is not None and binding["state"] == "current"
    assert binding["knowledge_admission_receipt_id"] == "admission-v06"
    assert grant["state"] == "active" and grant["calls_used"] == 1
    assert grant["max_calls"] == 3
    assert pending is not None and pending["state"] == "dispatching"
    assert uncertain is not None and uncertain["state"] == "indeterminate"
    assert uncertain["last_error_code"] == "no_stop_confirmation"

    with sqlite3.connect(upgraded) as db:
        assert db.execute("SELECT COUNT(*) FROM authorization_invocations").fetchone()[0] == 0
        columns = {
            row[1] for row in db.execute("PRAGMA table_info(authorization_invocations)")
        }
        assert "tool_timeout_seconds" in columns
        # A still-open v0.6 dispatch keeps its adapter execution slot. The
        # migration must not make a second dispatch look safe to start.
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO enforcement_dispatches SELECT "
                "?, tenant_id, contract_version, binding_id, adapter_id, issuer_id, "
                "target_type, target_id, run_id, step_id, proposal_id, "
                "original_dispatch_id, request_hash, execution_envelope_hash, "
                "tool_spec_hash, arguments_digest, requested_at, expires_at, "
                "state, receipt_id, workload_id, last_error_code, created_at, updated_at "
                "FROM enforcement_dispatches WHERE id = ?",
                ("duplicate-pending", "pending-v06"),
            )

    assert _historical_rows(upgraded) == before


def _postgres_rows(url: str) -> dict[str, list[dict]]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return {
                table: [
                    dict(row) for row in connection.execute(
                        text(f"SELECT * FROM {table} ORDER BY id")
                    ).mappings()
                ]
                for table in HISTORICAL_TABLES
            }
    finally:
        engine.dispose()


def _postgres_revision(url: str) -> str:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()


def test_v0_6_0_postgres_history_upgrades_on_a_database_copy(tmp_path: Path) -> None:
    configured_url = os.getenv("AIGC_LITE_TEST_POSTGRES_URL")
    if not configured_url:
        pytest.skip("AIGC_LITE_TEST_POSTGRES_URL is not configured")
    tagged_source = _published_v0_6_app_root(tmp_path)
    sqlite_source = tmp_path / "representative-v0.6.0.db"
    command.upgrade(_config(sqlite_source, tagged_source), OLD_HEAD)
    _seed_v0_6_history(sqlite_source)

    supplied_url = make_url(configured_url)
    assert supplied_url.get_backend_name() == "postgresql"
    database_suffix = uuid4().hex[:12]
    old_name = f"aigc_release_old_{database_suffix}"
    copy_name = f"aigc_release_copy_{database_suffix}"
    admin_url = supplied_url.set(database="postgres").render_as_string(hide_password=False)
    old_url = supplied_url.set(database=old_name).render_as_string(hide_password=False)
    copy_url = supplied_url.set(database=copy_name).render_as_string(hide_password=False)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    created: list[str] = []
    try:
        with admin_engine.connect() as admin:
            admin.exec_driver_sql(f'CREATE DATABASE "{old_name}"')
            created.append(old_name)
        command.upgrade(_config(old_url, tagged_source), OLD_HEAD)
        old_engine = create_engine(old_url)
        try:
            with sqlite3.connect(sqlite_source) as seed, old_engine.begin() as connection:
                seed.row_factory = sqlite3.Row
                metadata = MetaData()
                metadata.reflect(bind=connection, only=SEED_TABLES)
                for table in SEED_TABLES:
                    rows = [dict(row) for row in seed.execute(f"SELECT * FROM {table}")]
                    if rows:
                        connection.execute(metadata.tables[table].insert(), rows)
        finally:
            old_engine.dispose()
        assert _revision(sqlite_source) == OLD_HEAD
        assert _postgres_revision(old_url) == OLD_HEAD
        before = _postgres_rows(old_url)
        assert [len(before[table]) for table in HISTORICAL_TABLES] == [1, 1, 1, 1, 2]
        with admin_engine.connect() as admin:
            admin.exec_driver_sql(f'CREATE DATABASE "{copy_name}" WITH TEMPLATE "{old_name}"')
            created.append(copy_name)
        assert _postgres_rows(copy_url) == before

        command.upgrade(_config(copy_url), NEW_HEAD)
        assert _postgres_revision(old_url) == OLD_HEAD
        assert _postgres_revision(copy_url) == NEW_HEAD
        assert _postgres_rows(old_url) == before
        assert _postgres_rows(copy_url) == before
        engine = create_engine(copy_url)
        try:
            with engine.connect() as connection:
                assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == NEW_HEAD
                assert connection.execute(text("SELECT COUNT(*) FROM authorization_invocations")).scalar_one() == 0
                assert connection.execute(text(
                    "SELECT state FROM current_use_bindings WHERE id = 'binding-v06'"
                )).scalar_one() == "current"
                assert connection.execute(text(
                    "SELECT calls_used FROM authorization_grants WHERE id = 'grant-v06'"
                )).scalar_one() == 1
                assert connection.execute(text(
                    "SELECT state FROM enforcement_dispatches WHERE id = 'pending-v06'"
                )).scalar_one() == "dispatching"
                assert connection.execute(text(
                    "SELECT state FROM enforcement_dispatches WHERE id = 'uncertain-v06'"
                )).scalar_one() == "indeterminate"
                assert connection.execute(text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_name = 'authorization_invocations' "
                    "AND column_name = 'tool_timeout_seconds'"
                )).scalar_one() == 1
                connection.rollback()
                with pytest.raises(IntegrityError):
                    connection.execute(text(
                        "INSERT INTO enforcement_dispatches SELECT "
                        "'duplicate-pending', tenant_id, contract_version, binding_id, "
                        "adapter_id, issuer_id, target_type, target_id, run_id, step_id, "
                        "proposal_id, original_dispatch_id, request_hash, "
                        "execution_envelope_hash, tool_spec_hash, arguments_digest, "
                        "requested_at, expires_at, state, receipt_id, workload_id, "
                        "last_error_code, created_at, updated_at "
                        "FROM enforcement_dispatches WHERE id = 'pending-v06'"
                    ))
                connection.rollback()
        finally:
            engine.dispose()
        assert _postgres_rows(copy_url) == before
    finally:
        with admin_engine.connect() as admin:
            for name in reversed(created):
                admin.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin_engine.dispose()
