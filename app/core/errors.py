"""Stable application error taxonomy.

Messages remain human-readable, while ``code`` is the stable value transports
and clients should branch on. HTTP status codes do not live here because they
belong to the HTTP adapter.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    RESOURCE_NOT_FOUND = "resource_not_found"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    UPSTREAM_REQUEST_FAILED = "upstream_request_failed"
    UPSTREAM_INVALID_RESPONSE = "upstream_invalid_response"
    TOOL_NOT_AVAILABLE = "tool_not_available"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    TOOL_PROVIDER_UNAVAILABLE = "tool_provider_unavailable"
    TOOL_PROVIDER_CONFLICT = "tool_provider_conflict"
    TOOL_TIMEOUT = "tool_timeout"
    CREDENTIAL_NOT_CONFIGURED = "credential_not_configured"
    CREDENTIAL_KEY_NOT_CONFIGURED = "credential_key_not_configured"
    CREDENTIAL_DECRYPTION_FAILED = "credential_decryption_failed"
    INTERNAL_ERROR = "internal_error"


class MCPProbeErrorCode(StrEnum):
    """Stable, non-sensitive failure categories for MCP configuration probes."""

    AUTH_FAILED = "auth_failed"
    TIMEOUT = "timeout"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    UNREACHABLE = "unreachable"
    DISCOVERY_FAILED = "discovery_failed"


class ApplicationError(RuntimeError):
    """Base class for failures safe to project through public transports."""

    code = ErrorCode.INTERNAL_ERROR
    retryable = False

    def __init__(self, message: str, *, metadata: dict[str, Any] | None = None):
        super().__init__(message)
        self.metadata = metadata or {}


class ResourceNotFoundError(ApplicationError):
    code = ErrorCode.RESOURCE_NOT_FOUND

    def __init__(self, resource: str, resource_id: str):
        super().__init__(
            f"{resource.replace('_', ' ').title()} not found",
            metadata={"resource": resource, "resource_id": resource_id},
        )


class LLMError(ApplicationError):
    """Compatibility base class for model-provider failures."""


class ProviderNotConfiguredError(LLMError):
    code = ErrorCode.PROVIDER_NOT_CONFIGURED


class UpstreamRequestError(LLMError):
    code = ErrorCode.UPSTREAM_REQUEST_FAILED
    retryable = True


class UpstreamResponseError(LLMError):
    code = ErrorCode.UPSTREAM_INVALID_RESPONSE


class CredentialNotConfiguredError(ApplicationError):
    code = ErrorCode.CREDENTIAL_NOT_CONFIGURED


class CredentialKeyNotConfiguredError(ApplicationError):
    """The independent at-rest encryption key is absent or malformed."""

    code = ErrorCode.CREDENTIAL_KEY_NOT_CONFIGURED

    def __init__(self, message: str = "Credential master key is not configured"):
        super().__init__(message)


class CredentialDecryptionError(ApplicationError):
    """Stored ciphertext could not be authenticated with the configured key."""

    code = ErrorCode.CREDENTIAL_DECRYPTION_FAILED

    def __init__(self, message: str = "Stored credential cannot be decrypted"):
        super().__init__(message)
