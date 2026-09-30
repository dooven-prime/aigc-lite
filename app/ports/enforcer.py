"""Port implemented by out-of-process execution authority enforcers."""

from __future__ import annotations

from typing import Protocol

from ..core.contracts import RequestContext, ToolProviderResult, ToolSpec
from ..core.enforcement import (
    EnforcementTargetType,
    EnforcerRequest,
    SignedEnforcementReceiptEnvelope,
)


class EnforcerAdapter(Protocol):
    """Dispatch one frozen request to an independently operated enforcer."""

    adapter_id: str
    issuer_id: str
    target_type: EnforcementTargetType
    target_id: str

    async def enforce(
        self, request: EnforcerRequest
    ) -> SignedEnforcementReceiptEnvelope: ...


class ExternalToolExecutor(Protocol):
    """Optional Tool Catalog projection of deployer-owned enforcement bindings."""

    async def execute_if_bound(
        self,
        context: RequestContext,
        run_id: str | None,
        spec: ToolSpec,
        arguments: dict,
        authorization: dict | None = None,
    ) -> ToolProviderResult | None: ...
