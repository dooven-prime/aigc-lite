"""A revoked admin session and a protected write have one observable order."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from authority_support import provision_admin

from app.core.contracts import RequestContext
from app.core.errors import AuthorityDeniedError
from app.core.invalidation import NoticeVerificationDraft
from app.repository import PostgresRepository, SQLiteRepository
from app.services.invalidation import InvalidationService


def _setup(tmp_path, backend: str):
    if backend == "sqlite":
        database_path = tmp_path / "session-linearization.db"
        writer = SQLiteRepository(database_path)
        writer.init()
        revoker = SQLiteRepository(database_path)
    else:
        postgres_url = os.getenv("AIGC_LITE_TEST_POSTGRES_URL", "")
        if not postgres_url:
            pytest.skip("AIGC_LITE_TEST_POSTGRES_URL is not configured")
        writer = PostgresRepository(postgres_url)
        writer.init()
        revoker = PostgresRepository(postgres_url)

    workspace_id = f"session-linearization-{uuid4()}"
    context, authority = provision_admin(
        writer,
        RequestContext(
            request_id="session-linearization",
            workspace_id=workspace_id,
            principal_id="candidate",
        ),
    )
    service = InvalidationService(
        lambda: writer,
        source_fetcher=lambda _url: b"# Pinned synthetic withdrawal notice\n",
    )
    return writer, revoker, service, context, authority


def _verification_count(repository, workspace_id: str) -> int:
    if isinstance(repository, SQLiteRepository):
        with repository._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM notice_verifications WHERE tenant_id = ?",
                (workspace_id,),
            ).fetchone()
        return int(row[0])
    row = repository._one(
        "SELECT COUNT(*) AS count FROM notice_verifications WHERE tenant_id = :tenant_id",
        {"tenant_id": workspace_id},
    )
    return int(row["count"])


@pytest.mark.parametrize("backend", ("sqlite", "postgres"))
def test_revoke_committed_before_final_authorization_rejects_write(
    tmp_path, monkeypatch, backend: str
) -> None:
    writer, revoker, service, context, authority = _setup(tmp_path, backend)
    at_final_write = Event()
    resume_write = Event()
    original = writer.record_notice_verification

    def pause_before_final_write(tenant_id, values, *, context, authority):
        at_final_write.set()
        assert resume_write.wait(10), "test did not release final write"
        return original(tenant_id, values, context=context, authority=authority)

    monkeypatch.setattr(writer, "record_notice_verification", pause_before_final_write)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(
            service.verify_notice,
            context,
            NoticeVerificationDraft("a" * 40),
            authority=authority,
        )
        try:
            assert at_final_write.wait(10), "service did not reach final write"
            assert revoker.revoke_auth_session(
                authority.session_hash, context.principal_id
            )
        finally:
            resume_write.set()
        with pytest.raises(AuthorityDeniedError):
            pending.result(timeout=10)

    assert _verification_count(writer, context.workspace_id) == 0


@pytest.mark.parametrize("backend", ("sqlite", "postgres"))
def test_authorized_write_commits_before_waiting_revoke(
    tmp_path, monkeypatch, backend: str
) -> None:
    writer, revoker, service, context, authority = _setup(tmp_path, backend)
    authorized_inside_transaction = Event()
    resume_write = Event()
    revoke_started = Event()
    revoke_finished = Event()
    original = writer._assert_admin_write_authority

    def pause_after_final_authorization(connection, request_context, current_authority, scope):
        original(connection, request_context, current_authority, scope)
        if scope == "qualification:invalidate":
            authorized_inside_transaction.set()
            assert resume_write.wait(10), "test did not release authorized write"

    def revoke():
        revoke_started.set()
        result = revoker.revoke_auth_session(
            authority.session_hash, context.principal_id
        )
        revoke_finished.set()
        return result

    monkeypatch.setattr(writer, "_assert_admin_write_authority", pause_after_final_authorization)
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending_write = workers.submit(
            service.verify_notice,
            context,
            NoticeVerificationDraft("a" * 40),
            authority=authority,
        )
        try:
            assert authorized_inside_transaction.wait(10), "write did not acquire authority lock"
            pending_revoke = workers.submit(revoke)
            assert revoke_started.wait(10)
            assert not revoke_finished.wait(0.2), "revoke completed while write held authority"
        finally:
            resume_write.set()
        verification = pending_write.result(timeout=10)
        assert pending_revoke.result(timeout=10)

    assert verification["requested_by"] == context.principal_id
    assert _verification_count(writer, context.workspace_id) == 1
    with pytest.raises(AuthorityDeniedError):
        service.verify_notice(
            context,
            NoticeVerificationDraft("b" * 40),
            authority=authority,
        )
    assert _verification_count(writer, context.workspace_id) == 1


@pytest.mark.parametrize("backend", ("sqlite", "postgres"))
def test_admin_session_cannot_write_another_workspace_through_repository(
    tmp_path, backend: str
) -> None:
    writer, _revoker, _service, context, authority = _setup(tmp_path, backend)
    other_workspace = f"other-session-workspace-{uuid4()}"
    writer.create_tenant("Other workspace", other_workspace)
    values = {
        "id": str(uuid4()),
        "tenant_id": context.workspace_id,
        "source_repository": "openai/math",
        "source_commit": "a" * 40,
        "source_path": "history.md",
        "source_artifact_id": str(uuid4()),
        "source_hash": "1" * 64,
        "verification_method": "https_pinned_commit_sha256",
        "verification_hash": "2" * 64,
        "requested_by": context.principal_id,
        "verified_at": "2026-10-14T00:00:00+00:00",
    }

    # The target workspace is the repository argument, not a caller-supplied
    # tenant_id hidden inside the values payload.
    with pytest.raises(AuthorityDeniedError):
        writer.record_notice_verification(
            other_workspace, values, context=context, authority=authority
        )
    assert _verification_count(writer, context.workspace_id) == 0
    assert _verification_count(writer, other_workspace) == 0

    recorded = writer.record_notice_verification(
        context.workspace_id,
        {**values, "id": str(uuid4()), "tenant_id": other_workspace},
        context=context,
        authority=authority,
    )
    assert recorded["tenant_id"] == context.workspace_id
    assert _verification_count(writer, context.workspace_id) == 1
    assert _verification_count(writer, other_workspace) == 0
