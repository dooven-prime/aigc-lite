"""HTTP routes for deterministic reviews and the advisory finding ledger."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from ..auth import current_admin_user
from ..core.contracts import RequestContext
from ..core.review import ReviewFindingStatus, ReviewSeverity
from ..database import get_repository
from ..profiles.execution_integrity import PROFILE_ID as EXECUTION_INTEGRITY_PROFILE_ID
from ..services.reviews import ReviewService
from ..tenancy import Tenant, current_tenant

RequestContextFactory = Callable[[Request, Tenant], RequestContext]


class ExecutionReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(
        default=EXECUTION_INTEGRITY_PROFILE_ID,
        min_length=1,
        max_length=200,
    )


def create_review_router(
    *,
    review_service: ReviewService,
    request_context_factory: RequestContextFactory,
) -> APIRouter:
    router = APIRouter()

    def admin_context(http_request: Request, user: dict) -> RequestContext:
        return request_context_factory(
            http_request, Tenant(user["tenant_id"], user["tenant_id"])
        )

    @router.get("/api/reviews/profiles")
    async def review_profiles(
        _tenant: Tenant = Depends(current_tenant),
    ) -> list[dict]:
        return review_service.list_profiles()

    @router.post("/api/reviews/runs/{run_id}", status_code=201)
    async def review_execution_run(
        run_id: str,
        payload: ExecutionReviewRequest,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        result = review_service.review_execution_run(
            admin_context(http_request, user), run_id, payload.profile_id
        )
        get_repository().write_audit(
            user["tenant_id"],
            "review.execution.evaluate",
            f"/api/reviews/runs/{run_id}",
            {
                "review_run_id": result["id"],
                "subject_id": run_id,
                "profile_id": result["profile_id"],
                "subject_digest": result["subject_digest"],
                "finding_count": result["finding_count"],
            },
            user_id=user["id"],
        )
        return result

    @router.get("/api/reviews")
    async def review_runs(
        http_request: Request,
        subject_id: str | None = None,
        profile_id: str | None = None,
        limit: int = 50,
        tenant: Tenant = Depends(current_tenant),
    ) -> list[dict]:
        return review_service.list_review_runs(
            request_context_factory(http_request, tenant),
            subject_id=subject_id,
            profile_id=profile_id,
            limit=max(1, min(limit, 200)),
        )

    @router.get("/api/reviews/{review_run_id}")
    async def review_run_detail(
        review_run_id: str,
        http_request: Request,
        tenant: Tenant = Depends(current_tenant),
    ) -> dict:
        return review_service.get_review_run(
            request_context_factory(http_request, tenant), review_run_id
        )

    @router.get("/api/review-findings")
    async def review_findings(
        http_request: Request,
        review_run_id: str | None = None,
        subject_id: str | None = None,
        severity: ReviewSeverity | None = None,
        status: ReviewFindingStatus | None = None,
        limit: int = 100,
        tenant: Tenant = Depends(current_tenant),
    ) -> list[dict]:
        return review_service.list_findings(
            request_context_factory(http_request, tenant),
            review_run_id=review_run_id,
            subject_id=subject_id,
            severity=severity.value if severity else None,
            status=status.value if status else None,
            limit=max(1, min(limit, 500)),
        )

    @router.get("/api/review-findings/{finding_id}")
    async def review_finding_detail(
        finding_id: str,
        http_request: Request,
        tenant: Tenant = Depends(current_tenant),
    ) -> dict:
        return review_service.get_finding(
            request_context_factory(http_request, tenant), finding_id
        )

    return router
