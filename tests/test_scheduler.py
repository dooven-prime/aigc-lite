from datetime import UTC, datetime, timedelta

import pytest

from app.core.contracts import RequestContext
from app.core.errors import (
    InvalidScheduleError,
    ResourceNotFoundError,
    ScheduleNotActiveError,
)
from app.core.scheduling import (
    ScheduleKind,
    ScheduleSpec,
    ScheduleStatus,
    ScheduleTaskCommand,
)
from app.repository import SQLiteRepository
from app.services.scheduler import SchedulerService


def _context(workspace_id: str = "workspace-a") -> RequestContext:
    return RequestContext(request_id="request-1", workspace_id=workspace_id)


def _service(tmp_path, now: datetime):
    repository = SQLiteRepository(tmp_path / "scheduler.db")
    repository.init()
    service = SchedulerService(
        repository_provider=lambda: repository,
        clock=lambda: now,
    )
    return repository, service


def test_one_time_schedule_is_persistent_due_and_completed(tmp_path) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    repository, service = _service(tmp_path, now)
    task = service.schedule(
        _context(),
        ScheduleTaskCommand(
            name="Morning summary",
            target="agent.chat",
            payload={"prompt": "Summarize open work"},
            schedule=ScheduleSpec(ScheduleKind.ONCE, now + timedelta(minutes=5)),
        ),
    )

    assert task.status is ScheduleStatus.SCHEDULED
    assert task.workspace_id == "workspace-a"
    assert task.payload == {"prompt": "Summarize open work"}
    assert service.due(now=now + timedelta(minutes=4)) == []
    assert [item.id for item in service.due(now=now + timedelta(minutes=5))] == [
        task.id
    ]

    completed = service.acknowledge(
        _context(), task.id, fired_at=now + timedelta(minutes=5)
    )

    assert completed.status is ScheduleStatus.COMPLETED
    assert completed.next_run_at is None
    assert completed.last_run_at == now + timedelta(minutes=5)
    assert repository.list_due_scheduled_tasks(
        (now + timedelta(days=1)).isoformat()
    ) == []


def test_interval_schedule_skips_missed_ticks_and_recovers_after_restart(tmp_path) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    repository, service = _service(tmp_path, now)
    task = service.schedule(
        _context(),
        ScheduleTaskCommand(
            name="Probe MCP",
            target="mcp.probe",
            payload={"server_id": "server-1"},
            schedule=ScheduleSpec(
                ScheduleKind.INTERVAL,
                now + timedelta(seconds=10),
                interval_seconds=10,
            ),
        ),
    )

    advanced = service.acknowledge(
        _context(), task.id, fired_at=now + timedelta(seconds=35)
    )

    assert advanced.status is ScheduleStatus.SCHEDULED
    assert advanced.last_run_at == now + timedelta(seconds=35)
    assert advanced.next_run_at == now + timedelta(seconds=40)

    restarted = SchedulerService(repository_provider=lambda: repository)
    assert restarted.recover_active() == [advanced]


def test_schedule_transitions_remain_workspace_scoped(tmp_path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    _, service = _service(tmp_path, now)
    task = service.schedule(
        _context("workspace-a"),
        ScheduleTaskCommand(
            name="Internal cleanup",
            target="system.cleanup",
            schedule=ScheduleSpec(ScheduleKind.ONCE, now),
        ),
    )

    with pytest.raises(ResourceNotFoundError):
        service.get(_context("workspace-b"), task.id)

    paused = service.pause(_context("workspace-a"), task.id)
    assert paused.status is ScheduleStatus.PAUSED
    assert service.due(now=now + timedelta(days=1)) == []

    resumed = service.resume(_context("workspace-a"), task.id)
    assert resumed.status is ScheduleStatus.SCHEDULED
    cancelled = service.cancel(_context("workspace-a"), task.id)
    assert cancelled.status is ScheduleStatus.CANCELLED

    with pytest.raises(ScheduleNotActiveError):
        service.cancel(_context("workspace-a"), task.id)


@pytest.mark.parametrize(
    ("command", "field"),
    [
        (
            ScheduleTaskCommand(
                name="bad target",
                target="module:arbitrary callable()",
                schedule=ScheduleSpec(
                    ScheduleKind.ONCE, datetime(2026, 1, 1, tzinfo=UTC)
                ),
            ),
            "target",
        ),
        (
            ScheduleTaskCommand(
                name="bad interval",
                target="agent.chat",
                schedule=ScheduleSpec(
                    ScheduleKind.INTERVAL,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    interval_seconds=0,
                ),
            ),
            "interval_seconds",
        ),
        (
            ScheduleTaskCommand(
                name="naive time",
                target="agent.chat",
                schedule=ScheduleSpec(ScheduleKind.ONCE, datetime(2026, 1, 1)),
            ),
            "run_at",
        ),
    ],
)
def test_invalid_schedules_have_stable_errors(tmp_path, command, field) -> None:
    _, service = _service(tmp_path, datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(InvalidScheduleError) as raised:
        service.schedule(_context(), command)

    assert raised.value.metadata == {"field": field}
