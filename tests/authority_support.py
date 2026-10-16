"""Test-only active administrator session for final authority operations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.authority import ADMIN_AUTHORITY_SCOPES, AuthorityContext
from app.core.contracts import RequestContext


def provision_admin(repository, context: RequestContext) -> tuple[RequestContext, AuthorityContext]:
    if repository.get_tenant(context.workspace_id) is None:
        repository.create_tenant("Authority test workspace", context.workspace_id)
    user = repository.create_user(
        context.workspace_id,
        f"authority-{uuid4()}@example.test",
        "Authority test administrator",
        "unused-test-password-hash",
        role="admin",
    )
    session_hash = uuid4().hex + uuid4().hex
    repository.save_auth_session(
        session_hash,
        user["id"],
        context.workspace_id,
        (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        expected_auth_version=0,
    )
    privileged = replace(context, principal_id=user["id"])
    authority = AuthorityContext(
        workspace_id=context.workspace_id,
        principal_id=user["id"],
        session_hash=session_hash,
        scopes=ADMIN_AUTHORITY_SCOPES,
    )
    return privileged, authority
