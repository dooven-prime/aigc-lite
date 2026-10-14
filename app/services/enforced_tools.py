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
    tool_spec_value,
)
from ..core.errors import (
    EnforcementDispatchIndeterminateError,
    ErrorCode,
    InvalidExecutionPolicyError,
)
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

    def prepare_if_bound(
        self, context: RequestContext, run_id: str | None, spec: ToolSpec
    ) -> tuple[dict[str, Any], ToolProviderResult | None] | None:
        """Resolve the host binding and reject blocked calls before grant use."""

        binding = self._registry.resolve(context, spec)
        if binding is None:
            return None
        public = binding.as_dict()
        metadata = {"external_enforcement": {
            "binding_id": binding.binding_id,
            "binding_hash": public["binding_hash"],
            "adapter_id": binding.adapter_id,
            "proposal_id": binding.proposal_id,
            "policy_hash": binding.policy_hash,
        }}
        proposal = self._repository_provider().get_policy_proposal(
            context.workspace_id, binding.proposal_id
        )
        details = {
            **public,
            "adapter_target_type": proposal["target_type"] if proposal else "",
            "adapter_target_id": proposal["target_id"] if proposal else "",
        }
        if not run_id or proposal is None or proposal.get("candidate_policy_hash") != binding.policy_hash:
            return details, self._failure(ErrorCode.EXTERNAL_ENFORCEMENT_REQUIRED, metadata)
        unresolved = self._enforcer_service.unresolved_dispatches(
            context, adapter_id=binding.adapter_id
        )
        if unresolved:
            blocked = unresolved[0]
            metadata["external_enforcement"].update(
                {"dispatch_id": blocked["id"], "state": blocked["state"]}
            )
            return details, self._failure(
                ErrorCode.ENFORCEMENT_DISPATCH_INDETERMINATE, metadata
            )
        return details, None

    async def execute_if_bound(
        self,
        context: RequestContext,
        run_id: str | None,
        spec: ToolSpec,
        arguments: dict,
        authorization: dict | None = None,
    ) -> ToolProviderResult | None:
        prepared = self.prepare_if_bound(context, run_id, spec)
        if prepared is None:
            return None
        details, failure = prepared
        if failure is not None:
            return failure
        binding = self._registry.resolve(context, spec)
        assert binding is not None
        base_metadata = {
            "external_enforcement": {
                "binding_id": binding.binding_id,
                "binding_hash": binding.as_dict()["binding_hash"],
                "adapter_id": binding.adapter_id,
                "proposal_id": binding.proposal_id,
                "policy_hash": binding.policy_hash,
            }
        }
        spec_value = tool_spec_value(spec)
        arguments_digest = enforcement_payload_hash(arguments)
        authorization_value = authorization or {}
        if not authorization_value.get("authorization_invocation_id"):
            return self._failure(ErrorCode.TOOL_AUTHORIZATION_REQUIRED, base_metadata)
        grant_binding = {
            "action": authorization_value.get("action"),
            "target": authorization_value.get("target"),
            "grant_id": authorization_value.get("grant_id"),
            "grant_call_number": authorization_value.get("calls_used"),
            "authorization_invocation_id": authorization_value["authorization_invocation_id"],
            "invocation_digest": authorization_value.get("invocation_digest"),
        }
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
            "grant_binding": grant_binding,
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
                tool_timeout_seconds=spec.timeout_seconds,
                arguments_digest=arguments_digest,
                expires_in_seconds=min(spec.timeout_seconds, 300.0),
                binding_id=binding.binding_id,
                binding_hash=details["binding_hash"],
                authorization_invocation_id=authorization_value[
                    "authorization_invocation_id"
                ],
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
        except InvalidExecutionPolicyError:
            return self._failure(ErrorCode.TOOL_AUTHORIZATION_REQUIRED, base_metadata)

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
