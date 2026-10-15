"""Upgrade a populated copy of the published v0.7.0 schema to 0029.

This is an engineering migration fixture, not proof that the synthetic
mathematical receipts were validly issued. The deliberately malformed
zero-selection receipt exercises fail-closed current-use behavior.
"""

from __future__ import annotations

import json
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

from app.core.contracts import (
    RequestContext,
    ToolHints,
    ToolRisk,
    ToolSource,
    ToolSpec,
)
from app.core.qualification import canonical_hash, claim_semantic_hash
from app.profiles.math_theorem import MATH_FORMAL_PROFILE
from app.repository import PostgresRepository, SQLiteRepository
from app.services.authorization import ToolAuthorizationGate
from app.services.qualification import QualificationService

OLD_HEAD = "0025_invocation_timeout"
NEW_HEAD = "0029_shared_attempt_invalidation"
V0_7_COMMIT = "834ebff7ac17f7825d8afab0c7e277f9185bd853"
WORKSPACE = "tagged-upgrade-workspace"
STAMP = "2026-10-14T12:00:00+00:00"
KINDS = ("exact", "ambiguous", "zero")
HISTORICAL_TABLES = (
    "qualification_evaluations",
    "qualification_receipts",
    "knowledge_admission_receipts",
    "current_use_bindings",
    "authorization_grants",
    "enforcement_dispatches",
)
SEED_TABLES = (
    "tenants", "sessions", "agent_runs", "research_claim_revisions",
    *HISTORICAL_TABLES,
)


def _tagged_app_root(tmp_path: Path) -> Path:
    """Use the annotated release tag, not a current-code approximation of 0025."""

    checkout = Path(__file__).resolve().parents[1]
    commit = subprocess.run(
        ["git", "rev-parse", "--verify", "v0.7.0^{commit}"],
        cwd=checkout, capture_output=True, text=True, check=False,
    )
    if commit.returncode != 0:
        pytest.fail("v0.7.0 tag is required for historical upgrade acceptance")
    assert commit.stdout.strip() == V0_7_COMMIT
    archive = subprocess.run(
        ["git", "archive", "--format=tar", "v0.7.0", "app/alembic", "app/alembic.ini"],
        cwd=checkout, capture_output=True, check=True,
    )
    root = tmp_path / "published-v0.7.0"
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
    tagged = root / "app"
    current = checkout / "app"
    for published in (tagged / "alembic").rglob("*.py"):
        matching = current / "alembic" / published.relative_to(tagged / "alembic")
        assert published.read_bytes() == matching.read_bytes()
    return tagged


def _config(path: Path | str, app_root: Path | None = None) -> Config:
    app_root = app_root or Path(__file__).resolve().parents[1] / "app"
    config = Config(str(app_root / "alembic.ini"))
    config.set_main_option("script_location", str(app_root / "alembic"))
    url = f"sqlite:///{path.as_posix()}" if isinstance(path, Path) else path
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _attempt(attempt_id: str, certificate_id: str, *, passed: bool = True) -> dict:
    return {
        "node_type": "verification_attempt",
        "node_id": attempt_id,
        "payload": {
            "validation_modality": "kernel_check",
            "outcome": "passed" if passed else "failed",
            "artifact_ids": [certificate_id],
            "run_id": "run-v07",
            "verifier_lineage": {
                "runtime_derived": True,
                "principal_id": "system:verifier:lean-kernel",
                "model_route": None,
            },
        },
    }


