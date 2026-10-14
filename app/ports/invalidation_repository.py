"""Persistence port for verified notices and local invalidation decisions."""

from __future__ import annotations

from typing import Protocol

from ..core.authority import AuthorityContext
from ..core.contracts import RequestContext


class InvalidationRepository(Protocol):
    def record_notice_verification(
        self, tenant_id: str, values: dict, *,
        context: RequestContext, authority: AuthorityContext,
    ) -> dict: ...

    def get_notice_verification(self, tenant_id: str, verification_id: str) -> dict | None: ...

    def list_invalidation_decisions(self, tenant_id: str, claim_id: str) -> list[dict]: ...

    def apply_invalidation_decision(
        self, tenant_id: str, values: dict, *,
        context: RequestContext, authority: AuthorityContext,
    ) -> dict: ...
