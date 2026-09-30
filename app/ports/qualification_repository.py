"""Persistence port for qualification, current use, and authorization."""

from __future__ import annotations

from typing import Any, Protocol


class QualificationRepository(Protocol):
    def create_qualification_evaluation(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def list_qualification_evaluations(
        self, tenant_id: str, claim_id: str, profile_id: str | None = None
    ) -> list[dict]: ...

    def create_qualification_receipt(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def get_qualification_receipt(
        self, tenant_id: str, receipt_id: str
    ) -> dict | None: ...

    def list_qualification_receipts(
        self, tenant_id: str, claim_id: str | None = None
    ) -> list[dict]: ...

    def create_evidence_edges(
        self, tenant_id: str, evaluation_id: str, edges: list[dict[str, Any]]
    ) -> list[dict]: ...

    def upsert_current_use_binding(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def admit_knowledge(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def list_knowledge_admission_receipts(
        self, tenant_id: str, claim_id: str | None = None
    ) -> list[dict]: ...

    def get_current_use_binding(
        self,
        tenant_id: str,
        claim_id: str,
        profile_id: str,
        use_scope: str = "knowledge",
    ) -> dict | None: ...

    def list_current_use_bindings(
        self,
        tenant_id: str,
        profile_id: str,
        state: str = "current",
        use_scope: str = "knowledge",
    ) -> list[dict]: ...

    def create_authorization_grant(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def list_authorization_grants(
        self, tenant_id: str, qualification_receipt_ids: list[str] | None = None
    ) -> list[dict]: ...

    def consume_authorization_grant(
        self,
        tenant_id: str,
        actor_id: str,
        action: str,
        target: str,
        used_at: str,
        *,
        grant_id: str | None = None,
    ) -> dict | None: ...
