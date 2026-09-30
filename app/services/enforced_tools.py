"""Project deployer-owned external enforcement bindings into the Tool Catalog."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from ..core.contracts import RequestContext, ToolProviderResult, ToolSpec
from ..core.enforcement import (
    EnforcementDecision,
    EnforcementDispatchState,
    EnforcementOutcome,
    ExternalToolExecutionBinding,
    enforcement_payload_hash,
)
from ..core.errors import EnforcementDispatchIndeterminateError, ErrorCode
from ..database import get_repository
from ..repository import Repository
from .enforcer import ExternalEnforcerService

RepositoryProvider = Callable[[], Repository]

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


class ExternalToolExecutionBindingRegistry:
    """Host-owned current-use bindings; no workspace transport can mutate them."""

    def __init__(
        self, bindings: list[ExternalToolExecutionBinding] | None = None
    ) -> None:
        self._bindings: dict[tuple[str, str, str], ExternalToolExecutionBinding] = {}
        self._ids: set[str] = set()
        for binding in bindings or []:
            self.register(binding)

    def register(self, binding: ExternalToolExecutionBinding) -> None:
        names = (
            binding.binding_id,
            binding.workspace_id,
            binding.provider_id,
            binding.native_name,
            binding.adapter_id,
            binding.proposal_id,
        )
        if any(not _NAME.fullmatch(value) for value in names):
            raise ValueError("External tool binding contains an invalid stable name")
        if not _HASH.fullmatch(binding.policy_hash):
            raise ValueError("External tool binding policy hash is invalid")
        key = (binding.workspace_id, binding.provider_id, binding.native_name)
        if binding.binding_id in self._ids or key in self._bindings:
            raise ValueError("External tool binding is already registered")
        self._ids.add(binding.binding_id)
        self._bindings[key] = binding

    def resolve(
        self, context: RequestContext, spec: ToolSpec
    ) -> ExternalToolExecutionBinding | None:
        binding = self._bindings.get(
            (context.workspace_id, spec.provider_id, spec.native_name)
        )
        return binding if binding is not None and binding.enabled else None

    def list_public(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        return [
            binding.as_dict()
            for binding in sorted(self._bindings.values(), key=lambda item: item.binding_id)
            if workspace_id is None or binding.workspace_id == workspace_id
        ]


class ExternalToolExecutionRouter:
    """Execute bound tools only through an external enforcer; never fall back locally."""

    def __init__(
        self,
        *,
        registry: ExternalToolExecutionBindingRegistry,
        enforcer_service: ExternalEnforcerService,
        repository_provider: RepositoryProvider = get_repository,
    ) -> None:
        self._registry = registry
        self._enforcer_service = enforcer_service
        self._repository_provider = repository_provider

    def list_bindings(self, workspace_id: str) -> list[dict[str, Any]]:
        return self._registry.list_public(workspace_id)

    async def execute_if_bound(
        self,
        context: RequestContext,
        run_id: str | None,
        spec: ToolSpec,
        arguments: dict,
        authorization: dict | None = None,
    ) -> ToolProviderResult | None:
        binding = self._registry.resolve(context, spec)
        if binding is None:
            return None
        base_metadata = {
            "external_enforcement": {
                "binding_id": binding.binding_id,
                "binding_hash": binding.as_dict()["binding_hash"],
                "adapter_id": binding.adapter_id,
                "proposal_id": binding.proposal_id,
                "policy_hash": binding.policy_hash,
            }
        }
        if not run_id:
            return self._failure(
                ErrorCode.EXTERNAL_ENFORCEMENT_REQUIRED,
                base_metadata,
            )
        proposal = self._repository_provider().get_policy_proposal(
            context.workspace_id, binding.proposal_id
        )
        if (
            proposal is None
            or proposal.get("candidate_policy_hash") != binding.policy_hash
        ):
            return self._failure(
                ErrorCode.EXTERNAL_ENFORCEMENT_REQUIRED,
                base_metadata,
            )
        unresolved = self._enforcer_service.unresolved_dispatches(
            context,
            adapter_id=binding.adapter_id,
        )
        if unresolved:
            metadata = {
                **base_metadata,
                "external_enforcement": {
                    **base_metadata["external_enforcement"],
                    "dispatch_id": unresolved[0]["id"],
                    "state": unresolved[0]["state"],
                },
            }
            return self._failure(
                ErrorCode.ENFORCEMENT_DISPATCH_INDETERMINATE,
                metadata,
            )

        spec_value = _tool_spec_value(spec)
        arguments_digest = enforcement_payload_hash(arguments)
        authorization_value = authorization or {}
        envelope = {
            "operation": "tool.execute.v1",
            "principal_id": context.principal_id,
            "request_id": context.request_id,
            "tool": {
                "public_name": spec.name,
                "native_name": spec.native_name,
                "provider_id": spec.provider_id,
                "source": spec.source.value,
            },
            "arguments": arguments,
            "grant_binding": authorization_value,
        }
        try:
            receipt = await self._enforcer_service.dispatch(
                context,
                adapter_id=binding.adapter_id,
                proposal_id=binding.proposal_id,
                run_id=run_id,
                step_id=None,
                execution_envelope=envelope,
                tool_spec_hash=enforcement_payload_hash(spec_value),
                arguments_digest=arguments_digest,
                expires_in_seconds=min(spec.timeout_seconds, 300.0),
                binding_id=binding.binding_id,
            )
        except EnforcementDispatchIndeterminateError as exc:
            metadata = {
                **base_metadata,
                "external_enforcement": {
                    **base_metadata["external_enforcement"],
                    "dispatch_id": exc.metadata["dispatch_id"],
                    "state": EnforcementDispatchState.INDETERMINATE.value,
                },
            }
            return self._failure(
                ErrorCode.ENFORCEMENT_DISPATCH_INDETERMINATE,
                metadata,
            )

        enforcement_metadata = {
            **base_metadata["external_enforcement"],
            "dispatch_id": receipt["dispatch_id"],
            "receipt_id": receipt["id"],
            "state": receipt["dispatch_state"],
            "decision": receipt["decision"],
            "outcome": receipt["outcome"],
            "workload_id": receipt.get("workload_id"),
            "signature_verified": receipt["signature_verified"],
        }
        metadata = {"external_enforcement": enforcement_metadata}
        decision = EnforcementDecision(receipt["decision"])
        outcome = EnforcementOutcome(receipt["outcome"])
        if receipt["dispatch_state"] == EnforcementDispatchState.INDETERMINATE.value:
            return self._failure(
                ErrorCode.ENFORCEMENT_DISPATCH_INDETERMINATE,
                metadata,
            )
        if decision is not EnforcementDecision.ALLOW:
            return self._failure(ErrorCode.EXTERNAL_ENFORCEMENT_DENIED, metadata)
        if outcome is not EnforcementOutcome.SUCCEEDED:
            return self._failure(ErrorCode.TOOL_EXECUTION_FAILED, metadata)
        return ToolProviderResult(
            content=json.dumps(
                {
                    "status": "succeeded",
                    "dispatch_id": receipt["dispatch_id"],
                    "receipt_id": receipt["id"],
                    "workload_id": receipt.get("workload_id"),
                }
            ),
            metadata=metadata,
        )

    @staticmethod
    def _failure(
        error_code: ErrorCode, metadata: dict[str, Any]
    ) -> ToolProviderResult:
        return ToolProviderResult(
            content=json.dumps({"error": error_code.value}),
            failed=True,
            metadata={**metadata, "error_code": error_code.value},
        )


def _tool_spec_value(spec: ToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "native_name": spec.native_name,
        "description": spec.description,
        "input_schema": spec.input_schema,
        "source": spec.source.value,
        "provider_id": spec.provider_id,
        "workspace_id": spec.workspace_id,
        "risk": spec.risk.value,
        "required_scopes": sorted(spec.required_scopes),
        "timeout_seconds": spec.timeout_seconds,
        "execution_mode": spec.execution_mode.value if spec.execution_mode else None,
        "hints": {
            "readOnlyHint": spec.hints.read_only,
            "destructiveHint": spec.hints.destructive,
            "idempotentHint": spec.hints.idempotent,
            "openWorldHint": spec.hints.open_world,
        },
        "extensions": spec.extensions,
    }
