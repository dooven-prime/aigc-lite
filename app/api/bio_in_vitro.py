"""Authenticated, data-only in-vitro observation Case endpoint."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from ..auth import current_admin_user
from ..core.contracts import RequestContext
from ..database import get_repository
from ..services.bio_in_vitro_case import BioInVitroCaseService
from ..tenancy import Tenant, current_tenant

RequestContextFactory = Callable[[Request, Tenant], RequestContext]


def create_bio_in_vitro_router(
    *, service: BioInVitroCaseService, request_context_factory: RequestContextFactory
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/research/bio-in-vitro/contract")
    async def contract(_tenant: Tenant = Depends(current_tenant)) -> dict:
        return service.contract()

    @router.post("/api/research/bio-in-vitro/witnesses", status_code=201)
    async def submit(
        witness: dict, request: Request, user: dict = Depends(current_admin_user)
    ) -> dict:
        context = request_context_factory(
            request, Tenant(user["tenant_id"], user["tenant_id"])
        )
        result = await run_in_threadpool(service.submit, context, witness)
        get_repository().write_audit(
            user["tenant_id"],
            "bio_in_vitro.submit",
            "/api/research/bio-in-vitro/witnesses",
            {
                "case_id": result["case_id"],
                "claim_revision_id": result["claim_revision_id"],
                "run_id": result["run_id"],
                "verdict": result["evaluation"]["verdict"],
            },
            user_id=user["id"],
        )
        return result

    return router
