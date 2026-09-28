"""In-process hierarchical time-wheel backed by persistent schedule definitions."""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime

from ...core.contracts import RequestContext
from ...core.errors import ApplicationError, ResourceNotFoundError
from ...core.scheduling import ScheduledTask, ScheduleStatus
from ...services.scheduler import SchedulerService
from ...services.task_runner import TaskRunner

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Wakeup:
    task_id: str
    workspace_id: str
    expected_next_run_at: str
    due_at: datetime


class _WheelLevel:
    def __init__(self, slots: int, tick_seconds: float) -> None:
        self.slots = slots
        self.tick_seconds = tick_seconds
        self.current_pos = 0
        self.buckets: list[list[tuple[int, _Wakeup]]] = [
            [] for _ in range(slots)
        ]

    @property
    def span_seconds(self) -> float:
        return self.slots * self.tick_seconds

    def clear(self) -> None:
        self.current_pos = 0
        for bucket in self.buckets:
            bucket.clear()

    def add(self, delay_seconds: float, wakeup: _Wakeup, *, base: bool) -> None:
        scaled = max(0.0, delay_seconds) / self.tick_seconds
        ticks = max(1, math.ceil(scaled) if base else math.floor(scaled))
        rounds = (ticks - 1) // self.slots
        position = (self.current_pos + ticks) % self.slots
        self.buckets[position].append((rounds, wakeup))

    def advance(self) -> tuple[list[_Wakeup], bool]:
        self.current_pos = (self.current_pos + 1) % self.slots
        entries = self.buckets[self.current_pos]
        self.buckets[self.current_pos] = []
        ready: list[_Wakeup] = []
        for rounds, wakeup in entries:
            if rounds > 0:
                self.buckets[self.current_pos].append((rounds - 1, wakeup))
            else:
                ready.append(wakeup)
        return ready, self.current_pos == 0


class TimeWheelIndex:
    """A disposable wake-up index; SQLite remains the source of truth."""

    def __init__(self, tick_seconds: float = 1.0) -> None:
        if not math.isfinite(tick_seconds) or tick_seconds <= 0:
            raise ValueError("tick_seconds must be positive")
        self.tick_seconds = float(tick_seconds)
        self._levels = [
            _WheelLevel(60, self.tick_seconds),
            _WheelLevel(60, self.tick_seconds * 60),
            _WheelLevel(24, self.tick_seconds * 60 * 60),
            _WheelLevel(365, self.tick_seconds * 60 * 60 * 24),
        ]
        self._expected: dict[str, str] = {}

    def clear(self) -> None:
        self._expected.clear()
        for level in self._levels:
            level.clear()

    def discard(self, task_id: str) -> None:
        self._expected.pop(task_id, None)

    def add(self, task: ScheduledTask, *, now: datetime) -> None:
        if task.status is not ScheduleStatus.SCHEDULED or task.next_run_at is None:
            self.discard(task.id)
            return
        due_at = task.next_run_at.astimezone(UTC)
        expected = due_at.isoformat()
        self._expected[task.id] = expected
        wakeup = _Wakeup(task.id, task.workspace_id, expected, due_at)
        delay = max(0.0, (due_at - now.astimezone(UTC)).total_seconds())
        level_index = len(self._levels) - 1
        for index, level in enumerate(self._levels):
            if delay <= level.span_seconds:
                level_index = index
                break
        self._levels[level_index].add(
            delay, wakeup, base=level_index == 0
        )

    def rebuild(self, tasks: list[ScheduledTask], *, now: datetime) -> None:
        self.clear()
        for task in tasks:
            self.add(task, now=now)

    def advance(self, *, now: datetime) -> list[_Wakeup]:
        candidates = self._advance_level(0)
        due: list[_Wakeup] = []
        current = now.astimezone(UTC)
        for wakeup in candidates:
            if self._expected.get(wakeup.task_id) != wakeup.expected_next_run_at:
                continue
            if wakeup.due_at <= current:
                self._expected.pop(wakeup.task_id, None)
                due.append(wakeup)
            else:
                self._place(wakeup, now=current)
        return due

    def _place(self, wakeup: _Wakeup, *, now: datetime) -> None:
        delay = max(0.0, (wakeup.due_at - now).total_seconds())
        level_index = len(self._levels) - 1
        for index, level in enumerate(self._levels):
            if delay <= level.span_seconds:
                level_index = index
                break
        self._levels[level_index].add(
            delay, wakeup, base=level_index == 0
        )

    def _advance_level(self, index: int) -> list[_Wakeup]:
        ready, wrapped = self._levels[index].advance()
        if wrapped and index + 1 < len(self._levels):
            ready.extend(self._advance_level(index + 1))
        return ready


