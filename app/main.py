"""FastAPI entry point for the public, dependency-light runtime."""

from __future__ import annotations

import json
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .audit import record_request
from .auth import (
    create_login_session,
    current_admin_user,
    current_user,
    ensure_bootstrap_admin,
    hash_password,
    verify_password,
)
from .config import settings
from .core.contracts import ChatCommand, RequestContext
from .core.errors import (
    ApplicationError,
    LLMError,
    ProviderNotConfiguredError,
    ResourceNotFoundError,
)
from .database import (
    create_document,
    create_session,
    get_repository,
    get_session,
    init_db,
    list_sessions,
    search_documents,
)
from .mcp import build_transport_apps, call_local_tool, create_mcp_server, handle_rpc
from .services.gateway import GatewayService
from .services.mcp_probe import MCPProbeService
from .services.memory import MemoryService
from .services.tool_catalog import create_default_tool_catalog
from .services.tools import ToolService
from .tenancy import Tenant, current_tenant

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
tool_catalog = create_default_tool_catalog()
tool_service = ToolService(tool_catalog=tool_catalog)
mcp_server = create_mcp_server(tool_service)
mcp_app, mcp_sse_app = build_transport_apps(mcp_server)
gateway_service = GatewayService(tool_catalog=tool_catalog)
memory_service = MemoryService()
mcp_probe_service = MCPProbeService()


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)


class RegisterRequest(LoginRequest):
    name: str = Field(min_length=1, max_length=100)
    workspace_name: str | None = Field(default=None, min_length=1, max_length=100)


class ModelConfigRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default="", max_length=500)
    input_price: float = Field(default=0, ge=0)
    output_price: float = Field(default=0, ge=0)
    is_default: bool = False


class MCPServerConfigRequest(BaseModel):
    provider_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    url: str = Field(min_length=1, max_length=2000)
    header_credentials: dict[str, str] = Field(default_factory=dict)
    risk: str = Field(default="low", pattern="^(low|medium|high)$")
    required_scopes: list[str] = Field(default_factory=list, max_length=32)
    timeout_seconds: float = Field(default=30, ge=0.1, le=300)
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("url must be HTTP(S) and must not contain credentials")
        return value

    @field_validator("header_credentials")
    @classmethod
    def validate_header_credentials(cls, value: dict[str, str]) -> dict[str, str]:
        reference = re.compile(r"^env://[A-Za-z_][A-Za-z0-9_]*$")
        if not all(header.strip() and reference.fullmatch(item) for header, item in value.items()):
            raise ValueError("header credential values must use env://NAME references")
        return value

    @field_validator("required_scopes")
    @classmethod
    def validate_required_scopes(cls, value: list[str]) -> list[str]:
        if not all(item.strip() and len(item) <= 100 for item in value):
            raise ValueError("required scopes must be non-empty strings")
        return sorted(set(value))


class TenantCreateRequest(BaseModel):
    id: str | None = Field(default=None, min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)


class UserCreateRequest(RegisterRequest):
    role: str = Field(default="member", pattern="^(member|admin)$")


