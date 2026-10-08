"""Deterministic backend used for CI and policy/evidence demonstrations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from math import hypot
from typing import Any
from uuid import uuid4

from .contracts import (
    ActionFeedback,
    ExecutionEnvironment,
    NavigationRequest,
    PhysicalActionReceipt,
    PhysicalActionStatus,
    Pose2D,
    RobotBridgeError,
    RobotState,
    utc_now,
)


@dataclass(slots=True)
class _ActiveAction:
    action_id: str
    request: NavigationRequest
    before: RobotState
    cancel_event: asyncio.Event
    result: asyncio.Future[PhysicalActionReceipt]
    feedback: list[ActionFeedback] = field(default_factory=list)


class SimulatorBackend:
    """Small stateful robot model; it never imports ROS packages."""

    def __init__(
        self,
        *,
        robot_id: str = "robot-1",
        map_id: str = "default-map",
        travel_seconds: float = 0.2,
        outcome: str = "success",
        initial_pose: Pose2D | None = None,
    ) -> None:
        if travel_seconds <= 0:
            raise ValueError("travel_seconds must be positive")
        if outcome not in {
            "success",
            "failure",
            "indeterminate",
            "goal_rejected",
            "feedback_stall",
            "transport_loss",
            "localization_loss",
            "cancel_unconfirmed",
        }:
            raise ValueError("invalid simulator outcome")
        self.robot_id = robot_id
        self.map_id = map_id
        self.travel_seconds = travel_seconds
        self.outcome = outcome
        self.environment = ExecutionEnvironment.SIMULATION
        self._pose = initial_pose or Pose2D(0.0, 0.0, 0.0, frame_id=map_id)
        self._navigation_status = "idle"
        self._emergency_stop_engaged = False
        self._localized = True
        self._active: _ActiveAction | None = None
        self._receipts: dict[str, PhysicalActionReceipt] = {}
        self._key_to_action: dict[str, str] = {}
        self._latest: PhysicalActionReceipt | None = None
        self._lock = asyncio.Lock()

    def _state_unlocked(self) -> RobotState:
        return RobotState(
            robot_id=self.robot_id,
            environment=self.environment,
            observed_at=utc_now(),
            pose=self._pose,
            navigation_status=self._navigation_status,
            active_action_id=self._active.action_id if self._active else None,
            emergency_stop_engaged=self._emergency_stop_engaged,
            localized=self._localized,
            map_id=self.map_id,
            metadata={"backend": "simulator"},
        )

    async def get_state(self) -> RobotState:
        async with self._lock:
            return self._state_unlocked()

    async def latest_receipt(self) -> PhysicalActionReceipt | None:
        async with self._lock:
            return self._latest

    async def set_emergency_stop(self, engaged: bool) -> None:
        """Test/simulator control; intentionally not exposed as an MCP tool."""
        async with self._lock:
            self._emergency_stop_engaged = engaged
            if engaged and self._active is not None:
                self._active.cancel_event.set()

    async def navigate_to(self, request: NavigationRequest) -> PhysicalActionReceipt:
        replay: asyncio.Future[PhysicalActionReceipt] | None = None
        active: _ActiveAction | None = None
        async with self._lock:
            known_action_id = self._key_to_action.get(request.idempotency_key)
            if known_action_id is not None:
                known = self._receipts.get(known_action_id)
                if known is not None:
                    if not self._same_intent(known.request, request):
                        raise RobotBridgeError(
                            "idempotency_conflict",
                            "The idempotency key is already bound to a different goal.",
                        )
                    return replace(
                        known,
                        metadata={**known.metadata, "idempotent_replay": True},
                    )
                if self._active and self._active.action_id == known_action_id:
                    if not self._same_intent(self._active.request.as_dict(), request):
                        raise RobotBridgeError(
                            "idempotency_conflict",
                            "The idempotency key is already bound to a different goal.",
                        )
                    replay = self._active.result
            elif self._active is not None:
                raise RobotBridgeError(
                    "robot_busy",
                    "Another physical action is already active.",
                    retryable=True,
                    details={"active_action_id": self._active.action_id},
                )
            elif self._emergency_stop_engaged:
                raise RobotBridgeError(
                    "emergency_stop_engaged",
                    "Navigation is blocked while the emergency stop is engaged.",
                )
            elif not self._localized:
                raise RobotBridgeError(
                    "robot_not_localized",
                    "Navigation requires a localized robot state.",
                    retryable=True,
                )
            else:
                action_id = str(uuid4())
                active = _ActiveAction(
                    action_id=action_id,
                    request=request,
                    before=self._state_unlocked(),
                    cancel_event=asyncio.Event(),
                    result=asyncio.get_running_loop().create_future(),
                )
                self._active = active
                self._key_to_action[request.idempotency_key] = action_id
                self._navigation_status = "executing"

        if replay is not None:
            receipt = await asyncio.shield(replay)
            return replace(
                receipt,
                metadata={**receipt.metadata, "idempotent_replay": True},
            )
        if active is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("navigation action was not created")
        try:
            return await self._execute(active)
        except asyncio.CancelledError:
            active.cancel_event.set()
            await asyncio.shield(
                self._finish_cancelled(
                    active,
                    error_code="caller_cancelled",
                    error_message="The MCP caller cancelled the action wait.",
                )
            )
            raise

    @staticmethod
    def _same_intent(existing: dict[str, Any], request: NavigationRequest) -> bool:
        return (
            existing.get("goal") == request.goal.as_dict()
            and existing.get("action_timeout_seconds") == request.action_timeout_seconds
        )

    async def _execute(self, active: _ActiveAction) -> PhysicalActionReceipt:
        request = active.request
        started_at = utc_now()
        start = active.before.pose or Pose2D(0.0, 0.0, 0.0)
        steps = 10
        interval = self.travel_seconds / steps
        deadline = asyncio.get_running_loop().time() + request.action_timeout_seconds
        distance = hypot(request.goal.x - start.x, request.goal.y - start.y)

        if self.outcome == "goal_rejected":
            return await self._finish(
                active,
                status=PhysicalActionStatus.FAILED,
                started_at=started_at,
                stop_confirmed=True,
                error_code="navigation_goal_rejected",
                error_message="The simulator rejected the navigation goal before motion.",
            )

        for index in range(1, steps + 1):
            remaining_time = deadline - asyncio.get_running_loop().time()
            if remaining_time <= 0:
                return await self._finish_cancelled(
                    active,
                    error_code="action_timeout",
                    error_message="The action exceeded its declared wall-clock timeout.",
                    started_at=started_at,
                )
            try:
                await asyncio.wait_for(
                    active.cancel_event.wait(), timeout=min(interval, remaining_time)
                )
            except TimeoutError:
                pass
            if active.cancel_event.is_set():
                return await self._finish_cancelled(
                    active,
                    error_code=(
                        "emergency_stop_engaged"
                        if self._emergency_stop_engaged
                        else "action_cancelled"
                    ),
                    error_message="The simulator confirmed that motion stopped.",
                    started_at=started_at,
                )
            if self.outcome in {"transport_loss", "localization_loss"} and index == 5:
                if self.outcome == "localization_loss":
                    async with self._lock:
                        self._localized = False
                return await self._finish(
                    active,
                    status=PhysicalActionStatus.INDETERMINATE,
                    started_at=started_at,
                    stop_confirmed=False,
                    error_code=(
                        "transport_lost"
                        if self.outcome == "transport_loss"
                        else "localization_lost"
                    ),
                    error_message="The simulator cannot confirm that physical motion stopped.",
                )
            progress = index / steps
            pose = Pose2D(
                x=start.x + (request.goal.x - start.x) * progress,
                y=start.y + (request.goal.y - start.y) * progress,
                yaw=start.yaw + (request.goal.yaw - start.yaw) * progress,
                frame_id=request.goal.frame_id,
            )
            feedback = ActionFeedback(
                observed_at=utc_now(),
                phase="executing",
                progress=progress,
                distance_remaining=max(0.0, distance * (1.0 - progress)),
            )
            if self.outcome != "feedback_stall" or index <= 2:
                active.feedback.append(feedback)
            async with self._lock:
                if self._active is active:
                    self._pose = pose

        if self.outcome == "failure":
            return await self._finish(
                active,
                status=PhysicalActionStatus.FAILED,
                started_at=started_at,
                stop_confirmed=True,
                error_code="navigation_failed",
                error_message="The simulator injected a navigation failure.",
            )
        if self.outcome == "indeterminate":
            return await self._finish(
                active,
                status=PhysicalActionStatus.INDETERMINATE,
                started_at=started_at,
                stop_confirmed=False,
                error_code="robot_state_indeterminate",
                error_message="The simulator could not confirm the terminal robot state.",
            )
        return await self._finish(
            active,
            status=PhysicalActionStatus.SUCCEEDED,
            started_at=started_at,
            stop_confirmed=True,
        )

    async def _finish_cancelled(
        self,
        active: _ActiveAction,
        *,
        error_code: str,
        error_message: str,
        started_at: str | None = None,
    ) -> PhysicalActionReceipt:
        if self.outcome == "cancel_unconfirmed":
            return await self._finish(
                active,
                status=PhysicalActionStatus.INDETERMINATE,
                started_at=started_at or active.request.requested_at,
                stop_confirmed=False,
                error_code="cancel_confirmation_timeout",
                error_message="Cancellation was requested but the simulator did not confirm stop.",
            )
        return await self._finish(
            active,
            status=PhysicalActionStatus.CANCELLED,
            started_at=started_at or active.request.requested_at,
            stop_confirmed=True,
            error_code=error_code,
            error_message=error_message,
        )

    async def _finish(
        self,
        active: _ActiveAction,
        *,
        status: PhysicalActionStatus,
        started_at: str,
        stop_confirmed: bool,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> PhysicalActionReceipt:
        async with self._lock:
            if active.result.done():
                return active.result.result()
            if self._active is active and stop_confirmed:
                self._active = None
            self._navigation_status = (
                "idle"
                if status in {PhysicalActionStatus.SUCCEEDED, PhysicalActionStatus.CANCELLED}
                else "unknown"
                if status is PhysicalActionStatus.INDETERMINATE
                else "failed"
            )
            after = self._state_unlocked()
            receipt = PhysicalActionReceipt(
                action_id=active.action_id,
                idempotency_key=active.request.idempotency_key,
                capability="robot.navigate_to",
                robot_id=self.robot_id,
                environment=self.environment,
                status=status,
                request=active.request.as_dict(),
                requested_at=active.request.requested_at,
                started_at=started_at,
                completed_at=utc_now(),
                provider_action_id=f"sim:{active.action_id}",
                stop_confirmed=stop_confirmed,
                observation_before=active.before.as_dict(),
                observation_after=after.as_dict(),
                feedback=tuple(active.feedback[-32:]),
                error_code=error_code,
                error_message=error_message,
                metadata={"backend": "simulator", "map_id": self.map_id},
            )
            self._receipts[active.action_id] = receipt
            self._latest = receipt
            active.result.set_result(receipt)
            return receipt

    async def confirm_stopped(self, action_id: str) -> PhysicalActionReceipt:
        """Simulator-only operator reconciliation; never exposed as an MCP capability."""

        async with self._lock:
            current = self._receipts.get(action_id)
            if (
                self._active is None
                or self._active.action_id != action_id
                or current is None
                or current.status is not PhysicalActionStatus.INDETERMINATE
                or current.stop_confirmed is not False
            ):
                raise RobotBridgeError(
                    "reconciliation_not_applicable", "No indeterminate action owns the motion slot."
                )
            self._active = None
            self._navigation_status = "idle"
            updated = replace(
                current,
                status=PhysicalActionStatus.CANCELLED,
                stop_confirmed=True,
                completed_at=utc_now(),
                observation_after=self._state_unlocked().as_dict(),
                error_code="simulator_operator_stop_confirmed",
                error_message="A simulator-only operator control confirmed stop.",
                metadata={**current.metadata, "reconciled_by": "simulator_operator_control"},
            )
            self._receipts[action_id] = updated
            self._latest = updated
            return updated

    async def cancel_action(
        self,
        *,
        action_id: str | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> PhysicalActionReceipt:
        if not action_id and not idempotency_key:
            raise RobotBridgeError(
                "invalid_cancel_target",
                "action_id or idempotency_key is required.",
            )
        async with self._lock:
            resolved_id = action_id or self._key_to_action.get(idempotency_key or "")
            if resolved_id is None:
                raise RobotBridgeError("action_not_found", "The physical action was not found.")
            receipt = self._receipts.get(resolved_id)
            if receipt is not None:
                return replace(
                    receipt,
                    metadata={**receipt.metadata, "cancel_replayed_terminal": True},
                )
            active = self._active
            if active is None or active.action_id != resolved_id:
                raise RobotBridgeError("action_not_found", "The physical action was not found.")
            active.cancel_event.set()
            result = active.result
        try:
            return await asyncio.wait_for(asyncio.shield(result), timeout_seconds)
        except TimeoutError:
            return PhysicalActionReceipt(
                action_id=active.action_id,
                idempotency_key=active.request.idempotency_key,
                capability="robot.cancel_action",
                robot_id=self.robot_id,
                environment=self.environment,
                status=PhysicalActionStatus.INDETERMINATE,
                request={
                    "action_id": active.action_id,
                    "idempotency_key": active.request.idempotency_key,
                },
                requested_at=utc_now(),
                completed_at=utc_now(),
                stop_confirmed=False,
                error_code="cancel_confirmation_timeout",
                error_message="Cancellation was requested but stop was not confirmed.",
                metadata={"backend": "simulator"},
            )

    async def close(self) -> None:
        async with self._lock:
            active = self._active
            if active is not None:
                active.cancel_event.set()
                result = active.result
            else:
                result = None
        if result is not None:
            try:
                await asyncio.wait_for(asyncio.shield(result), 2.0)
            except TimeoutError:
                pass
