"""Recheck final authority at the application boundary, independent of HTTP.

``RequestContext`` fields are claims, not authorization. Only an active
repository session for the same active administrator and workspace can carry a
privileged operation. The repository repeats this check while holding the
write transaction's lock before committing a protected mutation. A session
hash is bearer-equivalent and must never be serialized to Run, Step,
Artifact, Receipt, or audit payloads.
"""

from __future__ import annotations

from ..core.authority import ADMIN_AUTHORITY_SCOPES, AuthorityContext
from ..core.contracts import RequestContext
from ..core.errors import AuthorityDeniedError


def require_admin_authority(
    repository: object,
    context: RequestContext,
    authority: AuthorityContext | None,
    scope: str,
) -> None:
    """Fail closed unless the current server session grants this exact action."""

    if (
        scope not in ADMIN_AUTHORITY_SCOPES
        or authority is None
        or scope not in authority.scopes
        or not authority.session_hash
        or not context.principal_id
        or authority.principal_id != context.principal_id
        or authority.workspace_id != context.workspace_id
    ):
        raise AuthorityDeniedError(scope)
    session = repository.get_auth_session(authority.session_hash)
    if (
        not session
        or session["user_id"] != context.principal_id
        or session["tenant_id"] != context.workspace_id
    ):
        raise AuthorityDeniedError(scope)
    user = repository.get_user(context.principal_id)
    if (
        not user
        or user["tenant_id"] != context.workspace_id
        or user["role"] != "admin"
    ):
        raise AuthorityDeniedError(scope)
