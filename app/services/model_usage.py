"""Host-owned, per-attempt model usage observations.

This is accounting evidence, not a vendor bill or a cross-Run cost budget.
Neither the model response nor an injectable runner may set identity fields.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from ..core.contracts import RequestContext
from ..core.model_usage import (
    RECEIPT_VERSION,
    NormalizedUsage,
    canonical_hash,
    estimated_cost,
    normalize_usage,
    pricing_snapshot,
)
from ..repository import Repository

_UNSET = object()


class ModelUsageObserver:
    """One serial Agent loop's bridge from provider facts to the Run ledger."""

    def __init__(
        self,
        repository: Repository,
        context: RequestContext,
        run_id: str,
        model: str,
        model_config: dict[str, Any] | None,
        *,
        default_input_price: float,
        default_output_price: float,
        default_pricing_known: bool = False,
        reserve_sequence: Callable[[], int],
        step_metadata: dict[str, Any],
        on_step: Callable[[int, str, str], None] | None = None,
    ) -> None:
        self._repository = repository
        self._context = context
        self._run_id = run_id
        self._model = model
        self._provider_config_id = (model_config or {}).get("id")
        self._pricing = pricing_snapshot(
            model_config or {
                "input_price": default_input_price,
                "output_price": default_output_price,
            },
            provider_config_id=self._provider_config_id,
            default_pricing_known=default_pricing_known,
        )
        self._reserve_sequence = reserve_sequence
        self._step_metadata = step_metadata
        self._on_step = on_step
        self._active: dict[str, Any] | None = None
        self._observed_usage: Any = _UNSET
        self._usage_conflict = False
        self._provider_request_id: str | None = None
        self._finish_reason: str | None = None
        self._output_bytes = 0

    def begin(self, iteration: int, input_content: str, model: str) -> None:
        if self._active is not None:
            raise RuntimeError("A model call is already active in this Agent loop")
        sequence = self._reserve_sequence()
        values = {
            "attempt_id": str(uuid4()),
            "model_call_id": str(uuid4()),
            "step_id": str(uuid4()),
            "tenant_id": self._context.workspace_id,
            "principal_id": self._context.principal_id,
            "api_key_id": self._context.api_key_id,
            "request_id": self._context.request_id,
            "run_id": self._run_id,
            "sequence": sequence,
            "iteration": iteration,
            "model": model,
            "provider_config_id": self._provider_config_id,
            "pricing_snapshot": self._pricing,
            "input_content": input_content,
            "step_metadata": {
                **self._step_metadata,
                "iteration": iteration,
                "model_call_id": "",  # replaced with server-created identity below
                "usage_status": "IN_FLIGHT",
            },
        }
        values["step_metadata"]["model_call_id"] = values["model_call_id"]
        values["step_metadata"]["attempt_id"] = values["attempt_id"]
        self._repository.begin_model_usage(values)
        self._active = values
        self._observed_usage = _UNSET
        self._usage_conflict = False
        self._provider_request_id = None
        self._finish_reason = None
        self._output_bytes = 0

    def observe_output(self, content: str) -> None:
        if self._active is None:
            raise RuntimeError("No active model call can receive output")
        self._output_bytes += len(content.encode("utf-8"))

    def observe_provider_metadata(self, value: dict[str, Any]) -> None:
        if self._active is None:
            raise RuntimeError("No active model call can receive provider metadata")
        request_id = self._bounded_identifier(value.get("id"), limit=256)
        reason = self._bounded_identifier(value.get("finish_reason"))
        if request_id is not None:
            self._provider_request_id = request_id
        if reason is not None:
            self._finish_reason = reason

    def observe_usage(self, value: Any) -> None:
        if self._active is None:
            raise RuntimeError("No active model call can receive usage")
        if self._observed_usage is _UNSET:
            self._observed_usage = value
        elif self._observed_usage != value:
            self._usage_conflict = True

    def finish(
        self,
        *,
        message: dict[str, Any] | None = None,
        output_content: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        error: BaseException | None = None,
    ) -> dict[str, Any]:
        active = self._active
        if active is None:
            raise RuntimeError("No active model call can be finalized")
        if message is not None and message.get("_usage") is not None:
            self.observe_usage(message["_usage"])
        usage = normalize_usage(
            None if self._observed_usage is _UNSET else self._observed_usage
        )
        if self._usage_conflict:
            usage = NormalizedUsage("INVALID", None, None)
        termination = self._termination(error)
        step_status = "succeeded" if error is None else (
            "cancelled" if isinstance(error, asyncio.CancelledError) else "failed"
        )
        finish_reason = self._bounded_identifier((message or {}).get("_finish_reason")) or self._finish_reason
        provider_request_id = self._bounded_identifier(
            (message or {}).get("_provider_request_id"), limit=256
        ) or self._provider_request_id
        output_bytes = self._output_bytes or len(output_content.encode("utf-8"))
        safe_usage = {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
        }
        final = {
            "usage_status": usage.status,
            **safe_usage,
            "estimated_cost": estimated_cost(usage, self._pricing),
            "termination": termination,
            "finish_reason": finish_reason,
            "provider_request_id": provider_request_id,
            "output_bytes": output_bytes,
            "step_status": step_status,
            "output_content": output_content,
            "step_metadata": {
                **self._step_metadata,
                "iteration": active["iteration"],
                "model_call_id": active["model_call_id"],
                "attempt_id": active["attempt_id"],
                "usage_status": usage.status,
                "usage": safe_usage,
                "tool_calls": [self._tool_name(call) for call in (tool_calls or [])],
                "termination": termination,
                "output_bytes": output_bytes,
            },
        }
        final["finalization_hash"] = canonical_hash({
            "receipt_version": RECEIPT_VERSION,
            "attempt_id": active["attempt_id"],
            "usage_status": usage.status,
            **safe_usage,
            "estimated_cost": final["estimated_cost"],
            "termination": termination,
            "finish_reason": finish_reason,
            "provider_request_id": provider_request_id,
            "output_bytes": output_bytes,
            "step_status": step_status,
            "output_sha256": hashlib.sha256(output_content.encode("utf-8")).hexdigest(),
        })
        result = self._repository.finish_model_usage(
            self._context.workspace_id, active["attempt_id"], final
        )
        self._active = None
        if self._on_step is not None:
            self._on_step(active["sequence"], "model", step_status)
        return result

    @staticmethod
    def _bounded_identifier(value: Any, *, limit: int = 64) -> str | None:
        if not isinstance(value, str) or not 0 < len(value) <= limit:
            return None
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return None
        return value

    @staticmethod
    def _tool_name(call: Any) -> str:
        if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
            return ""
        name = call["function"].get("name")
        return ModelUsageObserver._bounded_identifier(name, limit=256) or ""

    @staticmethod
    def _termination(error: BaseException | None) -> str:
        if error is None:
            return "COMPLETED"
        if isinstance(error, asyncio.CancelledError):
            # asyncio.timeout also cancels the task, so the individual provider
            # call cannot honestly distinguish explicit cancellation from it.
            return "CANCELLED_OR_TIMEOUT"
        name = type(error).__name__
        message = str(error).lower()
        if "truncated" in message:
            return "TRUNCATED"
        if "ended before completion" in message or "stream" in message:
            return "STREAM_INTERRUPTED"
        if name == "ProviderNotConfiguredError":
            return "NOT_SENT"
        return "FAILED_OR_INDETERMINATE"
