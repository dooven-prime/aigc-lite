"""Execute immutable research verification plans through the bounded Agent runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from ..core.artifacts import ArtifactDraft, ArtifactKind
from ..core.contracts import ChatCommand, RequestContext, RunStatus, StepKind, StepStatus
from ..core.errors import (
    ApplicationError,
    ErrorCode,
    InvalidEvidenceError,
    InvalidScheduleError,
    InvalidVerificationResultError,
    ResourceNotFoundError,
)
from ..core.research import (
    VerificationAttemptDraft,
    VerificationExecutionStatus,
    VerificationExecutor,
    VerificationKind,
    VerificationOutcome,
    VerificationPlanDraft,
    VerificationPlanStatus,
)
from ..core.scheduling import ScheduledTask
from ..database import get_repository
from ..redaction import redact, redact_record_text, redact_text
from ..repository import Repository
from .artifacts import ArtifactService
from .gateway import GatewayService
from .research_registry import ResearchRegistryService

RepositoryProvider = Callable[[], Repository]

TARGET_NAME = "research.verify"
RESULT_CONTRACT_VERSION = "research.verification-result.v1"
_PLAN_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_RESULT_FIELDS = frozenset(
    {
        "contract_version",
        "outcome",
        "summary",
        "findings",
        "evidence_refs",
        "limitations",
    }
)


class VerificationRunner:
    """Turn an Agent result into one auditable research verification closure."""

    def __init__(
        self,
        *,
        gateway_service: GatewayService,
        research_registry_service: ResearchRegistryService,
        artifact_service: ArtifactService,
        repository_provider: RepositoryProvider = get_repository,
    ) -> None:
        self._gateway = gateway_service
        self._research = research_registry_service
        self._artifacts = artifact_service
        self._repository_provider = repository_provider

    def create_plan(
        self,
        context: RequestContext,
        claim_id: str,
        draft: VerificationPlanDraft,
    ) -> dict:
        repository = self._repository_provider()
        claim = repository.get_research_claim(context.workspace_id, claim_id)
        if claim is None:
            raise ResourceNotFoundError("research_claim", claim_id)
        plan_key = draft.plan_key.strip().casefold()
        if not _PLAN_KEY.fullmatch(plan_key):
            raise InvalidEvidenceError(
                "plan_key",
                "plan_key must start with a letter and contain only lowercase letters, "
                "digits, dots, underscores, or hyphens",
            )
        name = self._text("name", draft.name, 200)
        method = self._text("method", draft.method, 4_000)
        scope = self._text("scope", draft.scope, 8_000)
        prompt = self._text("prompt", draft.prompt, 40_000)
        system = self._text("system", draft.system, 20_000)
        model = self._optional_text("model", draft.model, 200)
        metadata = redact(draft.metadata)
        if not isinstance(metadata, dict):
            raise InvalidEvidenceError("metadata", "metadata must be an object")
        snapshot = {
            "plan_key": plan_key,
            "executor": VerificationExecutor.AGENT.value,
            "name": name,
            "kind": draft.kind.value,
            "method": method,
            "scope": scope,
            "prompt": prompt,
            "system_prompt": system,
            "model": model,
            "result_contract_version": RESULT_CONTRACT_VERSION,
            "auto_promote": draft.auto_promote,
            "metadata": metadata,
        }
        return repository.create_research_verification_plan(
            context.workspace_id,
            {
                "research_case_id": claim["research_case_id"],
                "claim_revision_id": claim_id,
                "status": VerificationPlanStatus.ACTIVE.value,
                **snapshot,
                "content_digest": self._json_hash(snapshot),
                "created_by": context.principal_id,
            },
        )

    def get_plan(self, context: RequestContext, plan_id: str) -> dict:
        plan = self._repository_provider().get_research_verification_plan(
            context.workspace_id, plan_id
        )
        if plan is None:
            raise ResourceNotFoundError("research_verification_plan", plan_id)
        return plan

    def validate_task_payload(self, payload: dict[str, Any]) -> None:
        unsupported = sorted(set(payload) - {"plan_id"})
        if unsupported:
            raise InvalidScheduleError(
                "payload", "research.verify payload contains unsupported fields"
            )
        plan_id = payload.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id.strip() or len(plan_id) > 100:
            raise InvalidScheduleError(
                "payload.plan_id", "research.verify requires a valid plan_id"
            )

    async def run_scheduled(self, task: ScheduledTask) -> dict:
        self.validate_task_payload(task.payload)
        return await self.execute(
            RequestContext(
                request_id=f"schedule:{task.id}:{uuid4()}",
                workspace_id=task.workspace_id,
                principal_id="system:scheduler",
            ),
            task.payload["plan_id"],
            scheduled_task_id=task.id,
        )

    async def execute(
        self,
        context: RequestContext,
        plan_id: str,
        *,
        scheduled_task_id: str | None = None,
    ) -> dict:
        repository = self._repository_provider()
        plan = self.get_plan(context, plan_id)
        if plan["status"] != VerificationPlanStatus.ACTIVE.value:
            raise InvalidEvidenceError(
                "plan_id", "Only an active verification plan can be executed"
            )
        claim = repository.get_research_claim(
            context.workspace_id, plan["claim_revision_id"]
        )
        if claim is None:
            raise ResourceNotFoundError(
                "research_claim", plan["claim_revision_id"]
            )
        input_snapshot = self._input_snapshot(plan, claim)
        input_digest = self._json_hash(input_snapshot)
        execution_id = str(uuid4())
        request_id = f"verification:{execution_id}"
        verification_context = RequestContext(
            request_id=request_id,
            workspace_id=context.workspace_id,
            principal_id=context.principal_id,
            api_key_id=context.api_key_id,
            scopes=context.scopes,
            tool_hops=context.tool_hops,
        )
        repository.create_research_verification_execution(
            context.workspace_id,
            {
                "id": execution_id,
                "research_case_id": plan["research_case_id"],
                "claim_revision_id": plan["claim_revision_id"],
                "plan_id": plan["id"],
                "plan_version": plan["version"],
                "status": VerificationExecutionStatus.RUNNING.value,
                "request_id": request_id,
                "scheduled_task_id": scheduled_task_id,
                "input_digest": input_digest,
                "metadata": {
                    "executor": VerificationExecutor.AGENT.value,
                    "result_contract_version": RESULT_CONTRACT_VERSION,
                },
                "created_by": context.principal_id,
            },
        )

        try:
            chat_result = await self._gateway.chat(
                ChatCommand(
                    prompt=self._agent_prompt(plan, input_snapshot),
                    system=self._agent_system(plan),
                    requested_model=plan.get("model"),
                ),
                verification_context,
            )
        except asyncio.CancelledError:
            self._record_failure(
                verification_context,
                plan,
                execution_id,
                input_digest,
                request_id,
                status=VerificationExecutionStatus.CANCELLED,
                error_code=ErrorCode.AGENT_CANCELLED.value,
                summary="The verification execution was cancelled before completion.",
                step_status=StepStatus.CANCELLED,
            )
            raise
        except Exception as exc:
            error_code = self._error_code(exc)
            self._record_failure(
                verification_context,
                plan,
                execution_id,
                input_digest,
                request_id,
                status=VerificationExecutionStatus.FAILED,
                error_code=error_code,
                summary="The Agent failed before producing a valid verification result.",
                step_status=StepStatus.FAILED,
            )
            raise

        try:
            result = self._parse_result(chat_result.content)
        except InvalidVerificationResultError as exc:
            self._record_failure(
                verification_context,
                plan,
                execution_id,
                input_digest,
                request_id,
                status=VerificationExecutionStatus.INVALID_OUTPUT,
                error_code=exc.code.value,
                summary=str(exc),
                step_status=StepStatus.FAILED,
                raw_output=chat_result.content,
                run_id=chat_result.run_id,
            )
            repository.finish_run(
                context.workspace_id,
                chat_result.run_id,
                RunStatus.FAILED.value,
                exc.code.value,
            )
            raise

        try:
            return self._record_result(
                verification_context,
                plan,
                execution_id,
                input_digest,
                chat_result.run_id,
                result,
                execution_status=VerificationExecutionStatus.SUCCEEDED,
                step_status=StepStatus.SUCCEEDED,
            )
        except Exception as exc:
            repository.finish_research_verification_execution(
                context.workspace_id,
                execution_id,
                {
                    "status": VerificationExecutionStatus.FAILED.value,
                    "run_id": chat_result.run_id,
                    "error_code": self._error_code(exc),
                    "metadata": {
                        "phase": "verification_closure",
                        "result_contract_version": RESULT_CONTRACT_VERSION,
                    },
                },
            )
            repository.finish_run(
                context.workspace_id,
                chat_result.run_id,
                RunStatus.FAILED.value,
                self._error_code(exc),
            )
            raise

    def _record_failure(
        self,
        context: RequestContext,
        plan: dict,
        execution_id: str,
        input_digest: str,
        request_id: str,
        *,
        status: VerificationExecutionStatus,
        error_code: str,
        summary: str,
        step_status: StepStatus,
        raw_output: str | None = None,
        run_id: str | None = None,
    ) -> dict | None:
        repository = self._repository_provider()
        run = (
            repository.get_run(context.workspace_id, run_id)
            if run_id is not None
            else repository.get_run_by_request_id(context.workspace_id, request_id)
        )
        if run is None:
            return repository.finish_research_verification_execution(
                context.workspace_id,
                execution_id,
                {
                    "status": status.value,
                    "error_code": error_code,
                    "metadata": {
                        "phase": "agent_execution",
                        "result_contract_version": RESULT_CONTRACT_VERSION,
                    },
                },
            )
        result = {
            "contract_version": RESULT_CONTRACT_VERSION,
            "outcome": VerificationOutcome.ERROR.value,
            "summary": redact_record_text(summary, max_chars=8_000),
            "findings": [],
            "evidence_refs": [],
            "limitations": [f"execution_error:{error_code}"],
        }
        if raw_output is not None:
            result["raw_output_excerpt"] = redact_record_text(
                raw_output, max_chars=20_000
            )
        return self._record_result(
            context,
            plan,
            execution_id,
            input_digest,
            run["id"],
            result,
            execution_status=status,
            step_status=step_status,
            error_code=error_code,
        )

    def _record_result(
        self,
        context: RequestContext,
        plan: dict,
        execution_id: str,
        input_digest: str,
        run_id: str,
        result: dict,
        *,
        execution_status: VerificationExecutionStatus,
        step_status: StepStatus,
        error_code: str | None = None,
    ) -> dict:
        repository = self._repository_provider()
        run = repository.get_run(context.workspace_id, run_id)
        if run is None:
            raise ResourceNotFoundError("run", run_id)
        sequence = max((int(item["sequence"]) for item in run["steps"]), default=0) + 1
        serialized = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        step = repository.append_run_step(
            context.workspace_id,
            run_id,
            sequence,
            StepKind.AGENT.value,
            "verification_result_contract",
            step_status.value,
            json.dumps(
                {
                    "plan_id": plan["id"],
                    "plan_version": plan["version"],
                    "input_digest": input_digest,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            serialized,
            {
                "verification_execution_id": execution_id,
                "result_contract_version": RESULT_CONTRACT_VERSION,
                "error_code": error_code,
            },
        )
        artifact = self._artifacts.create_artifact(
            context,
            ArtifactDraft(
                name=f"{plan['plan_key']}-v{plan['version']}-result.json",
                kind=ArtifactKind.JSON,
                media_type="application/json",
                content_text=serialized,
                metadata={
                    "role": "verification_result",
                    "verification_plan_id": plan["id"],
                    "verification_plan_version": plan["version"],
                    "verification_execution_id": execution_id,
                    "result_contract_version": RESULT_CONTRACT_VERSION,
                },
            ),
            run_id=run_id,
            step_id=step["id"],
        )
        outcome = VerificationOutcome(result["outcome"])
        attempt = self._research.record_verification_attempt(
            context,
            plan["claim_revision_id"],
            VerificationAttemptDraft(
                kind=VerificationKind(plan["kind"]),
                outcome=outcome,
                method=plan["method"],
                scope=plan["scope"],
                input_digest=input_digest,
                output_digest=artifact["content_hash"],
                independent=False,
                run_id=run_id,
                artifact_ids=(artifact["id"],),
                metadata={
                    "verification_plan_id": plan["id"],
                    "verification_plan_version": plan["version"],
                    "verification_execution_id": execution_id,
                    "executor": VerificationExecutor.AGENT.value,
                    "agent_execution_is_not_independent_review": True,
                    "result_summary": result["summary"],
                    "findings": result["findings"],
                    "evidence_refs": result["evidence_refs"],
                    "limitations": result["limitations"],
                    "error_code": error_code,
                },
                plan_id=plan["id"],
                verification_execution_id=execution_id,
            ),
        )
        promotion = None
        if plan["auto_promote"]:
            next_stage = self._research.next_promotion_stage(
                context, plan["claim_revision_id"]
            )
            if next_stage is not None:
                promotion = self._research.evaluate_promotion(
                    context,
                    plan["claim_revision_id"],
                    next_stage,
                    required_attempt_id=attempt["id"],
                )
        execution = repository.finish_research_verification_execution(
            context.workspace_id,
            execution_id,
            {
                "status": execution_status.value,
                "run_id": run_id,
                "attempt_id": attempt["id"],
                "artifact_id": artifact["id"],
                "promotion_evaluation_id": (
                    promotion["evaluation"]["id"] if promotion is not None else None
                ),
                "outcome": outcome.value,
                "output_digest": artifact["content_hash"],
                "error_code": error_code,
                "metadata": {
                    "result_contract_version": RESULT_CONTRACT_VERSION,
                    "step_id": step["id"],
                    "receipt_id": attempt["receipt_id"],
                    "promotion_decision": (
                        promotion["evaluation"]["decision"]
                        if promotion is not None
                        else None
                    ),
                },
            },
        )
        if execution is None:
            raise InvalidEvidenceError(
                "verification_execution", "Verification execution is already terminal"
            )
        return {
            "execution": execution,
            "run": repository.get_run(context.workspace_id, run_id),
            "step": step,
            "artifact": artifact,
            "attempt": attempt,
            "promotion": promotion,
        }

    @staticmethod
    def _input_snapshot(plan: dict, claim: dict) -> dict:
        return {
            "plan": {
                key: plan.get(key)
                for key in (
                    "id",
                    "plan_key",
                    "version",
                    "content_digest",
                    "kind",
                    "method",
                    "scope",
                    "result_contract_version",
                )
            },
            "claim": {
                key: claim.get(key)
                for key in (
                    "id",
                    "claim_key",
                    "revision_number",
                    "statement",
                    "claim_type",
                    "scope",
                    "method_revision",
                    "lifecycle_status",
                    "closure_status",
                    "promotion_stage",
                    "status_axes",
                    "blockers",
                )
            },
            "sources": [
                {
                    key: source.get(key)
                    for key in (
                        "ref_key",
                        "source_key",
                        "locator",
                        "content_hash",
                        "status",
                    )
                }
                for source in claim.get("sources", [])
            ],
        }

    @staticmethod
    def _agent_system(plan: dict) -> str:
        return (
            f"{plan['system_prompt']}\n\n"
            "You are executing a bounded research verification plan. Distinguish "
            "observations from inference, use tools only when needed, and do not claim "
            "independent review. Your final response must be exactly one JSON object "
            f"conforming to {RESULT_CONTRACT_VERSION}; do not use Markdown fences."
        )

    @staticmethod
    def _agent_prompt(plan: dict, input_snapshot: dict) -> str:
        contract = {
            "contract_version": RESULT_CONTRACT_VERSION,
            "outcome": "passed | failed | inconclusive | error",
            "summary": "non-empty string",
            "findings": ["string"],
            "evidence_refs": ["artifact/source/tool reference"],
            "limitations": ["string"],
        }
        return (
            "Execute this immutable verification plan:\n\n"
            f"{plan['prompt']}\n\n"
            "Frozen execution input:\n"
            f"{json.dumps(input_snapshot, ensure_ascii=False, indent=2, sort_keys=True)}\n\n"
            "Required final result contract:\n"
            f"{json.dumps(contract, ensure_ascii=False, indent=2)}"
        )

    @staticmethod
    def _parse_result(content: str) -> dict:
        try:
            value = json.loads(content.strip())
        except (TypeError, json.JSONDecodeError) as exc:
            raise InvalidVerificationResultError(
                "Agent verification output must be one valid JSON object"
            ) from exc
        if not isinstance(value, dict):
            raise InvalidVerificationResultError(
                "Agent verification output must be a JSON object"
            )
        unsupported = sorted(set(value) - _RESULT_FIELDS)
        missing = sorted(_RESULT_FIELDS - set(value))
        if unsupported or missing:
            raise InvalidVerificationResultError(
                "Agent verification output fields do not match the frozen result contract"
            )
        if value["contract_version"] != RESULT_CONTRACT_VERSION:
            raise InvalidVerificationResultError(
                "Agent verification output uses an unsupported contract version"
            )
        try:
            outcome = VerificationOutcome(value["outcome"])
        except (TypeError, ValueError) as exc:
            raise InvalidVerificationResultError(
                "Agent verification output contains an unsupported outcome"
            ) from exc
        summary = VerificationRunner._result_text("summary", value["summary"], 8_000)
        return {
            "contract_version": RESULT_CONTRACT_VERSION,
            "outcome": outcome.value,
            "summary": summary,
            "findings": VerificationRunner._result_list(
                "findings", value["findings"], 100, 4_000
            ),
            "evidence_refs": VerificationRunner._result_list(
                "evidence_refs", value["evidence_refs"], 100, 1_000
            ),
            "limitations": VerificationRunner._result_list(
                "limitations", value["limitations"], 100, 4_000
            ),
        }

    @staticmethod
    def _result_text(field: str, value: Any, maximum: int) -> str:
        if not isinstance(value, str):
            raise InvalidVerificationResultError(
                f"Agent verification field {field} must be a string"
            )
        cleaned = redact_record_text(value.strip(), max_chars=maximum)
        if not cleaned:
            raise InvalidVerificationResultError(
                f"Agent verification field {field} is required"
            )
        return cleaned

    @staticmethod
    def _result_list(
        field: str, value: Any, maximum_items: int, maximum_chars: int
    ) -> list[str]:
        if (
            not isinstance(value, list)
            or len(value) > maximum_items
            or not all(isinstance(item, str) for item in value)
        ):
            raise InvalidVerificationResultError(
                f"Agent verification field {field} must be a bounded string array"
            )
        return [
            redact_record_text(item.strip(), max_chars=maximum_chars)
            for item in value
            if item.strip()
        ]

    @staticmethod
    def _error_code(exc: BaseException) -> str:
        if isinstance(exc, ApplicationError):
            return exc.code.value
        return ErrorCode.INTERNAL_ERROR.value

    @staticmethod
    def _json_hash(value: object) -> str:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _text(field: str, value: str, maximum: int) -> str:
        cleaned = redact_record_text(value.strip(), max_chars=maximum)
        if not cleaned:
            raise InvalidEvidenceError(field, f"{field} is required")
        return cleaned

    @staticmethod
    def _optional_text(field: str, value: str | None, maximum: int) -> str | None:
        if value is None:
            return None
        cleaned = redact_text(value.strip())
        if len(cleaned) > maximum:
            raise InvalidEvidenceError(field, f"{field} exceeds {maximum} characters")
        return cleaned or None
