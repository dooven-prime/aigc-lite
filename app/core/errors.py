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
    RESOURCE_CONFLICT = "resource_conflict"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    UPSTREAM_REQUEST_FAILED = "upstream_request_failed"
    UPSTREAM_INVALID_RESPONSE = "upstream_invalid_response"
    TOOL_NOT_AVAILABLE = "tool_not_available"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    TOOL_PROVIDER_UNAVAILABLE = "tool_provider_unavailable"
    TOOL_PROVIDER_CONFLICT = "tool_provider_conflict"
    TOOL_AUTHORIZATION_REQUIRED = "tool_authorization_required"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_RESULT_TOO_LARGE = "tool_result_too_large"
    CHAT_CAPABILITY_NOT_ALLOWED = "chat_capability_not_allowed"
    CREDENTIAL_NOT_CONFIGURED = "credential_not_configured"
    CREDENTIAL_KEY_NOT_CONFIGURED = "credential_key_not_configured"
    CREDENTIAL_DECRYPTION_FAILED = "credential_decryption_failed"
    AGENT_MODEL_TURN_LIMIT_REACHED = "agent_model_turn_limit_reached"
    AGENT_TOOL_CALL_LIMIT_REACHED = "agent_tool_call_limit_reached"
    AGENT_WALL_TIME_LIMIT_REACHED = "agent_wall_time_limit_reached"
    AGENT_CANCELLED = "agent_cancelled"
    RUN_NOT_ACTIVE = "run_not_active"
    INVALID_SCHEDULE = "invalid_schedule"
    SCHEDULE_NOT_ACTIVE = "schedule_not_active"
    INVALID_ARTIFACT = "invalid_artifact"
    INVALID_CONVERSATION_IMPORT = "invalid_conversation_import"
    INVALID_EXECUTION_POLICY = "invalid_execution_policy"
    ENFORCER_NOT_CONFIGURED = "enforcer_not_configured"
    ENFORCER_REQUEST_FAILED = "enforcer_request_failed"
    INVALID_ENFORCEMENT_RECEIPT = "invalid_enforcement_receipt"
    ENFORCEMENT_DISPATCH_INDETERMINATE = "enforcement_dispatch_indeterminate"
    EXTERNAL_ENFORCEMENT_DENIED = "external_enforcement_denied"
    EXTERNAL_ENFORCEMENT_REQUIRED = "external_enforcement_required"
    INVALID_EVIDENCE = "invalid_evidence"
    INVALID_ASSURANCE_BUNDLE = "invalid_assurance_bundle"
    INVALID_VERIFICATION_RESULT = "invalid_verification_result"
    INVALID_KERNEL_VERIFICATION = "invalid_kernel_verification"
    KERNEL_VERIFIER_NOT_CONFIGURED = "kernel_verifier_not_configured"
    KERNEL_VERIFICATION_FAILED = "kernel_verification_failed"
    KERNEL_VERIFIER_TIMEOUT = "kernel_verifier_timeout"
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


