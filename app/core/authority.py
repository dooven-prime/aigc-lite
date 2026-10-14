"""Server-held application authority, separate from Tool Catalog permissions."""

from __future__ import annotations

from dataclasses import dataclass, field

ADMIN_AUTHORITY_SCOPES = frozenset({
    "qualification:admit",
    "qualification:invalidate",
    "authorization:grant",
    "enforcement:reconcile",
    "workflow:promote",
})


@dataclass(frozen=True, slots=True)
class AuthorityContext:
    """Session-bound authority passed only to final application mutations.

    This must not be embedded in the ordinary RequestContext, Agent/tool
    context, Run, Step, Artifact, or Receipt. A session hash is bearer-equivalent.
    Services always revalidate it against the current repository state.
    """

    workspace_id: str
    principal_id: str
    session_hash: str = field(repr=False, compare=False)
    scopes: frozenset[str] = field(default_factory=frozenset)