def _seed_v0_7_history(path: Path) -> None:
    profile = MATH_FORMAL_PROFILE
    policy_hash = canonical_hash(
        {"profile_hash": profile.content_hash, "policy_version": profile.policy_version}
    )
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO tenants(id, name, created_at) VALUES (?, ?, ?)",
            (WORKSPACE, "Tagged release upgrade", STAMP),
        )
        db.execute(
            "INSERT INTO sessions(id, tenant_id, title, created_at, updated_at) "
            "VALUES ('session-v07', ?, 'Historical run', ?, ?)",
            (WORKSPACE, STAMP, STAMP),
        )
        db.execute(
            "INSERT INTO agent_runs(id, tenant_id, session_id, request_id, status, "
            "selected_model, created_at) VALUES ('run-v07', ?, 'session-v07', "
            "'request-v07', 'running', 'fixture', ?)",
            (WORKSPACE, STAMP),
        )
        for kind in KINDS:
            claim = {
                "claim_key": f"tagged-{kind}",
                "revision_number": 1,
                "claim_type": "MATHEMATICAL_THEOREM",
                "statement": f"Synthetic {kind} statement",
                "scope": "Migration acceptance only; no mathematical assertion.",
                "definitions": [],
                "negative_boundaries": [],
                "dependency_claim_ids": [],
                "parent_revision_id": None,
            }
            semantic_hash = claim_semantic_hash(claim)
            db.execute(
                "INSERT INTO research_claim_revisions(id, tenant_id, research_case_id, "
                "claim_key, revision_number, statement, claim_type, scope, "
                "method_revision, lifecycle_status, status_axes, closure_status, "
                "blockers, semantic_hash, definitions, negative_boundaries, "
                "dependency_claim_ids, parent_revision_id, created_at) "
                "VALUES (?, ?, 'migration-case', ?, 1, ?, ?, ?, 'synthetic-v1', "
                "'active', '{}', 'closed', '[]', ?, '[]', '[]', '[]', NULL, ?)",
                (
                    f"claim-{kind}", WORKSPACE, claim["claim_key"],
                    claim["statement"], claim["claim_type"], claim["scope"],
                    semantic_hash, STAMP,
                ),
            )
            certificate_id = f"certificate-{kind}"
            if kind == "exact":
                nodes = [
                    _attempt("attempt-exact", certificate_id),
                    _attempt("attempt-exact-other", certificate_id),
                ]
                kernel_ids = ["attempt-exact"]
            elif kind == "ambiguous":
                nodes = [
                    _attempt("attempt-ambiguous-a", certificate_id),
                    _attempt("attempt-ambiguous-b", certificate_id),
                ]
                kernel_ids = ["attempt-ambiguous-a", "attempt-ambiguous-b"]
            else:
                nodes = [_attempt("attempt-zero", certificate_id, passed=False)]
                kernel_ids = ["attempt-zero"]
            criteria = [
                {"code": "statement_identity", "evidence_node_ids": [certificate_id]},
                {"code": "kernel_check", "evidence_node_ids": kernel_ids},
            ]
            closure = {"nodes": nodes}
            closure_hash = canonical_hash(closure)
            db.execute(
                "INSERT INTO qualification_evaluations(id, tenant_id, "
                "claim_revision_id, claim_semantic_hash, profile_id, profile_version, "
                "profile_hash, profile_snapshot, evidence_closure_hash, evidence_closure, "
                "policy_version, policy_hash, verdict, criteria, blockers, "
                "evidence_vector, independence_summary, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, '{}', ?, ?, ?, ?, 'ADMITTED', ?, "
                "'[]', '{}', '{}', ?)",
                (
                    f"evaluation-{kind}", WORKSPACE, f"claim-{kind}", semantic_hash,
                    profile.profile_id, profile.content_hash, closure_hash,
                    json.dumps(closure), profile.policy_version, policy_hash,
                    json.dumps(criteria), STAMP,
                ),
            )
            receipt_hash = canonical_hash({"receipt": kind})
            db.execute(
                "INSERT INTO qualification_receipts(id, tenant_id, evaluation_id, "
                "claim_revision_id, claim_semantic_hash, profile_id, profile_version, "
                "evidence_closure_hash, policy_version, policy_hash, verdict, criteria, "
                "blockers, evidence_vector, independence_summary, receipt_hash, issued_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 'ADMITTED', ?, '[]', '{}', "
                "'{}', ?, ?)",
                (
                    f"receipt-{kind}", WORKSPACE, f"evaluation-{kind}",
                    f"claim-{kind}", semantic_hash, profile.profile_id, closure_hash,
                    profile.policy_version, policy_hash, json.dumps(criteria),
                    receipt_hash, STAMP,
                ),
            )
            db.execute(
                "INSERT INTO knowledge_admission_receipts(id, tenant_id, "
                "contract_version, qualification_receipt_id, qualification_receipt_hash, "
                "claim_revision_id, claim_semantic_hash, profile_id, profile_version, "
                "use_scope, admission_policy_id, admission_policy_version, "
                "admission_policy_hash, approved_by, rationale, receipt_hash, issued_at) "
                "VALUES (?, ?, 'knowledge.admission.v1', ?, ?, ?, ?, ?, 1, "
                "'knowledge', 'knowledge.admission.admin.v1', 1, ?, 'admin-v07', "
                "'Historical synthetic admission', ?, ?)",
                (
                    f"admission-{kind}", WORKSPACE, f"receipt-{kind}",
                    receipt_hash, f"claim-{kind}", semantic_hash,
                    profile.profile_id, policy_hash,
                    canonical_hash({"admission": kind}), STAMP,
                ),
            )
            db.execute(
                "INSERT INTO current_use_bindings(id, tenant_id, claim_revision_id, "
                "profile_id, use_scope, qualification_receipt_id, state, stale_reason, "
                "bound_by, created_at, updated_at, knowledge_admission_receipt_id) "
                "VALUES (?, ?, ?, ?, 'knowledge', ?, 'current', NULL, 'admin-v07', "
                "?, ?, ?)",
                (
                    f"binding-{kind}", WORKSPACE, f"claim-{kind}", profile.profile_id,
                    f"receipt-{kind}", STAMP, STAMP, f"admission-{kind}",
                ),
            )
            db.execute(
                "INSERT INTO authorization_grants(id, tenant_id, "
                "qualification_receipt_id, actor_id, action, target, scope, "
                "conditions, expires_at, budget, max_calls, calls_used, "
                "policy_version, state, grant_receipt, created_by, created_at) "
                "VALUES (?, ?, ?, 'actor-v07', 'inspect', ?, "
                "'{}', '{}', '2027-01-01T00:00:00+00:00', '{}', 3, 1, "
                "'authorization.policy.v1', 'active', '{}', 'admin-v07', ?)",
                (
                    f"grant-{kind}", WORKSPACE, f"receipt-{kind}",
                    f"provider:fixture/{kind}", STAMP,
                ),
            )
        for dispatch_id, adapter_id, state in (
            ("pending-v07", "adapter-pending", "dispatching"),
            ("uncertain-v07", "adapter-uncertain", "indeterminate"),
        ):
            db.execute(
                "INSERT INTO enforcement_dispatches(id, tenant_id, contract_version, "
                "binding_id, adapter_id, issuer_id, target_type, target_id, run_id, "
                "proposal_id, request_hash, execution_envelope_hash, tool_spec_hash, "
                "arguments_digest, requested_at, expires_at, state, last_error_code, "
                "created_at, updated_at) VALUES (?, ?, 'external.dispatch.v1', "
                "'binding-exact', ?, 'issuer-v07', 'tool_provider', 'fixture', "
                "'run-v07', 'proposal-v07', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dispatch_id, WORKSPACE, adapter_id, "a" * 64, "b" * 64,
                    "c" * 64, "d" * 64, STAMP, "2026-10-14T12:05:00+00:00",
                    state, "no_stop_confirmation" if state == "indeterminate" else None,
                    STAMP, STAMP,
                ),
            )


