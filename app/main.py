"""FastAPI entry point for the public, dependency-light runtime."""

from __future__ import annotations

import json
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, field_validator

from . import __version__
from .adapters.scheduling import TimeWheelScheduler
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
    InvalidScheduleError,
    InvalidVerificationResultError,
    LLMError,
    ProviderNotConfiguredError,
    ResourceConflictError,
    ResourceNotFoundError,
    RunNotActiveError,
    ScheduleNotActiveError,
)
from .core.research import (
    ClaimPromotionStage,
    ClaimRelationDraft,
    ClaimRelationType,
    VerificationAttemptDraft,
    VerificationKind,
    VerificationOutcome,
    VerificationPlanDraft,
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
from .services.credentials import CredentialService
from .services.decision_lab import DecisionLabService
from .services.evidence import EvidenceService
from .services.gateway import GatewayService
from .services.http_poll import HTTPPollService
from .services.mcp_probe import MCPProbeService
from .services.memory import MemoryService
from .services.research_registry import ResearchRegistryService
from .services.scheduler import SchedulerService
from .services.task_runner import TaskRunner
from .services.tool_catalog import create_default_tool_catalog
from .services.tools import ToolService
from .services.verification_runner import VerificationRunner
from .tenancy import Tenant, _role_scopes, current_tenant

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
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
        env_reference = re.compile(r"^env://[A-Za-z_][A-Za-z0-9_]*$")
        encrypted_reference = re.compile(
            r"^encrypted-db://credential/([0-9a-fA-F-]{36})$"
        )
        for header, item in value.items():
            if not header.strip():
                raise ValueError("header names must not be empty")
            if env_reference.fullmatch(item):
                continue
            match = encrypted_reference.fullmatch(item)
            if match is not None:
                try:
                    UUID(match.group(1))
                    continue
                except ValueError:
                    pass
            raise ValueError(
                "header credential values must use env://NAME or "
                "encrypted-db://credential/UUID references"
            )
        return value

    @field_validator("required_scopes")
    @classmethod
    def validate_required_scopes(cls, value: list[str]) -> list[str]:
        if not all(item.strip() and len(item) <= 100 for item in value):
            raise ValueError("required scopes must be non-empty strings")
        return sorted(set(value))


class UserCreateRequest(RegisterRequest):
    role: str = Field(default="member", pattern="^(member|admin)$")


class CredentialCreateRequest(BaseModel):
    name: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )
    secret: SecretStr = Field(min_length=1, max_length=10_000)


class CredentialReplaceRequest(BaseModel):
    secret: SecretStr = Field(min_length=1, max_length=10_000)


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
    init_db()
    ensure_bootstrap_admin()
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
    StaticFiles(directory=FRONTEND_DIST if FRONTEND_DIST.exists() else Path(__file__).parent / "static", html=True),
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


class ClaimRelationRequest(BaseModel):
    target_claim_id: str = Field(min_length=1, max_length=100)
    relation_type: ClaimRelationType
    rationale: str = Field(min_length=1, max_length=4_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)


class RelationWithdrawalRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2_000)


class VerificationAttemptRequest(BaseModel):
    kind: VerificationKind
    outcome: VerificationOutcome
    method: str = Field(min_length=1, max_length=4_000)
    scope: str = Field(min_length=1, max_length=8_000)
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    independent: bool = False
    run_id: str | None = Field(default=None, max_length=100)
    artifact_ids: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PromotionGateRequest(BaseModel):
    target_stage: ClaimPromotionStage


class VerificationPlanRequest(BaseModel):
    plan_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    name: str = Field(min_length=1, max_length=200)
    kind: VerificationKind
    method: str = Field(min_length=1, max_length=4_000)
    scope: str = Field(min_length=1, max_length=8_000)
    prompt: str = Field(min_length=1, max_length=40_000)
    system: str = Field(
        default=(
            "Act as a careful research verification agent. Use only the supplied claim, "
            "declared sources, and auditable tool results."
        ),
        min_length=1,
        max_length=20_000,
    )
    model: str | None = Field(default=None, max_length=200)
    auto_promote: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


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


