"""Readiness guard for a dedicated, headless Nav2/Gazebo simulation graph.

This is evaluation infrastructure, not a proof that a ROS domain contains no
hardware. Operators must launch the documented simulation in an isolated domain.
"""

from __future__ import annotations

import asyncio
import math
import threading
from time import monotonic
from typing import Any
from uuid import uuid4

from .contracts import Pose2D, RobotBridgeError
from .nav2 import Nav2Backend, _await_ros_future


class SimulationGraphProbe:
    """Check live ROS graph identity before a test can command motion."""

    def __init__(self, backend: Nav2Backend) -> None:
        self.backend = backend
        ros = backend._ros  # The probe shares the adapter's initialized ROS context.
        self._node = ros["rclpy"].create_node(f"aigc_lite_nav2_probe_{uuid4().hex[:8]}")
        self._executor = ros["MultiThreadedExecutor"](num_threads=2)
        self._executor.add_node(self._node)
        self._spin_thread = threading.Thread(
            target=self._executor.spin, name="aigc-lite-nav2-probe", daemon=True
        )
        from geometry_msgs.msg import PoseWithCovarianceStamped
        from lifecycle_msgs.srv import GetState
        from rcl_interfaces.srv import GetParameters
        from rclpy.qos import QoSProfile, ReliabilityPolicy
        from rosgraph_msgs.msg import Clock

        self._pose_type = PoseWithCovarianceStamped
        self._state_type = GetState
        self._parameters_type = GetParameters
        self._clock: tuple[int, int] | None = None
        self._first_clock: tuple[int, int] | None = None
        self._clock_count = 0
        self._clients: dict[str, Any] = {}
        self._node.create_subscription(
            Clock,
            "/clock",
            self._on_clock,
            QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT),
        )
        self._pose_publisher = self._node.create_publisher(
            PoseWithCovarianceStamped, "/initialpose", 10
        )
        self._spin_thread.start()

    def _on_clock(self, message: Any) -> None:
        self._clock = (message.clock.sec, message.clock.nanosec)
        if self._first_clock is None:
            self._first_clock = self._clock
        self._clock_count += 1

    async def _call(self, service_name: str, service_type: Any, request: Any) -> Any:
        client = self._clients.get(service_name)
        if client is None:
            client = self._node.create_client(service_type, service_name)
            self._clients[service_name] = client
        if not await asyncio.to_thread(client.wait_for_service, timeout_sec=2.0):
            raise RuntimeError(f"ROS service unavailable: {service_name}")
        future = client.call_async(request)
        try:
            return await _await_ros_future(future, timeout=5.0)
        except BaseException:
            future.cancel()
            raise

    async def _state(self, node_name: str) -> str:
        response = await self._call(
            f"/{node_name}/get_state", self._state_type, self._state_type.Request()
        )
        return str(response.current_state.label)

    async def _sim_time(self, node_name: str) -> bool:
        response = await self._call(
            f"/{node_name}/get_parameters",
            self._parameters_type,
            self._parameters_type.Request(names=["use_sim_time"]),
        )
        return bool(response.values and response.values[0].bool_value)

    async def _wait_for_clock(self, timeout_seconds: float) -> None:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            if (
                self._clock is not None
                and self._first_clock is not None
                and self._clock > self._first_clock
            ):
                return
            await asyncio.sleep(0.2)
        raise RuntimeError("/clock did not advance in the simulation graph")

    def _publish_initial_pose(self, pose: Pose2D) -> None:
        message = self._pose_type()
        message.header.frame_id = pose.frame_id
        if self._clock is not None:
            message.header.stamp.sec, message.header.stamp.nanosec = self._clock
        message.pose.pose.position.x = pose.x
        message.pose.pose.position.y = pose.y
        message.pose.pose.orientation.z = math.sin(pose.yaw / 2.0)
        message.pose.pose.orientation.w = math.cos(pose.yaw / 2.0)
        message.pose.covariance[0] = 0.25
        message.pose.covariance[7] = 0.25
        message.pose.covariance[35] = 0.0685
        self._pose_publisher.publish(message)

    async def prepare(self, initial_pose: Pose2D, *, timeout_seconds: float = 60) -> dict:
        """Fail closed until Gazebo clock, AMCL, TF, and Nav2 are live."""

        await self._wait_for_clock(min(timeout_seconds, 20))
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            names = {
                f"{namespace.rstrip('/')}/{name}"
                for name, namespace in self._node.get_node_names_and_namespaces()
            }
            if "/ros_gz_bridge" in names and "/robot_state_publisher" in names:
                break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("Expected Gazebo bridge and robot state publisher are absent")
        for node_name in ("amcl", "bt_navigator", "robot_state_publisher"):
            while monotonic() < deadline:
                try:
                    sim_time = await self._sim_time(node_name)
                except (RuntimeError, TimeoutError):
                    await asyncio.sleep(0.5)
                    continue
                if not sim_time:
                    raise RuntimeError(f"/{node_name} is not using simulation time")
                break
            else:
                raise RuntimeError(f"/{node_name} parameter service did not become ready")
        while monotonic() < deadline:
            try:
                amcl_state = await self._state("amcl")
            except (RuntimeError, TimeoutError):
                amcl_state = "unavailable"
            if amcl_state == "active":
                break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("AMCL did not become active")

        observed = None
        while monotonic() < deadline:
            self._publish_initial_pose(initial_pose)
            await asyncio.sleep(1.0)
            try:
                observed = await self.backend.get_state()
            except RobotBridgeError:
                continue
            if observed.pose is not None:
                break
        if observed is None or observed.pose is None:
            raise RuntimeError("map-to-base_link TF stayed unavailable after initial pose")

        while monotonic() < deadline:
            try:
                navigator_state = await self._state("bt_navigator")
            except (RuntimeError, TimeoutError):
                navigator_state = "unavailable"
            if navigator_state == "active":
                break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("Nav2 navigator did not become active")
        if not await asyncio.to_thread(backend_action_ready, self.backend):
            raise RuntimeError("NavigateToPose action server is not ready")
        return {
            "graph_kind": "nav2_gazebo_simulation",
            "clock_ticks_observed": self._clock_count,
            "clock_stamp": self._clock,
            "amcl_state": amcl_state,
            "navigator_state": navigator_state,
            "use_sim_time": True,
            "initial_pose": initial_pose.as_dict(),
            "observed_pose": observed.pose.as_dict(),
        }

    def close(self) -> None:
        self._executor.shutdown(timeout_sec=5.0)
        self._node.destroy_node()
        if self._spin_thread.is_alive():
            self._spin_thread.join(timeout=5.0)


def backend_action_ready(backend: Nav2Backend) -> bool:
    return bool(backend._action_client.wait_for_server(timeout_sec=3.0))
