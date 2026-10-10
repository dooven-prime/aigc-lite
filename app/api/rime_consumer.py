"""Authenticated, data-only RIME finite consumer witness endpoint."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from ..auth import current_admin_user
from ..core.contracts import RequestContext
from ..database import get_repository
from ..services.rime_consumer_case import RimeConsumerCaseService
from ..tenancy import Tenant, current_tenant

RequestContextFactory = Callable[[Request, Tenant], RequestContext]


def create_rime_consumer_router(
    *, service: RimeConsumerCaseService, request_context_factory: RequestContextFactory
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/research/rime-consumer/contract")
    async def contract(_tenant: Tenant = Depends(current_tenant)) -> dict:
        return service.adapter.contract()

    @router.post("/api/research/rime-consumer/witnesses", status_code=201)
    async def submit(
        witness: dict, request: Request, user: dict = Depends(current_admin_user)
    ) -> dict:
        context = request_context_factory(
            request, Tenant(user["tenant_id"], user["tenant_id"])
        )
        result = await run_in_threadpool(service.submit, context, witness)
        get_repository().write_audit(
            user["tenant_id"], "rime_consumer.submit",
            "/api/research/rime-consumer/witnesses",
            {"case_id": result["case_id"], "run_id": result["run_id"],
             "witness_sha256": result["witness_sha256"],
             "mathematical_witness_sha256": result["mathematical_witness_sha256"],
             "prior_case_id": (result["prior_link"] or {}).get("case_id"),
             "outcome": result["result"]["outcome"]},
            user_id=user["id"],
        )
        return result

    return router
