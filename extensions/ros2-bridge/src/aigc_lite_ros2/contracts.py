"""Transport-neutral physical capability contracts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ExecutionEnvironment(StrEnum):
    SIMULATION = "simulation"
    HARDWARE = "hardware"


class PhysicalActionStatus(StrEnum):
    PROPOSED = "proposed"
    AUTHORIZED = "authorized"
    DISPATCHED = "dispatched"
    ACCEPTED = "accepted"
    EXECUTING = "executing"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INDETERMINATE = "indeterminate"

    @property
    def terminal(self) -> bool:
        return self in {
            self.SUCCEEDED,
            self.FAILED,
            self.CANCELLED,
            self.INDETERMINATE,
        }


class EffectClass(StrEnum):
    OBSERVATION = "observation"
    PHYSICAL_MOTION = "physical_motion"


class Reversibility(StrEnum):
    REVERSIBLE = "reversible"
    COMPENSATABLE = "compensatable"
    IRREVERSIBLE = "irreversible"


@dataclass(frozen=True, slots=True)
class Pose2D:
    x: float
    y: float
    yaw: float
    frame_id: str = "map"

    def as_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "yaw": self.yaw,
            "frame_id": self.frame_id,
        }


@dataclass(frozen=True, slots=True)
class RobotState:
    robot_id: str
    environment: ExecutionEnvironment
    observed_at: str
    pose: Pose2D | None
    navigation_status: str
    active_action_id: str | None = None
    emergency_stop_engaged: bool = False
    localized: bool = True
    map_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": "robot.state.v1",
            "robot_id": self.robot_id,
            "environment": self.environment.value,
            "observed_at": self.observed_at,
            "pose": self.pose.as_dict() if self.pose is not None else None,
            "navigation_status": self.navigation_status,
            "active_action_id": self.active_action_id,
            "emergency_stop_engaged": self.emergency_stop_engaged,
            "localized": self.localized,
            "map_id": self.map_id,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class NavigationRequest:
    idempotency_key: str
    goal: Pose2D
    action_timeout_seconds: float
    requested_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "idempotency_key": self.idempotency_key,
            "goal": self.goal.as_dict(),
            "action_timeout_seconds": self.action_timeout_seconds,
            "requested_at": self.requested_at,
        }


@dataclass(frozen=True, slots=True)
class ActionFeedback:
    observed_at: str
    phase: str
    progress: float | None = None
    distance_remaining: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "phase": self.phase,
            "progress": self.progress,
            "distance_remaining": self.distance_remaining,
        }


@dataclass(frozen=True, slots=True)
class PhysicalActionReceipt:
    action_id: str
    idempotency_key: str
    capability: str
    robot_id: str
    environment: ExecutionEnvironment
    status: PhysicalActionStatus
    request: dict[str, Any]
    requested_at: str
    started_at: str | None = None
    completed_at: str | None = None
    provider_action_id: str | None = None
    stop_confirmed: bool | None = None
    observation_before: dict[str, Any] | None = None
    observation_after: dict[str, Any] | None = None
    feedback: tuple[ActionFeedback, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_observations(
        self,
        before: RobotState | None,
        after: RobotState | None,
    ) -> PhysicalActionReceipt:
        return replace(
            self,
            observation_before=before.as_dict() if before else None,
            observation_after=after.as_dict() if after else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": "robot.action-receipt.v1",
            "action_id": self.action_id,
            "idempotency_key": self.idempotency_key,
            "capability": self.capability,
            "effect_class": EffectClass.PHYSICAL_MOTION.value,
            "robot_id": self.robot_id,
            "environment": self.environment.value,
            "status": self.status.value,
            "request": self.request,
            "requested_at": self.requested_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "provider_action_id": self.provider_action_id,
            "stop_confirmed": self.stop_confirmed,
            "observation_before": self.observation_before,
            "observation_after": self.observation_after,
            "feedback": [item.as_dict() for item in self.feedback],
            "error": (
                {"code": self.error_code, "message": self.error_message}
                if self.error_code
                else None
            ),
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class ObservationReceipt:
    capability: str
    robot_id: str
    environment: ExecutionEnvironment
    observed_at: str
    state: RobotState
    last_action: PhysicalActionReceipt | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": "robot.observation-receipt.v1",
            "capability": self.capability,
            "effect_class": EffectClass.OBSERVATION.value,
            "robot_id": self.robot_id,
            "environment": self.environment.value,
            "observed_at": self.observed_at,
            "state": self.state.as_dict(),
            "last_action": self.last_action.as_dict() if self.last_action else None,
        }


class RobotBridgeError(Exception):
    """Stable bridge error safe to return through MCP."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": "robot.error.v1",
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
            },
        }
