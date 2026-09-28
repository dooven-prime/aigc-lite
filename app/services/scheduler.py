"""Persistent schedule lifecycle independent of wake-up and execution backends."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

from ..core.contracts import RequestContext
from ..core.errors import (
    InvalidScheduleError,
    ResourceNotFoundError,
    ScheduleNotActiveError,
)
from ..core.scheduling import (
    ScheduledTask,
    ScheduleKind,
    ScheduleStatus,
    ScheduleTaskCommand,
)
from ..database import get_repository
from ..repository import Repository

RepositoryProvider = Callable[[], Repository]
Clock = Callable[[], datetime]

_TARGET_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_MAX_PAYLOAD_BYTES = 64 * 1024


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise InvalidScheduleError(field, f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _utc(value, "stored_datetime")
    parsed = datetime.fromisoformat(str(value))
    if parsed.utcoffset() is None:  # pragma: no cover - persisted values are normalized
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _task(value: dict[str, Any]) -> ScheduledTask:
    return ScheduledTask(
        id=str(value["id"]),
        workspace_id=str(value["tenant_id"]),
        name=str(value["name"]),
        target=str(value["target"]),
        payload=dict(value.get("payload") or {}),
        kind=ScheduleKind(value["trigger_kind"]),
        status=ScheduleStatus(value["status"]),
        next_run_at=_datetime(value.get("next_run_at")),
        interval_seconds=(
            float(value["interval_seconds"])
            if value.get("interval_seconds") is not None
            else None
        ),
        last_run_at=_datetime(value.get("last_run_at")),
        created_at=_datetime(value["created_at"]),  # type: ignore[arg-type]
        updated_at=_datetime(value["updated_at"]),  # type: ignore[arg-type]
    )


class SchedulerService:
    """Store and transition schedules without executing their targets.

    A wake-up backend may rebuild its in-memory index with ``recover_active``
    and ask for overdue definitions with ``due``. A future task runner owns
    target dispatch and calls ``acknowledge`` only after accepting a trigger.
    """

    def __init__(
        self,
        *,
        repository_provider: RepositoryProvider = get_repository,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._repository_provider = repository_provider
        self._clock = clock

    def schedule(
        self, context: RequestContext, command: ScheduleTaskCommand
    ) -> ScheduledTask:
        values = self._validate(command)
        created = self._repository_provider().create_scheduled_task(
            context.workspace_id, values
        )
        return _task(created)

    def list(self, context: RequestContext) -> list[ScheduledTask]:
        return [
            _task(value)
            for value in self._repository_provider().list_scheduled_tasks(
                context.workspace_id
            )
        ]

    def get(self, context: RequestContext, task_id: str) -> ScheduledTask:
        value = self._repository_provider().get_scheduled_task(
            context.workspace_id, task_id
        )
        if value is None:
            raise ResourceNotFoundError("scheduled_task", task_id)
        return _task(value)

    def pause(self, context: RequestContext, task_id: str) -> ScheduledTask:
        task = self.get(context, task_id)
        if task.status is not ScheduleStatus.SCHEDULED:
            raise ScheduleNotActiveError(task.id, task.status.value)
        return self._set_status(
            context,
            task.id,
            ScheduleStatus.PAUSED,
            expected_status=ScheduleStatus.SCHEDULED,
        )

    def resume(self, context: RequestContext, task_id: str) -> ScheduledTask:
        task = self.get(context, task_id)
        if task.status is not ScheduleStatus.PAUSED:
            raise ScheduleNotActiveError(task.id, task.status.value)
        return self._set_status(
            context,
            task.id,
            ScheduleStatus.SCHEDULED,
            expected_status=ScheduleStatus.PAUSED,
        )

    def cancel(self, context: RequestContext, task_id: str) -> ScheduledTask:
        task = self.get(context, task_id)
        if task.status not in {ScheduleStatus.SCHEDULED, ScheduleStatus.PAUSED}:
            raise ScheduleNotActiveError(task.id, task.status.value)
        return self._set_status(
            context,
            task.id,
            ScheduleStatus.CANCELLED,
            expected_status=task.status,
        )

    def recover_active(self) -> list[ScheduledTask]:
        """Load the durable source of truth used to rebuild a wake-up index."""
        return [
            _task(value)
            for value in self._repository_provider().list_active_scheduled_tasks()
        ]

    def due(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> list[ScheduledTask]:
        due_at = _utc(now or self._clock(), "now")
        return [
            _task(value)
            for value in self._repository_provider().list_due_scheduled_tasks(
                _iso(due_at), limit
            )
        ]

    def acknowledge(
        self,
        context: RequestContext,
        task_id: str,
        *,
        fired_at: datetime | None = None,
    ) -> ScheduledTask:
        """Advance a trigger atomically after a runner has accepted it."""
        task = self.get(context, task_id)
        if task.status is not ScheduleStatus.SCHEDULED or task.next_run_at is None:
            raise ScheduleNotActiveError(task.id, task.status.value)
        fired = _utc(fired_at or self._clock(), "fired_at")
        if fired < task.next_run_at:
            raise InvalidScheduleError(
                "fired_at", "Scheduled task cannot be acknowledged before it is due"
            )

        next_run_at: datetime | None = None
        next_status = ScheduleStatus.COMPLETED
        if task.kind is ScheduleKind.INTERVAL:
            interval = task.interval_seconds
            if interval is None:  # pragma: no cover - protected by validation/schema
                raise InvalidScheduleError(
                    "interval_seconds", "Interval schedule requires an interval"
                )
            missed_seconds = max(
                0.0, (fired - task.next_run_at).total_seconds()
            )
            steps = int(missed_seconds // interval) + 1
            next_run_at = task.next_run_at + timedelta(seconds=steps * interval)
            next_status = ScheduleStatus.SCHEDULED

        value = self._repository_provider().advance_scheduled_task(
            context.workspace_id,
            task.id,
            expected_next_run_at=_iso(task.next_run_at),
            status=next_status.value,
            last_run_at=_iso(fired),
            next_run_at=_iso(next_run_at) if next_run_at else None,
        )
        if value is None:
            latest = self.get(context, task.id)
            raise ScheduleNotActiveError(latest.id, latest.status.value)
        return _task(value)

    def _set_status(
        self,
        context: RequestContext,
        task_id: str,
        status: ScheduleStatus,
        *,
        expected_status: ScheduleStatus,
    ) -> ScheduledTask:
        value = self._repository_provider().set_scheduled_task_status(
            context.workspace_id,
            task_id,
            status.value,
            expected_status=expected_status.value,
        )
        if value is None:
            latest = self.get(context, task_id)
            raise ScheduleNotActiveError(latest.id, latest.status.value)
        return _task(value)

    @staticmethod
    def _validate(command: ScheduleTaskCommand) -> dict[str, Any]:
        name = command.name.strip()
        if not name or len(name) > 200:
            raise InvalidScheduleError(
                "name", "Scheduled task name must contain 1 to 200 characters"
            )
        target = command.target.strip()
        if not _TARGET_PATTERN.fullmatch(target):
            raise InvalidScheduleError(
                "target", "Scheduled target must be a registered action identifier"
            )
        if not isinstance(command.payload, dict):
            raise InvalidScheduleError("payload", "Scheduled task payload must be an object")
        try:
            payload_text = json.dumps(command.payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise InvalidScheduleError(
                "payload", "Scheduled task payload must be JSON serializable"
            ) from exc
        if len(payload_text.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise InvalidScheduleError(
                "payload", "Scheduled task payload exceeds 64 KiB"
            )

        run_at = _utc(command.schedule.run_at, "run_at")
        interval = command.schedule.interval_seconds
        if command.schedule.kind is ScheduleKind.ONCE:
            if interval is not None:
                raise InvalidScheduleError(
                    "interval_seconds", "One-time schedule cannot define an interval"
                )
        elif command.schedule.kind is ScheduleKind.INTERVAL:
            if interval is None or not isfinite(interval) or interval <= 0:
                raise InvalidScheduleError(
                    "interval_seconds", "Interval schedule requires a positive interval"
                )
            interval = float(interval)
        else:  # pragma: no cover - enum guards construction in typed callers
            raise InvalidScheduleError("kind", "Unsupported schedule kind")

        return {
            "name": name,
            "target": target,
            "payload": command.payload,
            "trigger_kind": command.schedule.kind.value,
            "status": ScheduleStatus.SCHEDULED.value,
            "next_run_at": _iso(run_at),
            "interval_seconds": interval,
        }