def _sqlite_rows(path: Path) -> dict[str, list[dict]]:
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return {
            table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in HISTORICAL_TABLES
        }


def _sqlite_revision(path: Path) -> str:
    with sqlite3.connect(path) as db:
        return db.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def _seed_receipt_batch(path: Path, count: int) -> None:
    """Repeat a tagged-0025 exact-selection shape without granting use."""

    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        evaluation = dict(db.execute(
            "SELECT * FROM qualification_evaluations WHERE id = 'evaluation-exact'"
        ).fetchone())
        receipt = dict(db.execute(
            "SELECT * FROM qualification_receipts WHERE id = 'receipt-exact'"
        ).fetchone())
        evaluation_columns = tuple(evaluation)
        receipt_columns = tuple(receipt)
        evaluations = []
        receipts = []
        for index in range(count):
            suffix = f"{index:04d}"
            evaluation_row = {**evaluation, "id": f"batch-evaluation-{suffix}"}
            receipt_row = {
                **receipt,
                "id": f"batch-receipt-{suffix}",
                "evaluation_id": evaluation_row["id"],
                "receipt_hash": canonical_hash({"batch-receipt": suffix}),
            }
            evaluations.append(tuple(evaluation_row[column] for column in evaluation_columns))
            receipts.append(tuple(receipt_row[column] for column in receipt_columns))
        db.executemany(
            f"INSERT INTO qualification_evaluations ({', '.join(evaluation_columns)}) "
            f"VALUES ({', '.join('?' for _ in evaluation_columns)})",
            evaluations,
        )
        db.executemany(
            f"INSERT INTO qualification_receipts ({', '.join(receipt_columns)}) "
            f"VALUES ({', '.join('?' for _ in receipt_columns)})",
            receipts,
        )


