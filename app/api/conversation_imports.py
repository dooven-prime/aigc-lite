"""HTTP control surface for previewed, immutable conversation imports."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from ..auth import current_admin_user
from ..config import settings
from ..core.contracts import RequestContext
from ..core.errors import InvalidConversationImportError
from ..database import get_repository
from ..services.conversation_imports import ConversationImportService
from ..tenancy import Tenant, current_tenant

RequestContextFactory = Callable[[Request, Tenant], RequestContext]


async def _read_source(upload: UploadFile) -> tuple[str, bytes]:
    maximum = settings.max_body_bytes
    content = await upload.read(maximum + 1)
    if not content:
        raise InvalidConversationImportError("file", "Import source is empty")
    if len(content) > maximum:
        raise InvalidConversationImportError(
            "file", f"Import source exceeds {maximum} bytes"
        )
    return upload.filename or "conversations.json", content


def create_conversation_import_router(
    *,
    import_service: ConversationImportService,
    request_context_factory: RequestContextFactory,
) -> APIRouter:
    router = APIRouter()

    def admin_context(http_request: Request, user: dict) -> RequestContext:
        return request_context_factory(
            http_request, Tenant(user["tenant_id"], user["tenant_id"])
        )

    @router.get("/api/conversation-importers")
    async def conversation_importers(
        _tenant: Tenant = Depends(current_tenant),
    ) -> list[dict]:
        return import_service.list_importers()

    @router.post("/api/conversation-imports/preview")
    async def preview_conversation_import(
        http_request: Request,
        importer_id: str = Form(..., min_length=1, max_length=200),
        file: UploadFile = File(...),
        user: dict = Depends(current_admin_user),
    ) -> dict:
        source_name, source_bytes = await _read_source(file)
        return import_service.preview(
            admin_context(http_request, user),
            importer_id=importer_id,
            source_name=source_name,
            source_bytes=source_bytes,
        )

    @router.post("/api/conversation-imports", status_code=201)
    async def commit_conversation_import(
        http_request: Request,
        importer_id: str = Form(..., min_length=1, max_length=200),
        expected_preview_hash: str = Form(
            ..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
        ),
        file: UploadFile = File(...),
        user: dict = Depends(current_admin_user),
    ) -> dict:
        source_name, source_bytes = await _read_source(file)
        result = import_service.commit(
            admin_context(http_request, user),
            importer_id=importer_id,
            source_name=source_name,
            source_bytes=source_bytes,
            expected_preview_hash=expected_preview_hash,
        )
        get_repository().write_audit(
            user["tenant_id"],
            "conversation_import.commit",
            "/api/conversation-imports",
            {
                "batch_id": result["id"],
                "importer_id": result["importer_id"],
                "source_content_hash": result["source_content_hash"],
                "conversation_count": result["conversation_count"],
                "message_count": result["message_count"],
                "deduplicated": result.get("deduplicated", False),
            },
            user_id=user["id"],
        )
        return ConversationImportService._summary_projection(result)

    @router.get("/api/conversation-imports")
    async def conversation_imports(
        http_request: Request,
        limit: int = 50,
        tenant: Tenant = Depends(current_tenant),
    ) -> list[dict]:
        return import_service.list(
            request_context_factory(http_request, tenant),
            max(1, min(limit, 200)),
        )

    @router.get("/api/conversation-imports/{batch_id}")
    async def conversation_import_detail(
        batch_id: str,
        http_request: Request,
        include_messages: bool = False,
        tenant: Tenant = Depends(current_tenant),
    ) -> dict:
        return import_service.get(
            request_context_factory(http_request, tenant),
            batch_id,
            include_messages=include_messages,
        )

    return router
