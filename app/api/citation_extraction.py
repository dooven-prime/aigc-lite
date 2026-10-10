"""Admin-only preview/commit interface for candidate citation extraction."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from ..auth import current_admin_user
from ..core.contracts import RequestContext
from ..core.errors import InvalidArtifactError
from ..database import get_repository
from ..repository import Repository
from ..services.citation_extraction import MAX_PDF_BYTES, CitationExtractionService
from ..tenancy import Tenant

RequestContextFactory = Callable[[Request, Tenant], RequestContext]


async def _read_pdf(file: UploadFile) -> bytes:
    content = await file.read(MAX_PDF_BYTES + 1)
    if not content or len(content) > MAX_PDF_BYTES:
        raise InvalidArtifactError("file", "PDF must be nonempty and no larger than 4 MB")
    return content


def create_citation_extraction_router(
    *,
    service: CitationExtractionService,
    request_context_factory: RequestContextFactory,
    repository_provider: Callable[[], Repository] = get_repository,
) -> APIRouter:
    router = APIRouter()

    def admin_context(request: Request, user: dict) -> RequestContext:
        return request_context_factory(request, Tenant(user["tenant_id"], user["tenant_id"]))

    @router.post("/api/research/citation-extractions/preview")
    async def preview(
        request: Request,
        source_uri: str = Form(...),
        proposal_json: str | None = Form(None),
        file: UploadFile = File(...),
        user: dict = Depends(current_admin_user),
    ) -> dict:
        content = await _read_pdf(file)
        return await run_in_threadpool(
            service.preview, admin_context(request, user), content, source_uri, proposal_json
        )

    @router.post("/api/research/citation-extractions", status_code=201)
    async def commit(
        request: Request,
        source_uri: str = Form(...),
        expected_preview_hash: str = Form(..., pattern=r"^[0-9a-f]{64}$"),
        proposal_json: str | None = Form(None),
        file: UploadFile = File(...),
        user: dict = Depends(current_admin_user),
    ) -> dict:
        content = await _read_pdf(file)
        result = await run_in_threadpool(
            service.commit,
            admin_context(request, user),
            content,
            source_uri,
            expected_preview_hash,
            proposal_json,
        )
        repository_provider().write_audit(
            user["tenant_id"],
            "citation_extraction.commit",
            "/api/research/citation-extractions",
            {"artifact_id": result["artifact_id"], "pdf_sha256": result["pdf_sha256"]},
            user_id=user["id"],
        )
        return result

    return router