def _selection_rows(repository) -> set[tuple[str, str, str]]:
    if isinstance(repository, SQLiteRepository):
        with repository._connect() as db:
            rows = db.execute(
                "SELECT qualification_receipt_id, attempt_id, selection_version "
                "FROM qualification_receipt_attempt_selections WHERE tenant_id = ?",
                (WORKSPACE,),
            ).fetchall()
    else:
        with repository.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT qualification_receipt_id, attempt_id, selection_version "
                    "FROM qualification_receipt_attempt_selections WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": WORKSPACE},
            ).fetchall()
    return {tuple(row) for row in rows}


def _assert_upgraded_authority(repository) -> None:
    assert _selection_rows(repository) == {
        ("receipt-exact", "attempt-exact", "statement-identity-kernel-attempt.v1"),
        (
            "receipt-ambiguous", "attempt-ambiguous-a",
            "historical-possible-kernel-attempts.v1",
        ),
        (
            "receipt-ambiguous", "attempt-ambiguous-b",
            "historical-possible-kernel-attempts.v1",
        ),
    }
    service = QualificationService(lambda: repository)
    context = RequestContext("tagged-upgrade", WORKSPACE, "admin-v07")
    for kind in ("exact", "ambiguous"):
        binding = repository.get_current_use_binding(WORKSPACE, f"claim-{kind}", "math.formal.v1")
        assert binding is not None
        assert service._refresh_binding(context, binding)["state"] == "current"
    # An ADMITTED row with no eligible frozen proof must not silently retain
    # current authority merely because the new selection index is empty. The
    # public tool authorization path must refresh before consuming an old
    # grant, even though the stored binding still says current at this point.
    zero = repository.get_current_use_binding(WORKSPACE, "claim-zero", "math.formal.v1")
    assert zero is not None
    assert zero["state"] == "current"
    spec = ToolSpec(
        name="fixture__inspect",
        native_name="inspect",
        description="Synthetic authorized fixture action.",
        input_schema={"type": "object"},
        source=ToolSource.MCP,
        provider_id="fixture",
        risk=ToolRisk.HIGH,
        hints=ToolHints(read_only=False),
        extensions={"authority_requirement": {"required": True, "target": "zero"}},
    )
    actor = RequestContext("tagged-upgrade-tool", WORKSPACE, "actor-v07")
    assert ToolAuthorizationGate(lambda: repository).authorize_and_consume(
        actor, spec, {},
    ) is None
    zero = repository.get_current_use_binding(WORKSPACE, "claim-zero", "math.formal.v1")
    assert zero is not None and zero["state"] == "stale"
    assert "proof_selection_unresolved" in zero["stale_reason"]
    assert service.qualified_search(context, "tagged-zero", "math.formal.v1") == []
    assert repository.get_qualification_receipt(WORKSPACE, "receipt-zero")["verdict"] == "ADMITTED"
    grants = repository.list_authorization_grants(WORKSPACE)
    assert {item["id"] for item in grants} == {f"grant-{kind}" for kind in KINDS}
    assert all(item["calls_used"] == 1 for item in grants)
    assert repository.get_enforcement_dispatch(WORKSPACE, "pending-v07")["state"] == "dispatching"
    assert repository.get_enforcement_dispatch(WORKSPACE, "uncertain-v07")["state"] == "indeterminate"