@app.get("/api/evidence/protocols")
async def evidence_protocols(
    http_request: Request,
    profile: str | None = None,
    limit: int = 50,
    tenant: Tenant = Depends(current_tenant),
) -> list[dict]:
    return evidence_service.list_protocols(
        request_context(http_request, tenant), profile=profile, limit=limit
    )


@app.get("/api/evidence/protocols/{protocol_id}")
async def evidence_protocol_detail(
    protocol_id: str,
    http_request: Request,
    tenant: Tenant = Depends(current_tenant),
) -> dict:
    return evidence_service.get_protocol(
        request_context(http_request, tenant), protocol_id
    )


@app.get("/api/research-registry")
async def research_registry(
    http_request: Request,
    research_case_id: str | None = None,
    limit: int = 2_000,
    tenant: Tenant = Depends(current_tenant),
) -> dict:
    return research_registry_service.dashboard(
        request_context(http_request, tenant),
        research_case_id=research_case_id,
        limit=limit,
    )


@app.get("/api/research-registry/claims/{claim_id}")
async def research_claim_detail(
    claim_id: str,
    http_request: Request,
    tenant: Tenant = Depends(current_tenant),
) -> dict:
    return research_registry_service.get_claim(
        request_context(http_request, tenant), claim_id
    )


@app.post("/api/research-registry/claims/{claim_id}/relations", status_code=201)
async def create_research_claim_relation(
    claim_id: str,
    payload: ClaimRelationRequest,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict:
    context = _schedule_context(http_request, user)
    relation = research_registry_service.create_relation(
        context,
        claim_id,
        ClaimRelationDraft(
            target_claim_id=payload.target_claim_id,
            relation_type=payload.relation_type,
            rationale=payload.rationale,
            evidence_refs=tuple(payload.evidence_refs),
        ),
    )
    get_repository().write_audit(
        user["tenant_id"],
        "research.claim_relation.create",
        f"/api/research-registry/claims/{claim_id}/relations",
        {
            "claim_id": claim_id,
            "target_claim_id": relation["target_claim_id"],
            "relation_type": relation["relation_type"],
        },
        user_id=user["id"],
    )
    return relation


@app.post(
    "/api/research-registry/claims/{claim_id}/relations/{relation_id}/withdraw"
)
async def withdraw_research_claim_relation(
    claim_id: str,
    relation_id: str,
    payload: RelationWithdrawalRequest,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict:
    context = _schedule_context(http_request, user)
    relation = research_registry_service.withdraw_relation(
        context, claim_id, relation_id, reason=payload.reason
    )
    get_repository().write_audit(
        user["tenant_id"],
        "research.claim_relation.withdraw",
        f"/api/research-registry/claims/{claim_id}/relations/{relation_id}/withdraw",
        {
            "claim_id": claim_id,
            "relation_id": relation_id,
            "relation_type": relation["relation_type"],
        },
        user_id=user["id"],
    )
    return relation


@app.post(
    "/api/research-registry/claims/{claim_id}/verification-attempts",
    status_code=201,
)
async def record_research_verification_attempt(
    claim_id: str,
    payload: VerificationAttemptRequest,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict:
    context = _schedule_context(http_request, user)
    attempt = research_registry_service.record_verification_attempt(
        context,
        claim_id,
        VerificationAttemptDraft(
            kind=payload.kind,
            outcome=payload.outcome,
            method=payload.method,
            scope=payload.scope,
            input_digest=payload.input_digest,
            output_digest=payload.output_digest,
            independent=payload.independent,
            run_id=payload.run_id,
            artifact_ids=tuple(payload.artifact_ids),
            metadata=payload.metadata,
        ),
    )
    get_repository().write_audit(
        user["tenant_id"],
        "research.verification_attempt.record",
        f"/api/research-registry/claims/{claim_id}/verification-attempts",
        {
            "claim_id": claim_id,
            "attempt_id": attempt["id"],
            "kind": attempt["kind"],
            "outcome": attempt["outcome"],
            "independent": attempt["independent"],
        },
        user_id=user["id"],
    )
    return attempt


@app.post(
    "/api/research-registry/claims/{claim_id}/verification-plans",
    status_code=201,
)
async def create_research_verification_plan(
    claim_id: str,
    payload: VerificationPlanRequest,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict:
    context = _schedule_context(http_request, user)
    plan = verification_runner.create_plan(
        context,
        claim_id,
        VerificationPlanDraft(
            plan_key=payload.plan_key,
            name=payload.name,
            kind=payload.kind,
            method=payload.method,
            scope=payload.scope,
            prompt=payload.prompt,
            system=payload.system,
            model=payload.model,
            auto_promote=payload.auto_promote,
            metadata=payload.metadata,
        ),
    )
    get_repository().write_audit(
        user["tenant_id"],
        "research.verification_plan.create",
        f"/api/research-registry/claims/{claim_id}/verification-plans",
        {
            "claim_id": claim_id,
            "plan_id": plan["id"],
            "plan_key": plan["plan_key"],
            "version": plan["version"],
        },
        user_id=user["id"],
    )
    return plan


@app.post("/api/research-registry/verification-plans/{plan_id}/runs")
async def run_research_verification_plan(
    plan_id: str,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict:
    result = await verification_runner.execute(
        _schedule_context(http_request, user), plan_id
    )
    execution = result["execution"]
    get_repository().write_audit(
        user["tenant_id"],
        "research.verification_plan.execute",
        f"/api/research-registry/verification-plans/{plan_id}/runs",
        {
            "plan_id": plan_id,
            "execution_id": execution["id"],
            "run_id": execution["run_id"],
            "attempt_id": execution["attempt_id"],
            "outcome": execution["outcome"],
        },
        user_id=user["id"],
    )
    return result


@app.post("/api/research-registry/claims/{claim_id}/promotion-gates")
async def evaluate_research_promotion_gate(
    claim_id: str,
    payload: PromotionGateRequest,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict:
    context = _schedule_context(http_request, user)
    result = research_registry_service.evaluate_promotion(
        context, claim_id, payload.target_stage
    )
    evaluation = result["evaluation"]
    get_repository().write_audit(
        user["tenant_id"],
        "research.promotion_gate.evaluate",
        f"/api/research-registry/claims/{claim_id}/promotion-gates",
        {
            "claim_id": claim_id,
            "evaluation_id": evaluation["id"],
            "from_stage": evaluation["from_stage"],
            "target_stage": evaluation["target_stage"],
            "decision": evaluation["decision"],
            "blockers": evaluation["blockers"],
        },
        user_id=user["id"],
    )
    return result


@app.get("/api/decision-lab")
async def decision_lab(
    http_request: Request,
    protocol_id: str | None = None,
    family: str | None = None,
    resolution: str | None = None,
    limit: int = 500,
    evaluation: bool = False,
    tenant: Tenant = Depends(current_tenant),
) -> dict:
    return decision_lab_service.dashboard(
        request_context(http_request, tenant),
        protocol_id=protocol_id,
        family=family,
        resolution=resolution,
        limit=limit,
        include_gold=evaluation,
    )


@app.get("/api/decision-lab/cases/{case_id}")
async def decision_case_detail(
    case_id: str,
    http_request: Request,
    evaluation: bool = False,
    tenant: Tenant = Depends(current_tenant),
) -> dict:
    return decision_lab_service.get_case(
        request_context(http_request, tenant), case_id, include_gold=evaluation
    )


async def _read_evidence_upload(
    upload: UploadFile, field: str, maximum: int = 5_000_000
) -> bytes:
    content = await upload.read(maximum + 1)
    if not content:
        raise InvalidEvidenceError(field, f"{field} is empty")
    if len(content) > maximum:
        raise InvalidEvidenceError(field, f"{field} exceeds {maximum} bytes")
    return content


@app.post("/api/research-registry/import/frontier", status_code=201)
async def import_frontier_registry(
    http_request: Request,
    source_name: str = Form(default="AI Frontier Claim Registry", max_length=200),
    registry_file: UploadFile = File(...),
    source_ledger_file: UploadFile | None = File(default=None),
    user: dict = Depends(current_admin_user),
) -> dict:
    context = _schedule_context(http_request, user)
    result = research_registry_service.import_frontier(
        context,
        source_name=source_name,
        registry_bytes=await _read_evidence_upload(
            registry_file, "registry_file", 1_000_000
        ),
        source_ledger_bytes=(
            await _read_evidence_upload(
                source_ledger_file, "source_ledger_file", 750_000
            )
            if source_ledger_file is not None
            else None
        ),
    )
    research_case = result.get("case") or {}
    get_repository().write_audit(
        user["tenant_id"],
        "research.frontier.import",
        "/api/research-registry/import/frontier",
        {
            "research_case_id": research_case.get("id"),
            "registry_id": research_case.get("registry_id"),
            "registry_version": research_case.get("registry_version"),
            "imported": result.get("imported", False),
            "claim_count": result.get("statistics", {}).get("claims", 0),
        },
        user_id=user["id"],
    )
    return result


@app.post("/api/decision-lab/import/nanojev", status_code=201)
async def import_nanojev_bundle(
    http_request: Request,
    source_name: str = Form(default="NanoJev evaluation", max_length=200),
    confidence_threshold: float = Form(default=0.7, ge=0, le=1),
    request_file: UploadFile = File(...),
    predictions_file: UploadFile = File(...),
    metrics_file: UploadFile = File(...),
    receipt_file: UploadFile | None = File(default=None),
    user: dict = Depends(current_admin_user),
) -> dict:
    context = _schedule_context(http_request, user)
    result = decision_lab_service.import_nanojev(
        context,
        source_name=source_name,
        confidence_threshold=confidence_threshold,
        request_bytes=await _read_evidence_upload(request_file, "request_file"),
        predictions_bytes=await _read_evidence_upload(
            predictions_file, "predictions_file"
        ),
        metrics_bytes=await _read_evidence_upload(metrics_file, "metrics_file"),
        receipt_bytes=(
            await _read_evidence_upload(receipt_file, "receipt_file", 1_000_000)
            if receipt_file is not None
            else None
        ),
    )
    protocol = result.get("protocol") or {}
    get_repository().write_audit(
        user["tenant_id"],
        "decision.nanojev.import",
        "/api/decision-lab/import/nanojev",
        {
            "protocol_id": protocol.get("id"),
            "imported": result.get("imported", False),
            "case_count": result.get("statistics", {}).get("cases", 0),
        },
        user_id=user["id"],
    )
    return result


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


@app.get("/api/credentials")
async def credentials(
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> list[dict]:
    tenant = Tenant(user["tenant_id"], user["tenant_id"])
    return credential_service.list(request_context(http_request, tenant))


@app.post("/api/credentials")
async def create_credential(
    request: CredentialCreateRequest,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict:
    tenant = Tenant(user["tenant_id"], user["tenant_id"])
    return credential_service.create(
        request_context(http_request, tenant),
        request.name,
        request.secret.get_secret_value(),
    )


@app.post("/api/credentials/{credential_id}/replace")
async def replace_credential(
    credential_id: str,
    request: CredentialReplaceRequest,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict:
    tenant = Tenant(user["tenant_id"], user["tenant_id"])
    return credential_service.replace(
        request_context(http_request, tenant),
        credential_id,
        request.secret.get_secret_value(),
    )


@app.delete("/api/credentials/{credential_id}")
async def revoke_credential(
    credential_id: str,
    http_request: Request,
    user: dict = Depends(current_admin_user),
) -> dict:
    tenant = Tenant(user["tenant_id"], user["tenant_id"])
    return credential_service.revoke(
        request_context(http_request, tenant), credential_id
    )


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
