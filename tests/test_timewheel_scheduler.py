import asyncio
from datetime import UTC, datetime, timedelta

from app.adapters.scheduling.timewheel import TimeWheelIndex, TimeWheelScheduler
from app.core.contracts import RequestContext
from app.core.scheduling import (
    ScheduledTask,
    ScheduleKind,
    ScheduleSpec,
    ScheduleStatus,
    ScheduleTaskCommand,
)
from app.repository import SQLiteRepository
from app.services.gateway import GatewayService
from app.services.scheduler import SchedulerService
from app.services.task_runner import TaskRunner


def _context() -> RequestContext:
    return RequestContext(request_id="request-1", workspace_id="workspace-a")


def _scheduled(service: SchedulerService, now: datetime, *, interval=None):
    kind = ScheduleKind.INTERVAL if interval else ScheduleKind.ONCE
    return service.schedule(
        _context(),
        ScheduleTaskCommand(
            name="Scheduled operation",
            target="test.capture",
            payload={"value": 1},
            schedule=ScheduleSpec(kind, now, interval_seconds=interval),
        ),
    )


def test_timewheel_index_cascades_upper_level_wakeups() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    projected = ScheduledTask(
        id="task-61",
        workspace_id="workspace-a",
        name="Later",
        target="test.capture",
        payload={},
        kind=ScheduleKind.ONCE,
        status=ScheduleStatus.SCHEDULED,
        next_run_at=now + timedelta(seconds=61),
        interval_seconds=None,
        last_run_at=None,
        created_at=now,
        updated_at=now,
    )
    index = TimeWheelIndex(tick_seconds=1)
    index.add(projected, now=now)

    for second in range(1, 61):
        assert index.advance(now=now + timedelta(seconds=second)) == []
    wakeups = index.advance(now=now + timedelta(seconds=61))

    assert [item.task_id for item in wakeups] == ["task-61"]


def test_backend_recovers_due_task_runs_it_and_completes_schedule(tmp_path) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    repository = SQLiteRepository(tmp_path / "timewheel.db")
    repository.init()
    scheduler = SchedulerService(
        repository_provider=lambda: repository, clock=lambda: now
    )
    scheduled = _scheduled(scheduler, now)
    calls = []

    class CaptureRunner:
        async def run(self, task):
            calls.append(task)

    backend = TimeWheelScheduler(
        scheduler_service=scheduler,
        task_runner=CaptureRunner(),
        tick_seconds=3600,
        clock=lambda: now,
    )

    async def scenario() -> None:
        await backend.start()
        await backend.wait_idle()
        await backend.stop()

    asyncio.run(scenario())

    assert [item.id for item in calls] == [scheduled.id]
    stored = scheduler.get(_context(), scheduled.id)
    assert stored.status is ScheduleStatus.COMPLETED
    assert stored.last_run_at == now


def test_backend_advances_and_reindexes_interval_task(tmp_path) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    repository = SQLiteRepository(tmp_path / "interval-wheel.db")
    repository.init()
    scheduler = SchedulerService(
        repository_provider=lambda: repository, clock=lambda: now
    )
    scheduled = _scheduled(scheduler, now, interval=30)
    calls = []

    class CaptureRunner:
        async def run(self, task):
            calls.append(task.id)

    backend = TimeWheelScheduler(
        scheduler_service=scheduler,
        task_runner=CaptureRunner(),
        tick_seconds=3600,
        clock=lambda: now,
    )

    async def scenario() -> None:
        await backend.start()
        await backend.wait_idle()
        await backend.stop()

    asyncio.run(scenario())

    assert calls == [scheduled.id]
    stored = scheduler.get(_context(), scheduled.id)
    assert stored.status is ScheduleStatus.SCHEDULED
    assert stored.next_run_at == now + timedelta(seconds=30)