class TimeWheelScheduler:
    """Recover schedules, wake due targets, and advance durable state."""

    def __init__(
        self,
        *,
        scheduler_service: SchedulerService,
        task_runner: TaskRunner,
        tick_seconds: float = 1.0,
        reconcile_seconds: float = 5.0,
        max_concurrency: int = 4,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        if not math.isfinite(reconcile_seconds) or reconcile_seconds <= 0:
            raise ValueError("reconcile_seconds must be positive")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self._scheduler_service = scheduler_service
        self._task_runner = task_runner
        self._tick_seconds = float(tick_seconds)
        self._reconcile_seconds = float(reconcile_seconds)
        self._clock = clock
        self._index = TimeWheelIndex(tick_seconds)
        self._max_concurrency = max_concurrency
        self._semaphore: asyncio.Semaphore | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._inflight: dict[str, asyncio.Task[None]] = {}
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stopping = False
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        await self._reconcile(self._clock())
        self._loop_task = asyncio.create_task(
            self._run_loop(), name="aigc-lite-timewheel"
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None
        workers = list(self._inflight.values())
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._inflight.clear()
        self._index.clear()
        self._semaphore = None

    def notify(self, task: ScheduledTask) -> None:
        """Index a newly created or resumed definition without waiting to reconcile."""
        self._index.add(task, now=self._clock())

    async def dispatch_due(self, *, now: datetime | None = None) -> None:
        """Dispatch the durable due set once, useful for startup and operators."""
        current = (now or self._clock()).astimezone(UTC)
        for task in self._scheduler_service.due(now=current, limit=1000):
            self._dispatch(task)

    async def wait_idle(self) -> None:
        workers = list(self._inflight.values())
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

    async def _run_loop(self) -> None:
        loop = asyncio.get_running_loop()
        last_reconcile = loop.time()
        try:
            while True:
                await asyncio.sleep(self._tick_seconds)
                now = self._clock().astimezone(UTC)
                for wakeup in self._index.advance(now=now):
                    self._dispatch_wakeup(wakeup, now)
                if loop.time() - last_reconcile >= self._reconcile_seconds:
                    await self._reconcile(now)
                    last_reconcile = loop.time()
        except asyncio.CancelledError:
            raise

    async def _reconcile(self, now: datetime) -> None:
        await self.dispatch_due(now=now)
        self._index.rebuild(self._scheduler_service.recover_active(), now=now)

    def _dispatch_wakeup(self, wakeup: _Wakeup, now: datetime) -> None:
        context = RequestContext(
            request_id=f"schedule-wakeup:{wakeup.task_id}",
            workspace_id=wakeup.workspace_id,
            principal_id="system:scheduler",
        )
        try:
            task = self._scheduler_service.get(context, wakeup.task_id)
        except ResourceNotFoundError:
            return
        if (
            task.status is not ScheduleStatus.SCHEDULED
            or task.next_run_at is None
            or task.next_run_at.isoformat() != wakeup.expected_next_run_at
        ):
            return
        if task.next_run_at > now:
            self._index.add(task, now=now)
            return
        self._dispatch(task)

    def _dispatch(self, task: ScheduledTask) -> None:
        if task.id in self._inflight or self._stopping:
            return
        self._index.discard(task.id)
        worker = asyncio.create_task(
            self._execute(task), name=f"scheduled-task:{task.id}"
        )
        self._inflight[task.id] = worker

    async def _execute(self, task: ScheduledTask) -> None:
        accepted = False
        cancelled_by_shutdown = False
        try:
            semaphore = self._semaphore
            if semaphore is None:  # pragma: no cover - workers only exist while started
                return
            async with semaphore:
                accepted = True
                await self._task_runner.run(task)
        except asyncio.CancelledError:
            cancelled_by_shutdown = self._stopping
            if cancelled_by_shutdown:
                raise
        except Exception:
            logger.exception(
                "Scheduled target failed",
                extra={"scheduled_task_id": task.id, "target": task.target},
            )
        finally:
            try:
                if accepted and not cancelled_by_shutdown:
                    context = RequestContext(
                        request_id=f"schedule-ack:{task.id}",
                        workspace_id=task.workspace_id,
                        principal_id="system:scheduler",
                    )
                    advanced = self._scheduler_service.acknowledge(
                        context, task.id, fired_at=self._clock()
                    )
                    self.notify(advanced)
            except ApplicationError:
                logger.info(
                    "Scheduled task changed state before acknowledgement",
                    extra={"scheduled_task_id": task.id},
                )
            finally:
                self._inflight.pop(task.id, None)