class MCPAuthMiddleware:
    """Resolve every MCP HTTP request into a workspace RequestContext."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(("/mcp", "/mcp-sse")):
            headers = {
                key.decode().lower(): value.decode()
                for key, value in scope.get("headers", [])
            }
            authorization = headers.get("authorization", "")
            token = (
                authorization[7:].strip()
                if authorization.lower().startswith("bearer ")
                else headers.get("x-api-key", "")
            )
            request = Request(scope)
            expected = settings.mcp_api_key
            legacy_key = bool(expected) and secrets.compare_digest(token, expected)
            if legacy_key:
                tenant = Tenant("default", "Default")
                request.state.tenant_id = tenant.id
                request.state.scopes = frozenset(
                    {"tools:write", "tools:high-risk"}
                )
            else:
                try:
                    tenant = await current_tenant(request, authorization)
                except HTTPException:
                    response = JSONResponse(
                        status_code=401,
                        content={"detail": "MCP authentication required"},
                    )
                    await response(scope, receive, send)
                    return
                has_tenant_keys = bool(settings.api_key or settings.tenants_json.strip())
                if expected and not has_tenant_keys and not getattr(
                    request.state, "user_id", None
                ):
                    response = JSONResponse(
                        status_code=401,
                        content={"detail": "MCP authentication required"},
                    )
                    await response(scope, receive, send)
                    return
            try:
                tool_hops = max(
                    0, int(headers.get("x-aigc-lite-mcp-hop", "0"))
                )
            except ValueError:
                tool_hops = 0
            request.state.mcp_context = RequestContext(
                request_id=str(uuid4()),
                workspace_id=tenant.id,
                principal_id=getattr(request.state, "user_id", None),
                api_key_id="legacy-mcp" if legacy_key else None,
                scopes=getattr(request.state, "scopes", frozenset()),
                tool_hops=tool_hops,
            )
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    ensure_bootstrap_admin()
    try:
        async with mcp_server.session_manager.run():
            yield
    except RuntimeError as exc:
        if "can only be called once" not in str(exc):
            raise
        # The SDK manager is intentionally single-use. This branch only helps
        # repeated in-process test clients; a production process starts once.
        yield


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
app.add_middleware(MCPAuthMiddleware)
app.mount(
    "/ui",
    StaticFiles(directory=FRONTEND_DIST if FRONTEND_DIST.exists() else Path(__file__).parent / "static", html=True),
    name="ui",
)
app.mount("/mcp", mcp_app, name="mcp")
app.mount("/mcp-sse", mcp_sse_app, name="mcp-sse")


@app.exception_handler(ApplicationError)
async def application_error_handler(_request: Request, exc: ApplicationError) -> JSONResponse:
    if isinstance(exc, ResourceNotFoundError):
        status_code = 404
    elif isinstance(exc, ProviderNotConfiguredError):
        status_code = 503
    elif isinstance(exc, LLMError):
        status_code = 502
    else:
        status_code = 500
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": str(exc),
            "error": {"code": exc.code.value, "retryable": exc.retryable},
        },
    )


@app.middleware("http")
async def audit_requests(request: Request, call_next):
    response = await call_next(request)
    if request.url.path not in {"/health", "/"} and not request.url.path.startswith(("/ui", "/docs", "/openapi")):
        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id:
            try:
                record_request(
                    tenant_id, request.method, request.url.path, response.status_code,
                    getattr(request.state, "user_id", None),
                )
            except Exception:
                pass
    return response


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    system: str = Field(default="You are a helpful assistant.", max_length=20_000)
    model: str | None = None
    session_id: str | None = None


class SessionRequest(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)


class DocumentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=2_000_000)


def request_context(request: Request, tenant: Tenant) -> RequestContext:
    return RequestContext(
        request_id=str(uuid4()),
        workspace_id=tenant.id,
        principal_id=getattr(request.state, "user_id", None),
        scopes=getattr(request.state, "scopes", frozenset()),
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get("/", include_in_schema=False)
async def home() -> RedirectResponse:
    return RedirectResponse("/ui/")


@app.post("/api/auth/register")
async def register(request: RegisterRequest) -> dict[str, str]:
    if not settings.allow_signup:
        raise HTTPException(status_code=403, detail="Registration is disabled")
    repository = get_repository()
    if repository.get_user_by_email(request.email):
        raise HTTPException(status_code=409, detail="Email is already registered")
    tenant = repository.create_tenant(request.workspace_name or request.name)
    user = repository.create_user(
        tenant["id"], request.email, request.name, hash_password(request.password), role="admin"
    )
    return {"access_token": create_login_session(user), "token_type": "bearer", "tenant_id": tenant["id"]}


@app.post("/api/auth/login")
async def login(request: LoginRequest) -> dict[str, str]:
    user = get_repository().get_user_by_email(request.email)
    if not user or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return {"access_token": create_login_session(user), "token_type": "bearer", "tenant_id": user["tenant_id"]}


@app.get("/api/auth/me")
async def me(user: dict = Depends(current_user)) -> dict:
    return {key: value for key, value in user.items() if key != "password_hash"}


@app.post("/api/sessions")
async def new_session(request: SessionRequest, tenant: Tenant = Depends(current_tenant)) -> dict:
    return create_session(tenant.id, request.title)


@app.get("/api/sessions")
async def sessions(tenant: Tenant = Depends(current_tenant)) -> list[dict]:
    return list_sessions(tenant.id)


@app.get("/api/sessions/{session_id}")
async def session(session_id: str, tenant: Tenant = Depends(current_tenant)) -> dict:
    value = get_session(tenant.id, session_id)
    if value is None:
        return JSONResponse(status_code=404, content={"detail": "Session not found"})
    return value


@app.post("/api/chat")
async def chat(
    request: ChatRequest,
    http_request: Request,
    tenant: Tenant = Depends(current_tenant),
) -> dict[str, str]:
    result = await gateway_service.chat(
        ChatCommand(
            prompt=request.prompt,
            system=request.system,
            requested_model=request.model,
            session_id=request.session_id,
        ),
        request_context(http_request, tenant),
    )
    return {
        "content": result.content,
        "session_id": result.session_id,
        "run_id": result.run_id,
    }


@app.post("/api/chat/stream")
async def chat_stream(
    request: ChatRequest,
    http_request: Request,
    tenant: Tenant = Depends(current_tenant),
) -> StreamingResponse:
    result = await gateway_service.stream_chat(
        ChatCommand(
            prompt=request.prompt,
            system=request.system,
            requested_model=request.model,
            session_id=request.session_id,
        ),
        request_context(http_request, tenant),
    )

    async def events():
        try:
            async for chunk in result.chunks:
                yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
        except ApplicationError as exc:
            yield f"data: {json.dumps({'error': str(exc), 'code': exc.code.value}, ensure_ascii=False)}\n\n"
            return
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"X-Run-Id": result.run_id, "X-Session-Id": result.session_id},
    )


@app.get("/api/runs")
async def runs(
    http_request: Request,
    limit: int = 50,
    tenant: Tenant = Depends(current_tenant),
) -> list[dict]:
    return memory_service.list_runs(request_context(http_request, tenant), limit)


@app.get("/api/runs/{run_id}")
async def run_detail(
    run_id: str,
    http_request: Request,
    tenant: Tenant = Depends(current_tenant),
) -> dict:
    return memory_service.get_run(request_context(http_request, tenant), run_id)


@app.get("/api/search")
async def memory_search(
    http_request: Request,
    q: str = "",
    limit: int = 20,
    tenant: Tenant = Depends(current_tenant),
) -> list[dict]:
    return memory_service.search(request_context(http_request, tenant), q, limit)


@app.post("/api/knowledge/documents")
async def add_document(request: DocumentRequest, tenant: Tenant = Depends(current_tenant)) -> dict:
    return create_document(tenant.id, request.name, request.content)


@app.post("/api/knowledge/upload")
async def upload_document(file: UploadFile = File(...), tenant: Tenant = Depends(current_tenant)) -> dict:
    if not file.filename or not file.filename.lower().endswith((".txt", ".md", ".csv", ".json")):
        return JSONResponse(status_code=415, content={"detail": "Only text, Markdown, CSV, and JSON files are supported"})
    content = (await file.read(2_000_001)).decode("utf-8", errors="replace")
    if not content.strip() or len(content) > 2_000_000:
        return JSONResponse(status_code=400, content={"detail": "Document must contain 1-2,000,000 characters"})
    return create_document(tenant.id, file.filename, content)


@app.get("/api/knowledge/search")
async def knowledge_search(q: str = "", limit: int = 5, tenant: Tenant = Depends(current_tenant)) -> list[dict]:
    return search_documents(tenant.id, q, limit)


@app.get("/api/models")
async def models(tenant: Tenant = Depends(current_tenant)) -> list[dict]:
    return get_repository().list_model_configs(tenant.id)


@app.post("/api/models")
async def save_model(request: ModelConfigRequest, user: dict = Depends(current_admin_user)) -> dict:
    return get_repository().save_model_config(user["tenant_id"], request.model_dump())


@app.get("/api/mcp-servers")
async def mcp_servers(user: dict = Depends(current_admin_user)) -> list[dict]:
    return get_repository().list_mcp_servers(user["tenant_id"])


@app.post("/api/mcp-servers")
async def save_mcp_server(
    request: MCPServerConfigRequest,
    user: dict = Depends(current_admin_user),
) -> dict:
    if request.provider_id == "local":
        raise HTTPException(status_code=409, detail="Provider id is reserved")
    return get_repository().save_mcp_server(
        user["tenant_id"], request.model_dump()
    )


@app.post("/api/mcp-servers/{server_id}/probe")
async def probe_mcp_server(
    server_id: str,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict:
    tenant = Tenant(user["tenant_id"], user["tenant_id"])
    return await mcp_probe_service.probe(
        request_context(http_request, tenant), server_id
    )


@app.delete("/api/mcp-servers/{server_id}")
async def delete_mcp_server(
    server_id: str,
    user: dict = Depends(current_admin_user),
) -> dict[str, bool]:
    deleted = get_repository().delete_mcp_server(user["tenant_id"], server_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return {"deleted": True}


@app.get("/api/usage")
async def usage(tenant: Tenant = Depends(current_tenant)) -> dict:
    return get_repository().usage_summary(tenant.id)


@app.get("/api/audit")
async def audit(limit: int = 100, user: dict = Depends(current_user)) -> list[dict]:
    return get_repository().list_audit(user["tenant_id"], limit)


@app.get("/api/admin/tenants")
async def tenants(_user: dict = Depends(current_admin_user)) -> list[dict]:
    return get_repository().list_tenants()


@app.post("/api/admin/tenants")
async def create_tenant(request: TenantCreateRequest, _user: dict = Depends(current_admin_user)) -> dict:
    return get_repository().create_tenant(request.name, request.id)


@app.get("/api/admin/tenants/{tenant_id}/users")
async def tenant_users(tenant_id: str, _user: dict = Depends(current_admin_user)) -> list[dict]:
    if not get_repository().get_tenant(tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")
    return get_repository().list_users(tenant_id)


@app.post("/api/admin/tenants/{tenant_id}/users")
async def create_tenant_user(
    tenant_id: str, request: UserCreateRequest, _user: dict = Depends(current_admin_user)
) -> dict:
    repository = get_repository()
    if not repository.get_tenant(tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")
    if repository.get_user_by_email(request.email):
        raise HTTPException(status_code=409, detail="Email is already registered")
    return repository.create_user(
        tenant_id, request.email, request.name, hash_password(request.password), request.role
    )


@app.post("/mcp-legacy")
async def mcp(request: dict, tenant: Tenant = Depends(current_tenant)) -> dict:
    del tenant
    if request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return handle_rpc(request)
    if not isinstance(request.get("params") or {}, dict):
        return handle_rpc(request)
    if request.get("method") == "tools/call":
        return await call_local_tool(request)
    return handle_rpc(request)


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=settings.debug)


if __name__ == "__main__":
    run()
