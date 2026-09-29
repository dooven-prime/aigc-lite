"""Persistence port for research registry and verification state."""

from __future__ import annotations

from typing import Any, Protocol


class ResearchRepository(Protocol):
    def create_research_registry(
        self,
        tenant_id: str,
        case_values: dict[str, Any],
        sources: list[dict[str, Any]],
        claims: list[dict[str, Any]],
    ) -> dict: ...

    def get_research_case(self, tenant_id: str, case_id: str) -> dict | None: ...

    def get_research_case_by_protocol(
        self, tenant_id: str, protocol_id: str
    ) -> dict | None: ...

    def list_research_cases(
        self, tenant_id: str, limit: int = 50, profile: str | None = None
    ) -> list[dict]: ...

    def list_research_claims(
        self,
        tenant_id: str,
        *,
        research_case_id: str,
        claim_type: str | None = None,
        closure_status: str | None = None,
        limit: int = 1_000,
    ) -> list[dict]: ...

    def get_research_claim(self, tenant_id: str, claim_id: str) -> dict | None: ...

    def create_research_claim_relation(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def list_research_claim_relations(
        self,
        tenant_id: str,
        *,
        research_case_id: str,
        claim_id: str | None = None,
    ) -> list[dict]: ...

    def withdraw_research_claim_relation(
        self,
        tenant_id: str,
        relation_id: str,
        *,
        reason: str,
        withdrawn_by: str | None,
    ) -> dict | None: ...

    def create_research_verification_attempt(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def list_research_verification_attempts(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]: ...

    def create_research_verification_plan(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def get_research_verification_plan(
        self, tenant_id: str, plan_id: str
    ) -> dict | None: ...

    def list_research_verification_plans(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]: ...

    def create_research_verification_execution(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def finish_research_verification_execution(
        self, tenant_id: str, execution_id: str, values: dict[str, Any]
    ) -> dict | None: ...

    def list_research_verification_executions(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]: ...

    def create_research_promotion_evaluation(
        self, tenant_id: str, values: dict[str, Any], *, promote: bool
    ) -> dict: ...

    def list_research_promotion_evaluations(
        self, tenant_id: str, claim_id: str
    ) -> list[dict]: ...
