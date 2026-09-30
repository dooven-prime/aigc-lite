"""Lazy ROS 2/Nav2 adapter.

The module itself is importable without ROS. Constructing ``Nav2Backend``
requires the ROS 2 Python packages supplied by a sourced ROS installation.
"""

from __future__ import annotations

import asyncio
import importlib
import math
import threading
from dataclasses import dataclass, field, replace
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


def _load_ros() -> dict[str, Any]:
    try:
        rclpy = importlib.import_module("rclpy")
        action = importlib.import_module("rclpy.action")
        duration = importlib.import_module("rclpy.duration")
        executors = importlib.import_module("rclpy.executors")
        time_module = importlib.import_module("rclpy.time")
        goal_status = importlib.import_module("action_msgs.msg")
        geometry = importlib.import_module("geometry_msgs.msg")
        nav2 = importlib.import_module("nav2_msgs.action")
        tf2 = importlib.import_module("tf2_ros")
    except ImportError as exc:
        raise RuntimeError(
            "Nav2 backend requires a sourced ROS 2 installation with rclpy, "
            "nav2_msgs, geometry_msgs, action_msgs, and tf2_ros."
        ) from exc
    return {
        "rclpy": rclpy,
        "ActionClient": action.ActionClient,
        "Duration": duration.Duration,
        "MultiThreadedExecutor": executors.MultiThreadedExecutor,
        "Time": time_module.Time,
        "GoalStatus": goal_status.GoalStatus,
        "PoseStamped": geometry.PoseStamped,
        "NavigateToPose": nav2.NavigateToPose,
        "Buffer": tf2.Buffer,
        "TransformListener": tf2.TransformListener,
    }


async def _await_ros_future(future: Any, timeout: float | None = None) -> Any:
    loop = asyncio.get_running_loop()
    projected = loop.create_future()

    def done(completed: Any) -> None:
        try:
            value = completed.result()
        except Exception as exc:  # noqa: BLE001 - crosses the ROS callback boundary
            loop.call_soon_threadsafe(_set_exception, exc)
        else:
            loop.call_soon_threadsafe(_set_result, value)

    def _set_result(value: Any) -> None:
        if not projected.done():
            projected.set_result(value)

    def _set_exception(exc: Exception) -> None:
        if not projected.done():
            projected.set_exception(exc)

    future.add_done_callback(done)
    if timeout is None:
        return await asyncio.shield(projected)
    return await asyncio.wait_for(asyncio.shield(projected), timeout)


@dataclass(slots=True)
class _Nav2Action:
    action_id: str
    request: NavigationRequest
    before: RobotState
    goal_handle: Any
    result_future: Any
    provider_action_id: str
    started_at: str
    feedback: list[ActionFeedback] = field(default_factory=list)


@dataclass(slots=True)
class _PendingNavigation:
    action_id: str
    request: NavigationRequest
    before: RobotState
    goal_future: Any
    started_at: str
    feedback: list[ActionFeedback] = field(default_factory=list)