def test_tagged_v0_7_sqlite_copy_upgrades_to_0029(tmp_path: Path) -> None:
    original = tmp_path / "published-v0.7-original.db"
    upgraded = tmp_path / "upgrade-copy.db"
    command.upgrade(_config(original, _tagged_app_root(tmp_path)), OLD_HEAD)
    assert _sqlite_revision(original) == OLD_HEAD
    _seed_v0_7_history(original)
    before = _sqlite_rows(original)
    assert [len(before[table]) for table in HISTORICAL_TABLES] == [3, 3, 3, 3, 3, 2]
    with sqlite3.connect(original) as source, sqlite3.connect(upgraded) as target:
        source.backup(target)
    assert _sqlite_rows(upgraded) == before

    command.upgrade(_config(upgraded), NEW_HEAD)
    assert _sqlite_revision(original) == OLD_HEAD
    assert _sqlite_rows(original) == before
    assert _sqlite_revision(upgraded) == NEW_HEAD
    assert _sqlite_rows(upgraded) == before
    _assert_upgraded_authority(SQLiteRepository(upgraded))
    # A runtime safety reconciliation changes only the copy's current-use
    # projection; never the original or immutable historical receipts.
    assert _sqlite_rows(original) == before
    after = _sqlite_rows(upgraded)
    for table in HISTORICAL_TABLES:
        if table != "current_use_bindings":
            assert after[table] == before[table]


def test_failed_0029_backfill_does_not_consume_tagged_source_backup(tmp_path: Path) -> None:
    original = tmp_path / "published-v0.7-original.db"
    failed_copy = tmp_path / "untrusted-failed-copy.db"
    restored_copy = tmp_path / "restored-copy.db"
    command.upgrade(_config(original, _tagged_app_root(tmp_path)), OLD_HEAD)
    _seed_v0_7_history(original)
    # A malformed frozen closure is an artificial corruption probe. The
    # published source is preserved; the copy is never used as a rollback
    # source after a failed SQLite DDL migration.
    with sqlite3.connect(original) as source, sqlite3.connect(failed_copy) as target:
        source.backup(target)
    with sqlite3.connect(failed_copy) as db:
        db.execute(
            "UPDATE qualification_evaluations SET evidence_closure = ? "
            "WHERE id = 'evaluation-zero'",
            ("{not-json",),
        )
    before = _sqlite_rows(original)
    with pytest.raises(RuntimeError, match="Cannot backfill proof selection"):
        command.upgrade(_config(failed_copy), NEW_HEAD)
    assert _sqlite_revision(original) == OLD_HEAD
    assert _sqlite_rows(original) == before

    # Recovery means returning to a known-good pre-upgrade database image,
    # never trusting the partially migrated copy or an in-place downgrade.
    with sqlite3.connect(original) as source, sqlite3.connect(restored_copy) as target:
        source.backup(target)
    assert _sqlite_revision(restored_copy) == OLD_HEAD
    assert _sqlite_rows(restored_copy) == before
    command.upgrade(_config(restored_copy), NEW_HEAD)
    assert _sqlite_revision(restored_copy) == NEW_HEAD


def test_tagged_v0_7_bounded_receipt_batch_backfills_on_copy(tmp_path: Path) -> None:
    original = tmp_path / "published-v0.7-batch.db"
    upgraded = tmp_path / "batch-upgrade-copy.db"
    command.upgrade(_config(original, _tagged_app_root(tmp_path)), OLD_HEAD)
    _seed_v0_7_history(original)
    _seed_receipt_batch(original, 300)
    before = _sqlite_rows(original)
    with sqlite3.connect(original) as source, sqlite3.connect(upgraded) as target:
        source.backup(target)

    command.upgrade(_config(upgraded), NEW_HEAD)
    assert _sqlite_revision(original) == OLD_HEAD
    assert _sqlite_rows(original) == before
    assert _sqlite_revision(upgraded) == NEW_HEAD
    assert _sqlite_rows(upgraded) == before
    selections = _selection_rows(SQLiteRepository(upgraded))
    assert len(selections) == 303
    assert {
        (f"batch-receipt-{index:04d}", "attempt-exact", "statement-identity-kernel-attempt.v1")
        for index in range(300)
    } <= selections


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


