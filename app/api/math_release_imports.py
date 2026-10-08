"""Candidate-only mathematics release import control API."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..auth import current_admin_user
from ..core.contracts import RequestContext
from ..database import get_repository
from ..services.math_release_imports import MathReleaseImportService
from ..tenancy import Tenant, current_tenant

RequestContextFactory = Callable[[Request, Tenant], RequestContext]


class MathReleasePreviewRequest(BaseModel):
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")


class MathReleaseCommitRequest(MathReleasePreviewRequest):
    expected_preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def create_math_release_import_router(
    *,
    import_service: MathReleaseImportService,
    request_context_factory: RequestContextFactory,
) -> APIRouter:
    router = APIRouter()

    def admin_context(request: Request, user: dict) -> RequestContext:
        return request_context_factory(request, Tenant(user["tenant_id"], user["tenant_id"]))

    @router.post("/api/research/math-release-imports/preview")
    async def preview(
        payload: MathReleasePreviewRequest,
        request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        return await run_in_threadpool(
            import_service.preview,
            admin_context(request, user),
            payload.source_commit,
        )

    @router.post("/api/research/math-release-imports", status_code=201)
    async def commit(
        payload: MathReleaseCommitRequest,
        request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        result = await run_in_threadpool(
            import_service.commit,
            admin_context(request, user),
            payload.source_commit,
            payload.expected_preview_hash,
        )
        get_repository().write_audit(
            user["tenant_id"],
            "math_release_import.commit",
            "/api/research/math-release-imports",
            {
                "batch_id": result["id"],
                "source_commit": result["source_commit"],
                "preview_hash": result["preview_hash"],
                "family_count": result["family_count"],
                "manuscript_count": result["manuscript_count"],
                "deduplicated": result["deduplicated"],
            },
            user_id=user["id"],
        )
        return result

    @router.get("/api/research/math-release-imports")
    async def list_imports(
        request: Request,
        limit: int = 50,
        tenant: Tenant = Depends(current_tenant),
    ) -> list[dict]:
        return import_service.list(
            request_context_factory(request, tenant), max(1, min(limit, 200))
        )

    @router.get("/api/research/math-release-imports/{import_id}")
    async def get_import(
        import_id: str,
        request: Request,
        include_families: bool = False,
        tenant: Tenant = Depends(current_tenant),
    ) -> dict:
        return import_service.get(
            request_context_factory(request, tenant),
            import_id,
            include_families=include_families,
        )

    return router