class Nav2Backend:
    """Translate bounded bridge operations to Nav2 Action and TF2 state."""

    def __init__(
        self,
        *,
        robot_id: str,
        environment: ExecutionEnvironment,
        map_id: str,
        base_frame: str = "base_link",
        global_frame: str = "map",
        action_name: str = "navigate_to_pose",
    ) -> None:
        self._ros = _load_ros()
        self.robot_id = robot_id
        self.environment = environment
        self.map_id = map_id
        self.base_frame = base_frame
        self.global_frame = global_frame
        self.action_name = action_name
        rclpy = self._ros["rclpy"]
        self._owns_context = not rclpy.ok()
        if self._owns_context:
            rclpy.init(args=None)
        self._node = rclpy.create_node(f"aigc_lite_bridge_{uuid4().hex[:8]}")
        self._executor = self._ros["MultiThreadedExecutor"](num_threads=2)
        self._executor.add_node(self._node)
        self._spin_thread = threading.Thread(
            target=self._executor.spin,
            name=f"aigc-lite-ros2-{robot_id}",
            daemon=True,
        )
        self._tf_buffer = self._ros["Buffer"]()
        self._tf_listener = self._ros["TransformListener"](
            self._tf_buffer, self._node, spin_thread=False
        )
        self._action_client = self._ros["ActionClient"](
            self._node, self._ros["NavigateToPose"], action_name
        )
        self._spin_thread.start()
        self._active: _Nav2Action | None = None
        self._pending: _PendingNavigation | None = None
        self._dispatching = False
        self._reconciliation_tasks: dict[str, asyncio.Task[None]] = {}
        self._receipts: dict[str, PhysicalActionReceipt] = {}
        self._key_to_action: dict[str, str] = {}
        self._latest: PhysicalActionReceipt | None = None
        self._lock = asyncio.Lock()
        self._feedback_lock = threading.Lock()

    async def get_state(self) -> RobotState:
        try:
            transform = await asyncio.to_thread(
                self._tf_buffer.lookup_transform,
                self.global_frame,
                self.base_frame,
                self._ros["Time"](),
                self._ros["Duration"](seconds=1.0),
            )
        except Exception as exc:  # noqa: BLE001 - normalize TF implementation errors
            raise RobotBridgeError(
                "robot_state_unavailable",
                "TF2 could not resolve the robot pose.",
                retryable=True,
                details={
                    "global_frame": self.global_frame,
                    "base_frame": self.base_frame,
                },
            ) from exc
        rotation = transform.transform.rotation
        yaw = math.atan2(
            2.0 * (rotation.w * rotation.z + rotation.x * rotation.y),
            1.0 - 2.0 * (rotation.y * rotation.y + rotation.z * rotation.z),
        )
        translation = transform.transform.translation
        async with self._lock:
            active = self._active
            dispatching = self._dispatching
        return RobotState(
            robot_id=self.robot_id,
            environment=self.environment,
            observed_at=utc_now(),
            pose=Pose2D(
                x=float(translation.x),
                y=float(translation.y),
                yaw=float(yaw),
                frame_id=self.global_frame,
            ),
            navigation_status=("dispatching" if dispatching else "executing" if active else "idle"),
            active_action_id=active.action_id if active else None,
            emergency_stop_engaged=False,
            localized=True,
            map_id=self.map_id,
            metadata={
                "backend": "nav2",
                "base_frame": self.base_frame,
                "global_frame": self.global_frame,
                "tf_stamp": {
                    "sec": transform.header.stamp.sec,
                    "nanosec": transform.header.stamp.nanosec,
                },
            },
        )

    async def latest_receipt(self) -> PhysicalActionReceipt | None:
        async with self._lock:
            return self._latest

    async def navigate_to(self, request: NavigationRequest) -> PhysicalActionReceipt:
        replay_action: _Nav2Action | None = None
        async with self._lock:
            known_id = self._key_to_action.get(request.idempotency_key)
            if known_id is not None:
                known = self._receipts.get(known_id)
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
                if self._active is not None and self._active.action_id == known_id:
                    if not self._same_intent(self._active.request.as_dict(), request):
                        raise RobotBridgeError(
                            "idempotency_conflict",
                            "The idempotency key is already bound to a different goal.",
                        )
                    replay_action = self._active
            elif self._active is not None or self._dispatching:
                raise RobotBridgeError(
                    "robot_busy",
                    "Another Nav2 action is active or being dispatched.",
                    retryable=True,
                )
            else:
                self._dispatching = True

        if replay_action is not None:
            receipt = await self._wait_for_action(replay_action)
            return replace(
                receipt,
                metadata={**receipt.metadata, "idempotent_replay": True},
            )

        try:
            before = await self.get_state()
        except BaseException:
            async with self._lock:
                self._dispatching = False
            raise
        try:
            available = await asyncio.to_thread(
                self._action_client.wait_for_server, timeout_sec=5.0
            )
        except BaseException:
            async with self._lock:
                self._dispatching = False
            raise
        if not available:
            async with self._lock:
                self._dispatching = False
            raise RobotBridgeError(
                "nav2_action_unavailable",
                "The Nav2 NavigateToPose action server is unavailable.",
                retryable=True,
            )
        goal = self._ros["NavigateToPose"].Goal()
        pose = self._ros["PoseStamped"]()
        pose.header.frame_id = request.goal.frame_id
        pose.header.stamp = self._node.get_clock().now().to_msg()
        pose.pose.position.x = request.goal.x
        pose.pose.position.y = request.goal.y
        pose.pose.orientation.z = math.sin(request.goal.yaw / 2.0)
        pose.pose.orientation.w = math.cos(request.goal.yaw / 2.0)
        goal.pose = pose
        feedback_values: list[ActionFeedback] = []

        def on_feedback(message: Any) -> None:
            feedback = message.feedback
            value = ActionFeedback(
                observed_at=utc_now(),
                phase="executing",
                distance_remaining=(
                    float(feedback.distance_remaining)
                    if hasattr(feedback, "distance_remaining")
                    else None
                ),
            )
            with self._feedback_lock:
                feedback_values.append(value)
                del feedback_values[:-32]

        try:
            goal_future = self._action_client.send_goal_async(
                goal, feedback_callback=on_feedback
            )
        except BaseException:
            async with self._lock:
                self._dispatching = False
            raise
        pending = _PendingNavigation(
            action_id=str(uuid4()),
            request=request,
            before=before,
            goal_future=goal_future,
            started_at=utc_now(),
            feedback=feedback_values,
        )
        async with self._lock:
            self._pending = pending
            self._key_to_action[request.idempotency_key] = pending.action_id
        try:
            goal_handle = await _await_ros_future(goal_future, timeout=10.0)
            promoted = await self._promote_pending(pending, goal_handle)
        except TimeoutError as exc:
            self._track_reconciliation(
                f"dispatch:{pending.action_id}",
                self._reconcile_late_dispatch(pending),
            )
            raise RobotBridgeError(
                "nav2_goal_dispatch_indeterminate",
                "Nav2 did not confirm whether the navigation goal was accepted; "
                "new motion is blocked while the bridge reconciles and cancels any late acceptance.",
                retryable=False,
                details={"action_id": pending.action_id},
            ) from exc
        except asyncio.CancelledError:
            self._track_reconciliation(
                f"dispatch:{pending.action_id}",
                self._reconcile_late_dispatch(pending),
            )
            raise
        except Exception as exc:
            raise RobotBridgeError(
                "nav2_goal_dispatch_indeterminate",
                "Nav2 did not provide a usable goal handle; new motion remains blocked "
                "until the bridge is reconciled or restarted.",
                retryable=False,
                details={"action_id": pending.action_id},
            ) from exc
        if isinstance(promoted, PhysicalActionReceipt):
            return promoted
        active = promoted
        try:
            return await asyncio.wait_for(
                self._wait_for_action(active), request.action_timeout_seconds
            )
        except TimeoutError:
            return await self._cancel_active(
                active,
                error_code="action_timeout",
                error_message="Navigation timed out and cancellation was requested.",
                timeout_seconds=5.0,
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._cancel_active(
                    active,
                    error_code="caller_cancelled",
                    error_message="The MCP caller cancelled the action wait.",
                    timeout_seconds=5.0,
                )
            )
            raise

    async def _promote_pending(
        self,
        pending: _PendingNavigation,
        goal_handle: Any,
    ) -> _Nav2Action | PhysicalActionReceipt:
        if not goal_handle.accepted:
            await self._release_pending(pending)
            return await self._standalone_receipt(
                pending.request,
                pending.before,
                action_id=pending.action_id,
                status=PhysicalActionStatus.FAILED,
                stop_confirmed=True,
                error_code="nav2_goal_rejected",
                error_message="Nav2 rejected the navigation goal.",
            )
        active = _Nav2Action(
            action_id=pending.action_id,
            request=pending.request,
            before=pending.before,
            goal_handle=goal_handle,
            result_future=goal_handle.get_result_async(),
            provider_action_id=bytes(goal_handle.goal_id.uuid).hex(),
            started_at=pending.started_at,
            feedback=pending.feedback,
        )
        async with self._lock:
            if self._pending is not pending:
                raise RobotBridgeError(
                    "nav2_dispatch_reconciliation_conflict",
                    "The pending Nav2 goal no longer owns the dispatch slot.",
                )
            self._pending = None
            self._dispatching = False
            self._active = active
        return active

    async def _release_pending(self, pending: _PendingNavigation) -> None:
        async with self._lock:
            if self._pending is pending:
                self._pending = None
                self._dispatching = False

    async def _reconcile_late_dispatch(self, pending: _PendingNavigation) -> None:
        """Own a submitted goal until a late acceptance can be cancelled safely."""

        try:
            goal_handle = await _await_ros_future(pending.goal_future)
            promoted = await self._promote_pending(pending, goal_handle)
        except Exception:  # noqa: BLE001 - remain blocked without a usable goal handle
            return
        if isinstance(promoted, PhysicalActionReceipt):
            return
        await self._cancel_active(
            promoted,
            error_code="dispatch_wait_aborted",
            error_message=(
                "The dispatch wait ended before Nav2 accepted the goal; "
                "late acceptance was cancelled."
            ),
            timeout_seconds=5.0,
        )

    def _track_reconciliation(self, key: str, coroutine: Any) -> None:
        existing = self._reconciliation_tasks.get(key)
        if existing is not None and not existing.done():
            coroutine.close()
            return
        task = asyncio.create_task(coroutine)
        self._reconciliation_tasks[key] = task

        def finished(completed: asyncio.Task[None]) -> None:
            if self._reconciliation_tasks.get(key) is completed:
                self._reconciliation_tasks.pop(key, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(finished)

    @staticmethod
    def _same_intent(existing: dict[str, Any], request: NavigationRequest) -> bool:
        return (
            existing.get("goal") == request.goal.as_dict()
            and existing.get("action_timeout_seconds") == request.action_timeout_seconds
        )

    async def _wait_for_action(self, active: _Nav2Action) -> PhysicalActionReceipt:
        result = await _await_ros_future(active.result_future)
        goal_status = self._ros["GoalStatus"]
        if result.status == goal_status.STATUS_SUCCEEDED:
            status = PhysicalActionStatus.SUCCEEDED
            error_code = None
            error_message = None
            stop_confirmed = True
        elif result.status == goal_status.STATUS_CANCELED:
            status = PhysicalActionStatus.CANCELLED
            error_code = "action_cancelled"
            error_message = "Nav2 confirmed action cancellation."
            stop_confirmed = True
        elif result.status == goal_status.STATUS_ABORTED:
            status = PhysicalActionStatus.FAILED
            error_code = "navigation_aborted"
            error_message = "Nav2 aborted the navigation action."
            stop_confirmed = True
        else:
            status = PhysicalActionStatus.INDETERMINATE
            error_code = "unknown_nav2_terminal_status"
            error_message = "Nav2 returned an unknown terminal status."
            stop_confirmed = False
        return await self._finish(
            active,
            status=status,
            stop_confirmed=stop_confirmed,
            error_code=error_code,
            error_message=error_message,
            nav2_status=int(result.status),
        )

    async def _cancel_active(
        self,
        active: _Nav2Action,
        *,
        error_code: str,
        error_message: str,
        timeout_seconds: float,
    ) -> PhysicalActionReceipt:
        try:
            await _await_ros_future(active.goal_handle.cancel_goal_async(), timeout=timeout_seconds)
            return await asyncio.wait_for(self._wait_for_action(active), timeout_seconds)
        except TimeoutError:
            receipt = await self._finish(
                active,
                status=PhysicalActionStatus.INDETERMINATE,
                stop_confirmed=False,
                error_code="cancel_confirmation_timeout",
                error_message=(f"{error_message} Stop could not be confirmed before the deadline."),
            )
            self._ensure_action_reconciliation(active)
            return receipt
        except Exception as exc:  # noqa: BLE001 - preserve uncertainty on ROS errors
            receipt = await self._finish(
                active,
                status=PhysicalActionStatus.INDETERMINATE,
                stop_confirmed=False,
                error_code=error_code,
                error_message=f"{error_message} ROS cancellation failed: {type(exc).__name__}.",
            )
            self._ensure_action_reconciliation(active)
            return receipt

    def _ensure_action_reconciliation(self, active: _Nav2Action) -> None:
        self._track_reconciliation(
            active.action_id, self._reconcile_active_result(active)
        )

    async def _reconcile_active_result(self, active: _Nav2Action) -> None:
        try:
            await self._wait_for_action(active)
        except Exception:  # noqa: BLE001 - remain blocked when terminal state is unknown
            return

    async def _finish(
        self,
        active: _Nav2Action,
        *,
        status: PhysicalActionStatus,
        stop_confirmed: bool,
        error_code: str | None,
        error_message: str | None,
        nav2_status: int | None = None,
    ) -> PhysicalActionReceipt:
        try:
            after = await self.get_state()
        except RobotBridgeError:
            after = None
        if after is not None:
            after = replace(
                after,
                navigation_status=(
                    "idle"
                    if status
                    in {
                        PhysicalActionStatus.SUCCEEDED,
                        PhysicalActionStatus.CANCELLED,
                    }
                    else "failed"
                    if stop_confirmed
                    else "unknown"
                ),
                active_action_id=None if stop_confirmed else active.action_id,
            )
        with self._feedback_lock:
            feedback = tuple(active.feedback[-32:])
        receipt = PhysicalActionReceipt(
            action_id=active.action_id,
            idempotency_key=active.request.idempotency_key,
            capability="robot.navigate_to",
            robot_id=self.robot_id,
            environment=self.environment,
            status=status,
            request=active.request.as_dict(),
            requested_at=active.request.requested_at,
            started_at=active.started_at,
            completed_at=utc_now(),
            provider_action_id=active.provider_action_id,
            stop_confirmed=stop_confirmed,
            observation_before=active.before.as_dict(),
            observation_after=after.as_dict() if after else None,
            feedback=feedback,
            error_code=error_code,
            error_message=error_message,
            metadata={
                "backend": "nav2",
                "action_name": self.action_name,
                "map_id": self.map_id,
                "nav2_status": nav2_status,
            },
        )
        async with self._lock:
            existing = self._receipts.get(active.action_id)
            if existing is not None and (existing.stop_confirmed or not stop_confirmed):
                return existing
            if self._active is active and stop_confirmed:
                self._active = None
            self._receipts[active.action_id] = receipt
            self._latest = receipt
        return receipt

    async def _standalone_receipt(
        self,
        request: NavigationRequest,
        before: RobotState,
        *,
        action_id: str | None = None,
        status: PhysicalActionStatus,
        stop_confirmed: bool,
        error_code: str,
        error_message: str,
    ) -> PhysicalActionReceipt:
        receipt = PhysicalActionReceipt(
            action_id=action_id or str(uuid4()),
            idempotency_key=request.idempotency_key,
            capability="robot.navigate_to",
            robot_id=self.robot_id,
            environment=self.environment,
            status=status,
            request=request.as_dict(),
            requested_at=request.requested_at,
            started_at=utc_now(),
            completed_at=utc_now(),
            stop_confirmed=stop_confirmed,
            observation_before=before.as_dict(),
            observation_after=before.as_dict(),
            error_code=error_code,
            error_message=error_message,
            metadata={"backend": "nav2", "action_name": self.action_name},
        )
        async with self._lock:
            self._key_to_action[request.idempotency_key] = receipt.action_id
            self._receipts[receipt.action_id] = receipt
            self._latest = receipt
        return receipt

    async def cancel_action(
        self,
        *,
        action_id: str | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> PhysicalActionReceipt:
        if not action_id and not idempotency_key:
            raise RobotBridgeError(
                "invalid_cancel_target", "action_id or idempotency_key is required."
            )
        async with self._lock:
            resolved_id = action_id or self._key_to_action.get(idempotency_key or "")
            if resolved_id is None:
                raise RobotBridgeError("action_not_found", "The physical action was not found.")
            active = self._active
            if active is not None and active.action_id == resolved_id:
                receipt = None
            else:
                receipt = self._receipts.get(resolved_id)
            if receipt is not None:
                return replace(
                    receipt,
                    metadata={**receipt.metadata, "cancel_replayed_terminal": True},
                )
            if active is None or active.action_id != resolved_id:
                if self._pending is not None and self._pending.action_id == resolved_id:
                    raise RobotBridgeError(
                        "action_dispatch_pending",
                        "The goal response is pending; late acceptance is already under cancellation control.",
                        retryable=True,
                    )
                raise RobotBridgeError("action_not_found", "The physical action was not found.")
        return await self._cancel_active(
            active,
            error_code="action_cancelled",
            error_message="Cancellation was explicitly requested.",
            timeout_seconds=timeout_seconds,
        )

    async def close(self) -> None:
        async with self._lock:
            active = self._active
        if active is not None:
            await self._cancel_active(
                active,
                error_code="bridge_shutdown",
                error_message="The bridge is shutting down.",
                timeout_seconds=5.0,
            )
        tasks = list(self._reconciliation_tasks.values())
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=5.0)
            for task in pending:
                task.cancel()
        self._executor.shutdown(timeout_sec=5.0)
        self._node.destroy_node()
        if self._owns_context and self._ros["rclpy"].ok():
            self._ros["rclpy"].shutdown()
        if self._spin_thread.is_alive():
            self._spin_thread.join(timeout=5.0)
