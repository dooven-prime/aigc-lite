"""Application service for observable synchronous and streaming chat runs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from ..agent import build_messages, run_agent
from ..audit import usage_from_response
from ..config import settings
from ..core.contracts import (
    AgentStepRecord,
    ChatCommand,
    ChatResult,
    ChatStreamResult,
    RequestContext,
    RunStatus,
    StepKind,
    StepStatus,
)
from ..core.errors import ApplicationError, ErrorCode, ResourceNotFoundError
from ..database import get_repository
from ..providers import stream_chat
from ..repository import Repository
from .tool_catalog import ToolCatalog, create_default_tool_catalog

AgentRunner = Callable[..., Awaitable[str]]
RepositoryProvider = Callable[[], Repository]
UsageRecorder = Callable[[dict, str, str, dict | None], None]
MessageBuilder = Callable[..., list[dict]]
StreamRunner = Callable[..., AsyncIterator[str]]


class GatewayService:
    """Coordinate session ownership, model selection, usage, and agent execution.

    Dependencies are injectable so the use case can be tested without HTTP or
    a real model provider. The default adapters preserve the existing runtime
    behavior while the provider and repository ports are extracted later.
    """

    def __init__(
        self,
        *,
        repository_provider: RepositoryProvider = get_repository,
        agent_runner: AgentRunner = run_agent,
        usage_recorder: UsageRecorder = usage_from_response,
        message_builder: MessageBuilder = build_messages,
        stream_runner: StreamRunner = stream_chat,
        tool_catalog: ToolCatalog | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._agent_runner = agent_runner
        self._usage_recorder = usage_recorder
        self._message_builder = message_builder
        self._stream_runner = stream_runner
        self._tool_catalog = tool_catalog or create_default_tool_catalog(
            repository_provider
        )

    async def chat(self, command: ChatCommand, context: RequestContext) -> ChatResult:
        repository = self._repository_provider()
        session = self._owned_session(repository, context.workspace_id, command.session_id)
        history = session["messages"][-20:]
        repository.add_message(context.workspace_id, session["id"], "user", command.prompt)

        model_config = repository.get_model_config(
            context.workspace_id, command.requested_model
        )
        provider: dict[str, Any] = model_config or {}
        model_name = command.requested_model or provider.get("model")
        selected_model = model_name or settings.llm_model
        run = repository.create_run(
            context.workspace_id,
            session["id"],
            context.request_id,
            command.requested_model,
            selected_model,
        )
        sequence = 0

        def record_step(step: AgentStepRecord) -> None:
            nonlocal sequence
            sequence += 1
            repository.append_run_step(
                context.workspace_id,
                run["id"],
                sequence,
                step.kind.value,
                step.name,
                step.status.value,
                step.input_content,
                step.output_content,
                step.metadata,
            )

        def record_usage(usage: dict) -> None:
            pricing = provider or {
                "input_price": settings.default_input_price,
                "output_price": settings.default_output_price,
            }
            self._usage_recorder(
                usage,
                selected_model,
                context.workspace_id,
                pricing,
            )

        try:
            tool_session = await self._tool_catalog.open(context)
            content = await self._agent_runner(
                command.prompt,
                command.system,
                model_name,
                settings.max_agent_steps,
                history,
                context.workspace_id,
                provider=provider,
                usage_callback=record_usage,
                step_callback=record_step,
                available_tools=tool_session.model_schemas(),
                tool_invoker=tool_session.invoke,
            )
        except ApplicationError as exc:
            self._record_failed_run(
                repository,
                context,
                run["id"],
                command,
                selected_model,
                exc.code.value,
                sequence + 1,
            )
            raise
        except Exception:
            self._record_failed_run(
                repository,
                context,
                run["id"],
                command,
                selected_model,
                ErrorCode.INTERNAL_ERROR.value,
                sequence + 1,
            )
            raise

        repository.add_message(
            context.workspace_id, session["id"], "assistant", content
        )
        if sequence == 0:
            record_step(
                AgentStepRecord(
                    kind=StepKind.AGENT,
                    name="chat_agent",
                    status=StepStatus.SUCCEEDED,
                    input_content=command.prompt,
                    output_content=content,
                    metadata={"model": selected_model},
                )
            )
        repository.finish_run(
            context.workspace_id, run["id"], RunStatus.SUCCEEDED.value
        )
        return ChatResult(
            content=content,
            session_id=session["id"],
            run_id=run["id"],
        )

    async def stream_chat(
        self, command: ChatCommand, context: RequestContext
    ) -> ChatStreamResult:
        """Start a stream whose final content is persisted as one model step."""
        repository = self._repository_provider()
        session = self._owned_session(repository, context.workspace_id, command.session_id)
        history = session["messages"][-20:]
        repository.add_message(context.workspace_id, session["id"], "user", command.prompt)
        model_config = repository.get_model_config(
            context.workspace_id, command.requested_model
        ) or {}
        model_name = command.requested_model or model_config.get("model")
        selected_model = model_name or settings.llm_model
        run = repository.create_run(
            context.workspace_id,
            session["id"],
            context.request_id,
            command.requested_model,
            selected_model,
        )
        messages = self._message_builder(
            command.prompt, command.system, history, context.workspace_id
        )

        def record_usage(usage: dict) -> None:
            pricing = model_config or {
                "input_price": settings.default_input_price,
                "output_price": settings.default_output_price,
            }
            self._usage_recorder(
                usage, selected_model, context.workspace_id, pricing
            )

        async def chunks() -> AsyncIterator[str]:
            collected: list[str] = []
            try:
                async for chunk in self._stream_runner(
                    messages, model_name, model_config, record_usage
                ):
                    collected.append(chunk)
                    yield chunk
            except ApplicationError as exc:
                self._record_failed_run(
                    repository,
                    context,
                    run["id"],
                    command,
                    selected_model,
                    exc.code.value,
                    1,
                )
                raise
            except (asyncio.CancelledError, GeneratorExit):
                repository.append_run_step(
                    context.workspace_id,
                    run["id"],
                    1,
                    StepKind.MODEL.value,
                    selected_model,
                    StepStatus.FAILED.value,
                    command.prompt,
                    "".join(collected),
                    {"cancelled": True},
                )
                repository.finish_run(
                    context.workspace_id, run["id"], RunStatus.CANCELLED.value
                )
                raise
            except Exception:
                self._record_failed_run(
                    repository,
                    context,
                    run["id"],
                    command,
                    selected_model,
                    ErrorCode.INTERNAL_ERROR.value,
                    1,
                )
                raise

            content = "".join(collected)
            repository.add_message(
                context.workspace_id, session["id"], "assistant", content
            )
            repository.append_run_step(
                context.workspace_id,
                run["id"],
                1,
                StepKind.MODEL.value,
                selected_model,
                StepStatus.SUCCEEDED.value,
                command.prompt,
                content,
                {"stream": True},
            )
            repository.finish_run(
                context.workspace_id, run["id"], RunStatus.SUCCEEDED.value
            )

        return ChatStreamResult(
            session_id=session["id"], run_id=run["id"], chunks=chunks()
        )

    @staticmethod
    def _record_failed_run(
        repository: Repository,
        context: RequestContext,
        run_id: str,
        command: ChatCommand,
        selected_model: str,
        error_code: str,
        sequence: int,
    ) -> None:
        repository.append_run_step(
            context.workspace_id,
            run_id,
            sequence,
            StepKind.AGENT.value,
            "chat_agent",
            StepStatus.FAILED.value,
            command.prompt,
            "",
            {"model": selected_model, "error_code": error_code},
        )
        repository.finish_run(
            context.workspace_id, run_id, RunStatus.FAILED.value, error_code
        )

    @staticmethod
    def _owned_session(
        repository: Repository, workspace_id: str, session_id: str | None
    ) -> dict:
        if not session_id:
            return repository.create_session(workspace_id, "New conversation")
        session = repository.get_session(workspace_id, session_id)
        if session is None:
            raise ResourceNotFoundError("session", session_id)
        return session