def test_tagged_v0_7_postgres_copy_upgrades_to_0029(tmp_path: Path) -> None:
    configured_url = os.getenv("AIGC_LITE_TEST_POSTGRES_URL")
    if not configured_url:
        pytest.skip("AIGC_LITE_TEST_POSTGRES_URL is not configured")
    tagged = _tagged_app_root(tmp_path)
    seed_path = tmp_path / "representative-v0.7.0.db"
    command.upgrade(_config(seed_path, tagged), OLD_HEAD)
    _seed_v0_7_history(seed_path)

    supplied = make_url(configured_url)
    assert supplied.get_backend_name() == "postgresql"
    suffix = uuid4().hex[:12]
    source_name = f"aigc_v07_old_{suffix}"
    copy_name = f"aigc_v08_copy_{suffix}"
    failed_name = f"aigc_v08_failed_{suffix}"
    admin_url = supplied.set(database="postgres").render_as_string(hide_password=False)
    source_url = supplied.set(database=source_name).render_as_string(hide_password=False)
    copy_url = supplied.set(database=copy_name).render_as_string(hide_password=False)
    failed_url = supplied.set(database=failed_name).render_as_string(hide_password=False)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    created: list[str] = []
    try:
        with admin_engine.connect() as admin:
            admin.exec_driver_sql(f'CREATE DATABASE "{source_name}"')
            created.append(source_name)
        command.upgrade(_config(source_url, tagged), OLD_HEAD)
        source_engine = create_engine(source_url)
        try:
            with sqlite3.connect(seed_path) as seed, source_engine.begin() as connection:
                seed.row_factory = sqlite3.Row
                metadata = MetaData()
                metadata.reflect(bind=connection, only=SEED_TABLES)
                for table in SEED_TABLES:
                    rows = [dict(row) for row in seed.execute(f"SELECT * FROM {table}")]
                    if rows:
                        connection.execute(metadata.tables[table].insert(), rows)
        finally:
            source_engine.dispose()
        before = _postgres_rows(source_url)
        assert [len(before[table]) for table in HISTORICAL_TABLES] == [3, 3, 3, 3, 3, 2]
        with admin_engine.connect() as admin:
            admin.exec_driver_sql(f'CREATE DATABASE "{copy_name}" WITH TEMPLATE "{source_name}"')
            created.append(copy_name)
            admin.exec_driver_sql(f'CREATE DATABASE "{failed_name}" WITH TEMPLATE "{source_name}"')
            created.append(failed_name)
        assert _postgres_rows(copy_url) == before
        failed_engine = create_engine(failed_url)
        try:
            with failed_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE qualification_evaluations SET evidence_closure = :bad "
                        "WHERE id = 'evaluation-zero'"
                    ),
                    {"bad": "{not-json"},
                )
        finally:
            failed_engine.dispose()
        with pytest.raises(RuntimeError, match="Cannot backfill proof selection"):
            command.upgrade(_config(failed_url), NEW_HEAD)
        assert _postgres_revision(failed_url) == OLD_HEAD
        failed_engine = create_engine(failed_url)
        try:
            with failed_engine.connect() as connection:
                assert connection.execute(text(
                    "SELECT to_regclass('public.qualification_receipt_attempt_selections')"
                )).scalar_one() is None
        finally:
            failed_engine.dispose()
        assert _postgres_rows(source_url) == before

        command.upgrade(_config(copy_url), NEW_HEAD)
        assert _postgres_revision(source_url) == OLD_HEAD
        assert _postgres_rows(source_url) == before
        assert _postgres_revision(copy_url) == NEW_HEAD
        assert _postgres_rows(copy_url) == before
        _assert_upgraded_authority(PostgresRepository(copy_url))
        assert _postgres_rows(source_url) == before
        after = _postgres_rows(copy_url)
        for table in HISTORICAL_TABLES:
            if table != "current_use_bindings":
                assert after[table] == before[table]
    finally:
        with admin_engine.connect() as admin:
            for name in reversed(created):
                admin.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin_engine.dispose()