class ResourceConflictError(ApplicationError):
    code = ErrorCode.RESOURCE_CONFLICT

    def __init__(self, resource: str, field: str):
        super().__init__(
            f"{resource.replace('_', ' ').title()} already exists",
            metadata={"resource": resource, "field": field},
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


class AgentLimitError(ApplicationError):
    """Base class for configured Agent execution budgets being exhausted."""


class AgentModelTurnLimitError(AgentLimitError):
    code = ErrorCode.AGENT_MODEL_TURN_LIMIT_REACHED

    def __init__(self, limit: int):
        super().__init__(
            "Agent model turn limit reached",
            metadata={"budget": "model_turns", "limit": limit},
        )


class AgentToolCallLimitError(AgentLimitError):
    code = ErrorCode.AGENT_TOOL_CALL_LIMIT_REACHED

    def __init__(self, limit: int):
        super().__init__(
            "Agent tool call limit reached",
            metadata={"budget": "tool_calls", "limit": limit},
        )


class AgentWallTimeLimitError(AgentLimitError):
    code = ErrorCode.AGENT_WALL_TIME_LIMIT_REACHED

    def __init__(self, limit_seconds: float):
        super().__init__(
            "Agent wall time limit reached",
            metadata={"budget": "wall_time_seconds", "limit": limit_seconds},
        )


class RunNotActiveError(ApplicationError):
    code = ErrorCode.RUN_NOT_ACTIVE

    def __init__(self, run_id: str):
        super().__init__(
            "Agent run is not active in this process",
            metadata={"resource": "agent_run", "resource_id": run_id},
        )


class ChatCapabilityDeniedError(ApplicationError):
    """The caller cannot delegate the requested Tool capability set to a model."""

    code = ErrorCode.CHAT_CAPABILITY_NOT_ALLOWED

    def __init__(self, capability_set_id: str, required_scopes: frozenset[str]):
        super().__init__(
            "Chat capability set is not allowed for this caller",
            metadata={
                "capability_set_id": capability_set_id,
                "required_scopes": sorted(required_scopes),
            },
        )


class InvalidScheduleError(ApplicationError):
    code = ErrorCode.INVALID_SCHEDULE

    def __init__(self, field: str, message: str):
        super().__init__(message, metadata={"field": field})


class ScheduleNotActiveError(ApplicationError):
    code = ErrorCode.SCHEDULE_NOT_ACTIVE

    def __init__(self, task_id: str, status: str):
        super().__init__(
            "Scheduled task cannot perform this transition",
            metadata={
                "resource": "scheduled_task",
                "resource_id": task_id,
                "status": status,
            },
        )


class InvalidArtifactError(ApplicationError):
    code = ErrorCode.INVALID_ARTIFACT

    def __init__(self, field: str, message: str):
        super().__init__(message, metadata={"field": field})


class InvalidConversationImportError(ApplicationError):
    """An external conversation export violates the import contract."""

    code = ErrorCode.INVALID_CONVERSATION_IMPORT

    def __init__(self, field: str, message: str):
        super().__init__(message, metadata={"field": field})


class InvalidExecutionPolicyError(ApplicationError):
    """A policy proposal or enforcement receipt violates its frozen contract."""

    code = ErrorCode.INVALID_EXECUTION_POLICY

    def __init__(self, field: str, message: str):
        super().__init__(message, metadata={"field": field})


class EnforcerNotConfiguredError(ApplicationError):
    """No host-owned adapter or verification key matches the requested issuer."""

    code = ErrorCode.ENFORCER_NOT_CONFIGURED

    def __init__(self, resource_id: str):
        super().__init__(
            "External enforcer is not configured",
            metadata={"resource": "enforcer", "resource_id": resource_id},
        )


class EnforcerRequestError(ApplicationError):
    """The out-of-process enforcer did not return a verifiable result in time."""

    code = ErrorCode.ENFORCER_REQUEST_FAILED
    retryable = True


class InvalidEnforcementReceiptError(ApplicationError):
    """An external receipt failed signature, binding, freshness, or schema checks."""

    code = ErrorCode.INVALID_ENFORCEMENT_RECEIPT

    def __init__(self, field: str, message: str):
        super().__init__(message, metadata={"field": field})


class EnforcementDispatchIndeterminateError(ApplicationError):
    """An external workload may have started but no terminal state is proven."""

    code = ErrorCode.ENFORCEMENT_DISPATCH_INDETERMINATE
    retryable = False

    def __init__(self, dispatch_id: str, cause: str):
        super().__init__(
            "External enforcement dispatch requires reconciliation",
            metadata={"dispatch_id": dispatch_id, "cause": cause},
        )


class InvalidEvidenceError(ApplicationError):
    """An evidence bundle violates a stable schema or integrity boundary."""

    code = ErrorCode.INVALID_EVIDENCE

    def __init__(self, field: str, message: str):
        super().__init__(message, metadata={"field": field})


class InvalidAssuranceBundleError(ApplicationError):
    """A portable bundle violates its schema, limits, or hash closure."""

    code = ErrorCode.INVALID_ASSURANCE_BUNDLE

    def __init__(self, message: str, *, field: str = "bundle"):
        super().__init__(message, metadata={"field": field})


class InvalidVerificationResultError(ApplicationError):
    """An Agent response failed the frozen verification result contract."""

    code = ErrorCode.INVALID_VERIFICATION_RESULT

    def __init__(self, message: str):
        super().__init__(message, metadata={"contract": "research.verification-result.v1"})


class InvalidKernelVerificationError(ApplicationError):
    """A proof request violates the bounded kernel execution contract."""

    code = ErrorCode.INVALID_KERNEL_VERIFICATION

    def __init__(self, field: str, message: str):
        super().__init__(message, metadata={"field": field})


class KernelVerifierNotConfiguredError(ApplicationError):
    """The requested server-owned proof checker is unavailable."""

    code = ErrorCode.KERNEL_VERIFIER_NOT_CONFIGURED

    def __init__(self, backend: str):
        super().__init__(
            "Kernel verifier backend is not configured",
            metadata={"backend": backend},
        )
