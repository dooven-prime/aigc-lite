"""Dispatch persistent schedule targets through registered application services."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from ..core.contracts import ChatCommand, RequestContext
from ..core.errors import InvalidScheduleError, ResourceNotFoundError
from ..core.scheduling import ScheduledTask
from .gateway import GatewayService
from .http_poll import HTTPPollService
from .mcp_probe import MCPProbeService
from .tools import ToolService
from .verification_runner import TARGET_NAME as VERIFICATION_TARGET_NAME
from .verification_runner import VerificationRunner

TaskTarget = Callable[[ScheduledTask], Awaitable[Any]]
TaskPayloadValidator = Callable[[dict[str, Any]], None]

_TARGET_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class TaskRunner:
    """Execute allowlisted schedule targets without importing arbitrary callables."""

    def __init__(
        self,
        *,
        gateway_service: GatewayService,
        http_poll_service: HTTPPollService | None = None,
        mcp_probe_service: MCPProbeService | None = None,
        tool_service: ToolService | None = None,
        verification_runner: VerificationRunner | None = None,
        targets: dict[str, TaskTarget] | None = None,
    ) -> None:
        self._gateway_service = gateway_service
        self._http_poll_service = http_poll_service or HTTPPollService()
        self._mcp_probe_service = mcp_probe_service or MCPProbeService()
        self._tool_service = tool_service or ToolService()
        self._targets: dict[str, TaskTarget] = {}
        self._validators: dict[str, TaskPayloadValidator] = {}
        self.register("agent.chat", self._run_agent_chat, self._agent_chat_command)
        self.register(
            "http.poll",
            self._run_http_poll,
            self._http_poll_service.validate_payload,
        )
        self.register("mcp.probe", self._run_mcp_probe, self._mcp_probe_server_id)
        self.register("tool.call", self._run_tool_call, self._tool_call_payload)
        if verification_runner is not None:
            self.register(
                VERIFICATION_TARGET_NAME,
                verification_runner.run_scheduled,
                verification_runner.validate_task_payload,
            )
        for name, target in (targets or {}).items():
            self.register(name, target)

    @property
    def target_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._targets))

    def register(
        self,
        name: str,
        target: TaskTarget,
        validator: TaskPayloadValidator | None = None,
    ) -> None:
        """Register one explicit target; dynamic imports are never resolved."""
        if not _TARGET_PATTERN.fullmatch(name):
            raise ValueError("Task target name must be a stable action identifier")
        if name in self._targets:
            raise ValueError(f"Task target already registered: {name}")
        self._targets[name] = target
        if validator is not None:
            self._validators[name] = validator

    async def run(self, task: ScheduledTask) -> Any:
        target = self._targets.get(task.target)
        if target is None:
            raise ResourceNotFoundError("schedule_target", task.target)
        return await target(task)

    def validate(self, target: str, payload: dict[str, Any]) -> None:
        """Reject unknown targets and malformed built-in payloads before storage."""
        if target not in self._targets:
            raise InvalidScheduleError(
                "target", "Scheduled target is not registered"
            )
        validator = self._validators.get(target)
        if validator is not None:
            validator(payload)

    async def _run_agent_chat(self, task: ScheduledTask):
        command = self._agent_chat_command(task.payload)
        return await self._gateway_service.chat(command, self._context(task))

    async def _run_mcp_probe(self, task: ScheduledTask) -> dict:
        server_id = self._mcp_probe_server_id(task.payload)
        return await self._mcp_probe_service.probe(
            self._context(task), server_id
        )

    async def _run_http_poll(self, task: ScheduledTask) -> dict[str, Any]:
        return await self._http_poll_service.poll(
            self._context(task), task.payload
        )

    async def _run_tool_call(self, task: ScheduledTask) -> dict[str, Any]:
        name, arguments = self._tool_call_payload(task.payload)
        result, run_id = await self._tool_service.invoke(
            self._context(task),
            name,
            arguments,
            transport="scheduler",
        )
        return {
            "run_id": run_id,
            "failed": result.failed,
            "content": result.content,
        }

    @staticmethod
    def _context(task: ScheduledTask) -> RequestContext:
        return RequestContext(
            request_id=f"schedule:{task.id}:{uuid4()}",
            workspace_id=task.workspace_id,
            principal_id="system:scheduler",
            scopes=frozenset(),
        )

    @staticmethod
    def _agent_chat_command(payload: dict[str, Any]) -> ChatCommand:
        allowed = {"prompt", "system", "model", "session_id"}
        unsupported = sorted(set(payload) - allowed)
        if unsupported:
            raise InvalidScheduleError(
                "payload",
                "agent.chat payload contains unsupported fields",
            )
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise InvalidScheduleError(
                "payload.prompt", "agent.chat requires a non-empty prompt"
            )
        system = payload.get("system", "You are a helpful assistant.")
        model = payload.get("model")
        session_id = payload.get("session_id")
        if not isinstance(system, str):
            raise InvalidScheduleError(
                "payload.system", "agent.chat system must be a string"
            )
        if model is not None and not isinstance(model, str):
            raise InvalidScheduleError(
                "payload.model", "agent.chat model must be a string"
            )
        if session_id is not None and not isinstance(session_id, str):
            raise InvalidScheduleError(
                "payload.session_id", "agent.chat session_id must be a string"
            )
        return ChatCommand(
            prompt=prompt,
            system=system,
            requested_model=model,
            session_id=session_id,
        )

    @staticmethod
    def _mcp_probe_server_id(payload: dict[str, Any]) -> str:
        unsupported = sorted(set(payload) - {"server_id"})
        if unsupported:
            raise InvalidScheduleError(
                "payload", "mcp.probe payload contains unsupported fields"
            )
        server_id = payload.get("server_id")
        if not isinstance(server_id, str) or not server_id.strip():
            raise InvalidScheduleError(
                "payload.server_id", "mcp.probe requires a server_id"
            )
        if len(server_id) > 128:
            raise InvalidScheduleError(
                "payload.server_id", "mcp.probe server_id is too long"
            )
        return server_id

    @staticmethod
    def _tool_call_payload(
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        unsupported = sorted(set(payload) - {"name", "arguments"})
        if unsupported:
            raise InvalidScheduleError(
                "payload", "tool.call payload contains unsupported fields"
            )
        name = payload.get("name")
        arguments = payload.get("arguments", {})
        if not isinstance(name, str) or not name.strip() or len(name) > 256:
            raise InvalidScheduleError(
                "payload.name", "tool.call requires a valid tool name"
            )
        if not isinstance(arguments, dict):
            raise InvalidScheduleError(
                "payload.arguments", "tool.call arguments must be an object"
            )
        return name, arguments
