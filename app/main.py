"""FastAPI entry point for the public, dependency-light runtime."""

from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .adapters.kernel_verification import KernelVerifierRegistry
from .adapters.scheduling import TimeWheelScheduler
from .api.configuration import create_configuration_router
from .api.research import create_research_router
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
    AgentLimitError,
    AgentWallTimeLimitError,
    ApplicationError,
    InvalidArtifactError,
    InvalidEvidenceError,
    InvalidKernelVerificationError,
    InvalidScheduleError,
    InvalidVerificationResultError,
    KernelVerifierNotConfiguredError,
    LLMError,
    ProviderNotConfiguredError,
    ResourceConflictError,
    ResourceNotFoundError,
    RunNotActiveError,
    ScheduleNotActiveError,
)
from .core.scheduling import (
    ScheduledTask,
    ScheduleKind,
    ScheduleSpec,
    ScheduleTaskCommand,
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
from .services.artifacts import ArtifactService
from .services.assurance import AssuranceBundleService
from .services.credentials import CredentialService
from .services.decision_lab import DecisionLabService
from .services.evidence import EvidenceService
from .services.gateway import GatewayService
from .services.http_poll import HTTPPollService
from .services.kernel_verification import KernelVerificationService
from .services.mcp_probe import MCPProbeService
from .services.memory import MemoryService
from .services.qualification import QualificationService
from .services.readiness import ReadinessService
from .services.research_registry import ResearchRegistryService
from .services.scheduler import SchedulerService
from .services.task_runner import TaskRunner
from .services.tool_catalog import create_default_tool_catalog
from .services.tools import ToolService
from .services.verification_runner import VerificationRunner
from .startup import validate_startup_security
from .tenancy import Tenant, _role_scopes, current_tenant

PACKAGED_UI = Path(__file__).parent / "static"
tool_catalog = create_default_tool_catalog()
artifact_service = ArtifactService()
tool_service = ToolService(
    tool_catalog=tool_catalog, artifact_service=artifact_service
)
mcp_server = create_mcp_server(tool_service)
mcp_app, mcp_sse_app = build_transport_apps(mcp_server)
gateway_service = GatewayService(
    tool_catalog=tool_catalog, artifact_service=artifact_service
)
memory_service = MemoryService()
mcp_probe_service = MCPProbeService()
http_poll_service = HTTPPollService()
credential_service = CredentialService()
evidence_service = EvidenceService()
decision_lab_service = DecisionLabService(
    artifact_service=artifact_service, evidence_service=evidence_service
)
research_registry_service = ResearchRegistryService(
    artifact_service=artifact_service, evidence_service=evidence_service
)
kernel_verifier_registry = KernelVerifierRegistry.from_config(
    lean_executable=settings.lean_executable,
    coq_executable=settings.coq_executable,
    timeout_seconds=settings.kernel_verify_timeout_seconds,
    max_output_bytes=settings.kernel_verify_max_output_bytes,
    memory_mb=settings.kernel_verify_memory_mb,
)
kernel_verification_service = KernelVerificationService(
    registry=kernel_verifier_registry,
    artifact_service=artifact_service,
    research_registry_service=research_registry_service,
    max_source_bytes=settings.kernel_verify_max_source_bytes,
)
assurance_bundle_service = AssuranceBundleService()
qualification_service = QualificationService(
    artifact_service=artifact_service, evidence_service=evidence_service
)
verification_runner = VerificationRunner(
    gateway_service=gateway_service,
    research_registry_service=research_registry_service,
    artifact_service=artifact_service,
)
scheduler_service = SchedulerService()
task_runner = TaskRunner(
    gateway_service=gateway_service,
    http_poll_service=http_poll_service,
    mcp_probe_service=mcp_probe_service,
    tool_service=tool_service,
    verification_runner=verification_runner,
)
timewheel_scheduler = TimeWheelScheduler(
    scheduler_service=scheduler_service,
    task_runner=task_runner,
    tick_seconds=settings.scheduler_tick_seconds,
    reconcile_seconds=settings.scheduler_reconcile_seconds,
    max_concurrency=settings.scheduler_max_concurrency,
)
readiness_service = ReadinessService(
    scheduler_enabled=lambda: settings.scheduler_enabled,
    scheduler_running=lambda: timewheel_scheduler.running,
)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)


class RegisterRequest(LoginRequest):
    name: str = Field(min_length=1, max_length=100)
    workspace_name: str | None = Field(default=None, min_length=1, max_length=100)


class UserCreateRequest(RegisterRequest):
    role: str = Field(default="member", pattern="^(member|admin)$")


class ScheduleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target: str = Field(
        default="agent.chat",
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.:-]*$",
    )
    payload: dict[str, Any] = Field(default_factory=dict)
    kind: ScheduleKind | None = None
    cadence: Literal["daily", "weekly"] | None = None
    run_at: datetime
    interval_seconds: float | None = Field(default=None, gt=0)


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
                request.state.scopes = _role_scopes("admin")
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
    validate_startup_security(settings)
    init_db()
    ensure_bootstrap_admin()
    validate_startup_security(settings, get_repository())
    if settings.scheduler_enabled:
        await timewheel_scheduler.start()
    try:
        try:
            async with mcp_server.session_manager.run():
                yield
        except RuntimeError as exc:
            if "can only be called once" not in str(exc):
                raise
            # The SDK manager is intentionally single-use. This branch only helps
            # repeated in-process test clients; a production process starts once.
            yield
    finally:
        if settings.scheduler_enabled:
            await timewheel_scheduler.stop()


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
app.add_middleware(MCPAuthMiddleware)
app.mount(
    "/ui",
    StaticFiles(directory=PACKAGED_UI, html=True),
    name="ui",
)
app.mount("/mcp", mcp_app, name="mcp")
app.mount("/mcp-sse", mcp_sse_app, name="mcp-sse")


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    safe_errors = [
        {
            "type": error["type"],
            "loc": error["loc"],
            "msg": error["msg"],
        }
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": safe_errors})


@app.exception_handler(ApplicationError)
async def application_error_handler(_request: Request, exc: ApplicationError) -> JSONResponse:
    if isinstance(exc, ResourceNotFoundError):
        status_code = 404
    elif isinstance(
        exc, (ResourceConflictError, RunNotActiveError, ScheduleNotActiveError)
    ):
        status_code = 409
    elif isinstance(
        exc,
        (
            InvalidArtifactError,
            InvalidEvidenceError,
            InvalidKernelVerificationError,
            InvalidScheduleError,
            InvalidVerificationResultError,
        ),
    ):
        status_code = 422
    elif isinstance(exc, AgentWallTimeLimitError):
        status_code = 504
    elif isinstance(exc, AgentLimitError):
        status_code = 429
    elif isinstance(exc, ProviderNotConfiguredError):
        status_code = 503
    elif isinstance(exc, KernelVerifierNotConfiguredError):
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


def _schedule_response(task: ScheduledTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "workspace_id": task.workspace_id,
        "name": task.name,
        "target": task.target,
        "payload": task.payload,
        "kind": task.kind.value,
        "status": task.status.value,
        "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
        "interval_seconds": task.interval_seconds,
        "last_run_at": task.last_run_at.isoformat() if task.last_run_at else None,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def _schedule_context(request: Request, user: dict) -> RequestContext:
    return request_context(
        request, Tenant(user["tenant_id"], user["tenant_id"])
    )


def _audit_schedule_action(action: str, task: ScheduledTask, user: dict) -> None:
    get_repository().write_audit(
        user["tenant_id"],
        f"schedule.{action}",
        f"/api/schedules/{task.id}",
        {
            "task_id": task.id,
            "target": task.target,
            "kind": task.kind.value,
            "status": task.status.value,
        },
        user_id=user["id"],
    )


def _schedule_spec(request: ScheduleCreateRequest) -> ScheduleSpec:
    if request.cadence is not None:
        if request.kind not in {None, ScheduleKind.INTERVAL}:
            raise InvalidScheduleError(
                "kind", "Daily or weekly cadence requires an interval schedule"
            )
        if request.interval_seconds is not None:
            raise InvalidScheduleError(
                "interval_seconds",
                "Cadence and interval_seconds cannot both be specified",
            )
        return ScheduleSpec(
            kind=ScheduleKind.INTERVAL,
            run_at=request.run_at,
            interval_seconds={"daily": 86400.0, "weekly": 604800.0}[
                request.cadence
            ],
        )
    return ScheduleSpec(
        kind=request.kind or ScheduleKind.ONCE,
        run_at=request.run_at,
        interval_seconds=request.interval_seconds,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get("/ready")
async def ready() -> JSONResponse:
    is_ready, payload = readiness_service.snapshot()
    return JSONResponse(status_code=200 if is_ready else 503, content=payload)


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
    return {
        "access_token": create_login_session(user),
        "token_type": "bearer",
        "tenant_id": tenant["id"],
    }


@app.post("/api/auth/login")
async def login(request: LoginRequest) -> dict[str, str]:
    user = get_repository().get_user_by_email(request.email)
    if not user or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return {
        "access_token": create_login_session(user),
        "token_type": "bearer",
        "tenant_id": user["tenant_id"],
    }


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


@app.get("/api/artifacts")
async def artifacts(
    http_request: Request,
    run_id: str | None = None,
    limit: int = 50,
    tenant: Tenant = Depends(current_tenant),
) -> list[dict]:
    return artifact_service.list(
        request_context(http_request, tenant), limit=limit, run_id=run_id
    )


@app.get("/api/artifacts/{artifact_id}")
async def artifact_detail(
    artifact_id: str,
    http_request: Request,
    tenant: Tenant = Depends(current_tenant),
) -> dict:
    return artifact_service.get(request_context(http_request, tenant), artifact_id)


@app.post("/api/runs/{run_id}/cancel", status_code=202)
async def cancel_run(
    run_id: str,
    http_request: Request,
    tenant: Tenant = Depends(current_tenant),
) -> dict[str, str | bool]:
    gateway_service.cancel_run(request_context(http_request, tenant), run_id)
    return {"run_id": run_id, "cancel_requested": True}


@app.post("/api/schedules", status_code=201)
async def create_schedule(
    request: ScheduleCreateRequest,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict[str, Any]:
    task_runner.validate(request.target, request.payload)
    task = scheduler_service.schedule(
        _schedule_context(http_request, user),
        ScheduleTaskCommand(
            name=request.name,
            target=request.target,
            payload=request.payload,
            schedule=_schedule_spec(request),
        ),
    )
    _audit_schedule_action("create", task, user)
    timewheel_scheduler.notify(task)
    return _schedule_response(task)


@app.get("/api/schedules")
async def schedules(
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> list[dict[str, Any]]:
    tasks = scheduler_service.list(_schedule_context(http_request, user))
    return [_schedule_response(task) for task in tasks]


@app.get("/api/schedules/{task_id}")
async def schedule_detail(
    task_id: str,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict[str, Any]:
    task = scheduler_service.get(_schedule_context(http_request, user), task_id)
    return _schedule_response(task)


def _control_schedule(
    action: str,
    task_id: str,
    http_request: Request,
    user: dict,
) -> dict[str, Any]:
    context = _schedule_context(http_request, user)
    transition = {
        "pause": scheduler_service.pause,
        "resume": scheduler_service.resume,
        "cancel": scheduler_service.cancel,
    }[action]
    task = transition(context, task_id)
    _audit_schedule_action(action, task, user)
    timewheel_scheduler.notify(task)
    return _schedule_response(task)


@app.post("/api/schedules/{task_id}/pause")
async def pause_schedule(
    task_id: str,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict[str, Any]:
    return _control_schedule("pause", task_id, http_request, user)


@app.post("/api/schedules/{task_id}/resume")
async def resume_schedule(
    task_id: str,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict[str, Any]:
    return _control_schedule("resume", task_id, http_request, user)


@app.post("/api/schedules/{task_id}/cancel")
async def cancel_schedule(
    task_id: str,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict[str, Any]:
    return _control_schedule("cancel", task_id, http_request, user)


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
        return JSONResponse(
            status_code=415,
            content={"detail": "Only text, Markdown, CSV, and JSON files are supported"},
        )
    content = (await file.read(2_000_001)).decode("utf-8", errors="replace")
    if not content.strip() or len(content) > 2_000_000:
        return JSONResponse(status_code=400, content={"detail": "Document must contain 1-2,000,000 characters"})
    return create_document(tenant.id, file.filename, content)


@app.get("/api/knowledge/search")
async def knowledge_search(q: str = "", limit: int = 5, tenant: Tenant = Depends(current_tenant)) -> list[dict]:
    return search_documents(tenant.id, q, limit)


@app.get("/api/usage")
async def usage(tenant: Tenant = Depends(current_tenant)) -> dict:
    return get_repository().usage_summary(tenant.id)


@app.get("/api/audit")
async def audit(limit: int = 100, user: dict = Depends(current_user)) -> list[dict]:
    return get_repository().list_audit(user["tenant_id"], limit)


@app.get("/api/admin/tenants")
async def tenants(user: dict = Depends(current_admin_user)) -> list[dict]:
    tenant = get_repository().get_tenant(user["tenant_id"])
    return [tenant] if tenant is not None else []


@app.get("/api/admin/tenants/{tenant_id}/users")
async def tenant_users(tenant_id: str, user: dict = Depends(current_admin_user)) -> list[dict]:
    if tenant_id != user["tenant_id"] or not get_repository().get_tenant(tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")
    return get_repository().list_users(tenant_id)


@app.post("/api/admin/tenants/{tenant_id}/users")
async def create_tenant_user(
    tenant_id: str, request: UserCreateRequest, user: dict = Depends(current_admin_user)
) -> dict:
    repository = get_repository()
    if tenant_id != user["tenant_id"] or not repository.get_tenant(tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")
    if repository.get_user_by_email(request.email):
        raise HTTPException(status_code=409, detail="Email is already registered")
    return repository.create_user(
        tenant_id, request.email, request.name, hash_password(request.password), request.role
    )


app.include_router(
    create_configuration_router(
        credential_service=credential_service,
        mcp_probe_service=mcp_probe_service,
        request_context_factory=request_context,
    )
)
app.include_router(
    create_research_router(
        assurance_bundle_service=assurance_bundle_service,
        decision_lab_service=decision_lab_service,
        evidence_service=evidence_service,
        kernel_verification_service=kernel_verification_service,
        qualification_service=qualification_service,
        research_registry_service=research_registry_service,
        verification_runner=verification_runner,
        request_context_factory=request_context,
    )
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