def test_backend_dispatches_agent_target_through_gateway_service(tmp_path) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    repository = SQLiteRepository(tmp_path / "gateway-wheel.db")
    repository.init()
    scheduler = SchedulerService(
        repository_provider=lambda: repository, clock=lambda: now
    )
    scheduled = scheduler.schedule(
        _context(),
        ScheduleTaskCommand(
            name="Proactive summary",
            target="agent.chat",
            payload={"prompt": "Summarize pending work"},
            schedule=ScheduleSpec(ScheduleKind.ONCE, now),
        ),
    )

    async def fake_agent(*args, **kwargs):
        return "scheduled answer"

    gateway = GatewayService(
        repository_provider=lambda: repository,
        agent_runner=fake_agent,
    )
    backend = TimeWheelScheduler(
        scheduler_service=scheduler,
        task_runner=TaskRunner(gateway_service=gateway),
        tick_seconds=3600,
        clock=lambda: now,
    )

    async def scenario() -> None:
        await backend.start()
        await backend.wait_idle()
        await backend.stop()

    asyncio.run(scenario())

    assert scheduler.get(_context(), scheduled.id).status is ScheduleStatus.COMPLETED
    runs = repository.list_runs("workspace-a")
    assert len(runs) == 1
    assert runs[0]["status"] == "succeeded"
    session = repository.get_session("workspace-a", runs[0]["session_id"])
    assert [message["content"] for message in session["messages"]] == [
        "Summarize pending work",
        "scheduled answer",
    ]


def test_shutdown_cancellation_leaves_schedule_due_for_restart(tmp_path) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    repository = SQLiteRepository(tmp_path / "shutdown-wheel.db")
    repository.init()
    scheduler = SchedulerService(
        repository_provider=lambda: repository, clock=lambda: now
    )
    scheduled = _scheduled(scheduler, now)
    started = asyncio.Event()

    class BlockingRunner:
        async def run(self, task):
            started.set()
            await asyncio.Future()

    backend = TimeWheelScheduler(
        scheduler_service=scheduler,
        task_runner=BlockingRunner(),
        tick_seconds=3600,
        clock=lambda: now,
    )

    async def scenario() -> None:
        await backend.start()
        await started.wait()
        await backend.stop()

    asyncio.run(scenario())

    stored = scheduler.get(_context(), scheduled.id)
    assert stored.status is ScheduleStatus.SCHEDULED
    assert stored.next_run_at == now


def test_two_workers_claim_one_due_occurrence_once(tmp_path) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    path = tmp_path / "shared-wheel.db"
    first_repository = SQLiteRepository(path)
    first_repository.init()
    second_repository = SQLiteRepository(path)
    first_service = SchedulerService(repository_provider=lambda: first_repository, clock=lambda: now)
    second_service = SchedulerService(repository_provider=lambda: second_repository, clock=lambda: now)
    scheduled = _scheduled(first_service, now)
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    class SlowRunner:
        async def run(self, task):
            calls.append(task.execution_id)
            started.set()
            await release.wait()

    first = TimeWheelScheduler(
        scheduler_service=first_service, task_runner=SlowRunner(),
        tick_seconds=3600, clock=lambda: now,
    )
    second = TimeWheelScheduler(
        scheduler_service=second_service, task_runner=SlowRunner(),
        tick_seconds=3600, clock=lambda: now,
    )

    async def scenario():
        await first.start()
        await started.wait()
        await second.start()
        await second.wait_idle()
        assert len(calls) == 1
        release.set()
        await asyncio.gather(first.wait_idle(), second.wait_idle())
        await asyncio.gather(first.stop(), second.stop())

    asyncio.run(scenario())
    assert len(calls) == 1
    assert calls[0]
    firing = first_repository.get_scheduled_firing(calls[0])
    assert firing["state"] == "succeeded"
    assert first_service.get(_context(), scheduled.id).status is ScheduleStatus.COMPLETED


def test_cancelled_firing_is_not_silently_replayed_by_new_worker(tmp_path) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    repository = SQLiteRepository(tmp_path / "indeterminate-wheel.db")
    repository.init()
    scheduler = SchedulerService(repository_provider=lambda: repository, clock=lambda: now)
    scheduled = _scheduled(scheduler, now)
    started = asyncio.Event()
    firings: list[str] = []

    class InterruptedRunner:
        async def run(self, task):
            firings.append(task.execution_id)
            started.set()
            await asyncio.Future()

    first = TimeWheelScheduler(
        scheduler_service=scheduler, task_runner=InterruptedRunner(),
        tick_seconds=3600, clock=lambda: now,
    )

    async def scenario():
        await first.start()
        await started.wait()
        await first.stop()
        second = TimeWheelScheduler(
            scheduler_service=SchedulerService(repository_provider=lambda: SQLiteRepository(repository.database_path), clock=lambda: now),
            task_runner=InterruptedRunner(), tick_seconds=3600, clock=lambda: now,
        )
        await second.start()
        await second.wait_idle()
        await second.stop()

    asyncio.run(scenario())
    assert len(firings) == 1
    assert repository.get_scheduled_firing(firings[0])["state"] == "indeterminate"
    assert scheduler.get(_context(), scheduled.id).status is ScheduleStatus.SCHEDULED
