"""Workspace model, credential, and remote MCP configuration routes."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from ..auth import current_admin_user
from ..core.contracts import RequestContext
from ..core.credentials import (
    encrypted_credential_id,
    validate_workspace_credential_map,
)
from ..database import get_repository
from ..providers import validate_workspace_model_base_url
from ..services.credentials import CredentialService
from ..services.mcp_probe import MCPProbeService
from ..tenancy import Tenant, current_tenant

RequestContextFactory = Callable[[Request, Tenant], RequestContext]


class ModelConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    credential_reference: str = Field(min_length=1, max_length=200)
    input_price: float = Field(default=0, ge=0)
    output_price: float = Field(default=0, ge=0)
    is_default: bool = False

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return validate_workspace_model_base_url(value)

    @field_validator("credential_reference")
    @classmethod
    def validate_credential_reference(cls, value: str) -> str:
        encrypted_credential_id(value)
        return value


class MCPServerConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )
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
        return validate_workspace_credential_map(value)

    @field_validator("required_scopes")
    @classmethod
    def validate_required_scopes(cls, value: list[str]) -> list[str]:
        if not all(item.strip() and len(item) <= 100 for item in value):
            raise ValueError("required scopes must be non-empty strings")
        return sorted(set(value))


class CredentialCreateRequest(BaseModel):
    name: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )
    secret: SecretStr = Field(min_length=1, max_length=10_000)


class CredentialReplaceRequest(BaseModel):
    secret: SecretStr = Field(min_length=1, max_length=10_000)


def create_configuration_router(
    *,
    credential_service: CredentialService,
    mcp_probe_service: MCPProbeService,
    request_context_factory: RequestContextFactory,
) -> APIRouter:
    """Build the workspace configuration router with explicit services."""

    router = APIRouter()

    @router.get("/api/models")
    async def models(tenant: Tenant = Depends(current_tenant)) -> list[dict]:
        return get_repository().list_model_configs(tenant.id)

    @router.post("/api/models")
    async def save_model(
        request: ModelConfigRequest,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        repository = get_repository()
        credential = repository.get_credential(
            user["tenant_id"],
            encrypted_credential_id(request.credential_reference),
        )
        if credential is None or credential.get("revoked_at"):
            raise HTTPException(
                status_code=422,
                detail="Credential reference is not active in this workspace",
            )
        return repository.save_model_config(
            user["tenant_id"], request.model_dump()
        )

    @router.get("/api/mcp-servers")
    async def mcp_servers(
        user: dict = Depends(current_admin_user),
    ) -> list[dict]:
        return get_repository().list_mcp_servers(user["tenant_id"])

    @router.get("/api/credentials")
    async def credentials(
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> list[dict]:
        tenant = Tenant(user["tenant_id"], user["tenant_id"])
        return credential_service.list(
            request_context_factory(http_request, tenant)
        )

    @router.post("/api/credentials")
    async def create_credential(
        request: CredentialCreateRequest,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        tenant = Tenant(user["tenant_id"], user["tenant_id"])
        return credential_service.create(
            request_context_factory(http_request, tenant),
            request.name,
            request.secret.get_secret_value(),
        )

    @router.post("/api/credentials/{credential_id}/replace")
    async def replace_credential(
        credential_id: str,
        request: CredentialReplaceRequest,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        tenant = Tenant(user["tenant_id"], user["tenant_id"])
        return credential_service.replace(
            request_context_factory(http_request, tenant),
            credential_id,
            request.secret.get_secret_value(),
        )

    @router.delete("/api/credentials/{credential_id}")
    async def revoke_credential(
        credential_id: str,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        tenant = Tenant(user["tenant_id"], user["tenant_id"])
        return credential_service.revoke(
            request_context_factory(http_request, tenant), credential_id
        )

    @router.post("/api/mcp-servers")
    async def save_mcp_server(
        request: MCPServerConfigRequest,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        if request.provider_id == "local":
            raise HTTPException(status_code=409, detail="Provider id is reserved")
        repository = get_repository()
        for reference in request.header_credentials.values():
            credential = repository.get_credential(
                user["tenant_id"], encrypted_credential_id(reference)
            )
            if credential is None or credential.get("revoked_at"):
                raise HTTPException(
                    status_code=422,
                    detail="Credential reference is not active in this workspace",
                )
        return repository.save_mcp_server(
            user["tenant_id"], request.model_dump()
        )

    @router.post("/api/mcp-servers/{server_id}/probe")
    async def probe_mcp_server(
        server_id: str,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        tenant = Tenant(user["tenant_id"], user["tenant_id"])
        return await mcp_probe_service.probe(
            request_context_factory(http_request, tenant), server_id
        )

    @router.delete("/api/mcp-servers/{server_id}")
    async def delete_mcp_server(
        server_id: str,
        user: dict = Depends(current_admin_user),
    ) -> dict[str, bool]:
        deleted = get_repository().delete_mcp_server(
            user["tenant_id"], server_id
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="MCP server not found")
        return {"deleted": True}

    return router
