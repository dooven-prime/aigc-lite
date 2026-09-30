"""HTTP control/read surface for execution authority evidence."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from ..auth import current_admin_user
from ..core.contracts import RequestContext
from ..core.enforcement import (
    EnforcementTargetType,
    ExecutionPolicySnapshot,
    PermissionDomain,
    PolicyPermission,
)
from ..database import get_repository
from ..services.enforcement import EnforcementService
from ..tenancy import Tenant, current_tenant

RequestContextFactory = Callable[[Request, Tenant], RequestContext]


class PolicyPermissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: PermissionDomain
    resource: str = Field(min_length=1, max_length=2_000)
    actions: list[str] = Field(min_length=1, max_length=32)
    constraints: dict[str, Any] = Field(default_factory=dict)


class ExecutionPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=0)
    default_action: Literal["deny"] = "deny"
    permissions: list[PolicyPermissionRequest] = Field(
        default_factory=list, max_length=256
    )
    limits: dict[str, StrictInt] = Field(default_factory=dict)

    def to_contract(self) -> ExecutionPolicySnapshot:
        return ExecutionPolicySnapshot(
            policy_id=self.policy_id,
            revision=self.revision,
            default_action=self.default_action,
            permissions=tuple(
                PolicyPermission(
                    domain=item.domain,
                    resource=item.resource,
                    actions=tuple(item.actions),
                    constraints=item.constraints,
                )
                for item in self.permissions
            ),
            limits=self.limits,
        )


class PolicyProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: EnforcementTargetType
    target_id: str = Field(min_length=1, max_length=500)
    base_policy: ExecutionPolicyRequest | None = None
    candidate_policy: ExecutionPolicyRequest
    rationale: str = Field(default="", max_length=4_000)


def create_enforcement_router(
    *,
    enforcement_service: EnforcementService,
    request_context_factory: RequestContextFactory,
) -> APIRouter:
    router = APIRouter()

    def admin_context(http_request: Request, user: dict) -> RequestContext:
        return request_context_factory(
            http_request, Tenant(user["tenant_id"], user["tenant_id"])
        )

    @router.get("/api/enforcement/issuers")
    async def enforcement_issuers(
        _tenant: Tenant = Depends(current_tenant),
    ) -> list[dict]:
        return enforcement_service.list_issuers()

    @router.post("/api/enforcement/policy-proposals", status_code=201)
    async def create_policy_proposal(
        payload: PolicyProposalRequest,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        result = enforcement_service.propose_policy(
            admin_context(http_request, user),
            target_type=payload.target_type,
            target_id=payload.target_id,
            base_policy=(
                payload.base_policy.to_contract() if payload.base_policy else None
            ),
            candidate_policy=payload.candidate_policy.to_contract(),
            rationale=payload.rationale,
        )
        get_repository().write_audit(
            user["tenant_id"],
            "execution_policy.propose",
            "/api/enforcement/policy-proposals",
            {
                "proposal_id": result["id"],
                "target_type": result["target_type"],
                "target_id": result["target_id"],
                "base_policy_hash": result["base_policy_hash"],
                "candidate_policy_hash": result["candidate_policy_hash"],
                "diff_hash": result["diff_hash"],
                "expands_authority": result["expands_authority"],
            },
            user_id=user["id"],
        )
        return result

    @router.get("/api/enforcement/policy-proposals")
    async def policy_proposals(
        http_request: Request,
        target_type: EnforcementTargetType | None = None,
        target_id: str | None = None,
        limit: int = 50,
        tenant: Tenant = Depends(current_tenant),
    ) -> list[dict]:
        return enforcement_service.list_policy_proposals(
            request_context_factory(http_request, tenant),
            target_type=target_type,
            target_id=target_id,
            limit=limit,
        )

    @router.get("/api/enforcement/policy-proposals/{proposal_id}")
    async def policy_proposal_detail(
        proposal_id: str,
        http_request: Request,
        tenant: Tenant = Depends(current_tenant),
    ) -> dict:
        return enforcement_service.get_policy_proposal(
            request_context_factory(http_request, tenant), proposal_id
        )

    @router.get("/api/enforcement/receipts")
    async def enforcement_receipts(
        http_request: Request,
        run_id: str | None = None,
        proposal_id: str | None = None,
        backend_id: str | None = None,
        limit: int = 100,
        tenant: Tenant = Depends(current_tenant),
    ) -> list[dict]:
        return enforcement_service.list_receipts(
            request_context_factory(http_request, tenant),
            run_id=run_id,
            proposal_id=proposal_id,
            backend_id=backend_id,
            limit=limit,
        )

    @router.get("/api/enforcement/receipts/{receipt_id}")
    async def enforcement_receipt_detail(
        receipt_id: str,
        http_request: Request,
        tenant: Tenant = Depends(current_tenant),
    ) -> dict:
        return enforcement_service.get_receipt(
            request_context_factory(http_request, tenant), receipt_id
        )

    return router
