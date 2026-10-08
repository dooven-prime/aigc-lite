"""Environment configuration for the standalone bridge process."""

from __future__ import annotations

import os
from dataclasses import dataclass
from math import isfinite

from .contracts import ExecutionEnvironment


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if not isfinite(value) or value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class BridgeSettings:
    backend: str = "simulator"
    host: str = "127.0.0.1"
    port: int = 9010
    robot_id: str = "robot-1"
    environment: ExecutionEnvironment = ExecutionEnvironment.SIMULATION
    map_id: str = "default-map"
    base_frame: str = "base_link"
    global_frame: str = "map"
    nav2_action_name: str = "navigate_to_pose"
    sim_travel_seconds: float = 0.2
    sim_outcome: str = "success"
    api_key: str = ""
    allowed_hosts: tuple[str, ...] = (
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
    )
    allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> BridgeSettings:
        backend = os.getenv("AIGC_LITE_ROS2_BACKEND", "simulator").strip().lower()
        if backend not in {"simulator", "nav2"}:
            raise RuntimeError("AIGC_LITE_ROS2_BACKEND must be simulator or nav2")
        outcome = os.getenv("AIGC_LITE_ROS2_SIM_OUTCOME", "success").strip().lower()
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
            raise RuntimeError("AIGC_LITE_ROS2_SIM_OUTCOME is not a supported simulator fault")
        environment_value = (
            os.getenv(
                "AIGC_LITE_ROS2_ENVIRONMENT",
                "simulation" if backend == "simulator" else "hardware",
            )
            .strip()
            .lower()
        )
        try:
            environment = ExecutionEnvironment(environment_value)
        except ValueError as exc:
            raise RuntimeError("AIGC_LITE_ROS2_ENVIRONMENT must be simulation or hardware") from exc
        try:
            port = int(os.getenv("AIGC_LITE_ROS2_PORT", "9010"))
        except ValueError as exc:
            raise RuntimeError("AIGC_LITE_ROS2_PORT must be an integer") from exc
        if not 1 <= port <= 65_535:
            raise RuntimeError("AIGC_LITE_ROS2_PORT must be between 1 and 65535")
        host = os.getenv("AIGC_LITE_ROS2_HOST", "127.0.0.1")
        api_key = os.getenv("AIGC_LITE_ROS2_API_KEY", "")
        if api_key and len(api_key) < 16:
            raise RuntimeError("AIGC_LITE_ROS2_API_KEY must contain at least 16 characters")
        if backend == "simulator" and environment is not ExecutionEnvironment.SIMULATION:
            raise RuntimeError("Simulator backend must use AIGC_LITE_ROS2_ENVIRONMENT=simulation")
        if (backend == "nav2" or host not in {"127.0.0.1", "localhost", "::1"}) and not api_key:
            raise RuntimeError(
                "AIGC_LITE_ROS2_API_KEY is required for Nav2 or non-loopback binding"
            )
        return cls(
            backend=backend,
            host=host,
            port=port,
            robot_id=os.getenv("AIGC_LITE_ROS2_ROBOT_ID", "robot-1"),
            environment=environment,
            map_id=os.getenv("AIGC_LITE_ROS2_MAP_ID", "default-map"),
            base_frame=os.getenv("AIGC_LITE_ROS2_BASE_FRAME", "base_link"),
            global_frame=os.getenv("AIGC_LITE_ROS2_GLOBAL_FRAME", "map"),
            nav2_action_name=os.getenv("AIGC_LITE_ROS2_NAV2_ACTION", "navigate_to_pose"),
            sim_travel_seconds=_float_env("AIGC_LITE_ROS2_SIM_TRAVEL_SECONDS", 0.2),
            sim_outcome=outcome,
            api_key=api_key,
            allowed_hosts=tuple(
                item.strip()
                for item in os.getenv(
                    "AIGC_LITE_ROS2_ALLOWED_HOSTS",
                    "127.0.0.1:*,localhost:*,[::1]:*",
                ).split(",")
                if item.strip()
            ),
            allowed_origins=tuple(
                item.strip()
                for item in os.getenv("AIGC_LITE_ROS2_ALLOWED_ORIGINS", "").split(",")
                if item.strip()
            ),
        )
