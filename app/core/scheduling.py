"""Transport-neutral contracts for persistent task scheduling."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ScheduleKind(StrEnum):
    """Supported trigger shapes for the first scheduler baseline."""

    ONCE = "once"
    INTERVAL = "interval"


class ScheduleStatus(StrEnum):
    """Lifecycle of a schedule definition, not an Agent execution attempt."""

    SCHEDULED = "scheduled"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ScheduleSpec:
    """When a registered target should next be activated."""

    kind: ScheduleKind
    run_at: datetime
    interval_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ScheduleTaskCommand:
    """Create one persistent schedule for a registered internal target."""

    name: str
    target: str
    schedule: ScheduleSpec
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    """Persistent schedule projected independently of its storage adapter."""

    id: str
    workspace_id: str
    name: str
    target: str
    payload: dict
    kind: ScheduleKind
    status: ScheduleStatus
    next_run_at: datetime | None
    interval_seconds: float | None
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
