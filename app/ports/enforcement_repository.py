"""Persistence port for immutable policy proposals and enforcement receipts."""

from __future__ import annotations

from typing import Any, Protocol


class EnforcementRepository(Protocol):
    def create_policy_proposal(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def get_policy_proposal(
        self, tenant_id: str, proposal_id: str
    ) -> dict | None: ...

    def list_policy_proposals(
        self,
        tenant_id: str,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]: ...

    def create_enforcement_dispatch(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def get_enforcement_dispatch(
        self, tenant_id: str, dispatch_id: str
    ) -> dict | None: ...

    def list_enforcement_dispatches(
        self,
        tenant_id: str,
        *,
        adapter_id: str | None = None,
        proposal_id: str | None = None,
        state: str | None = None,
        original_dispatch_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]: ...

    def finish_enforcement_dispatch(
        self,
        tenant_id: str,
        dispatch_id: str,
        *,
        state: str,
        receipt_id: str | None = None,
        workload_id: str | None = None,
        last_error_code: str | None = None,
    ) -> dict | None: ...

    def create_enforcement_receipt(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def get_enforcement_receipt(
        self, tenant_id: str, receipt_id: str
    ) -> dict | None: ...

    def list_enforcement_receipts(
        self,
        tenant_id: str,
        *,
        run_id: str | None = None,
        proposal_id: str | None = None,
        backend_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]: ...
