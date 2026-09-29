"""Smoke the standalone ROS 2 bridge wheel with its deterministic simulator."""

from __future__ import annotations

import asyncio

from aigc_lite_ros2.service import RobotCapabilityService
from aigc_lite_ros2.simulator import SimulatorBackend


async def _run() -> None:
    service = RobotCapabilityService(SimulatorBackend(travel_seconds=0.01))
    state = await service.invoke("robot_get_state", {})
    assert not state.failed
    assert state.payload["environment"] == "simulation"
    receipt = await service.invoke(
        "robot_navigate_to",
        {
            "idempotency_key": "wheel-smoke-navigation",
            "x": 0.25,
            "y": 0,
            "yaw": 0,
            "action_timeout_seconds": 2,
        },
    )
    assert not receipt.failed
    assert receipt.payload["status"] == "succeeded"
    assert receipt.payload["stop_confirmed"] is True


if __name__ == "__main__":
    asyncio.run(_run())
