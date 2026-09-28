"""Application service that exposes a deliberately small robot capability surface."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

from .backend import RobotBackend
from .contracts import (
    NavigationRequest,
    ObservationReceipt,
    PhysicalActionStatus,
    Pose2D,
    RobotBridgeError,
    utc_now,
)


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    risk: str
    required_scopes: tuple[str, ...]
    timeout_seconds: float
    extensions: dict[str, Any]
    read_only: bool
    destructive: bool
    idempotent: bool

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "aigc-lite": {
                "risk": self.risk,
                "required_scopes": list(self.required_scopes),
                "timeout_seconds": self.timeout_seconds,
                "extensions": self.extensions,
            }
        }


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    payload: dict[str, Any]
    failed: bool = False


_NO_ARGUMENTS = {"type": "object", "properties": {}, "additionalProperties": False}


def _capability_extensions(
    *,
    effect_class: str,
    physical_risk: str,
    reversibility: str,
    required_robot_state: list[str],
    stop_strategy: str,
    receipt_contract: str,
) -> dict[str, Any]:
    return {
        "capability": {
            "domain": "robotics",
            "execution_class": "physical",
            "effect_class": effect_class,
            "physical_risk": physical_risk,
            "reversibility": reversibility,
            "required_robot_state": required_robot_state,
            "stop_strategy": stop_strategy,
            "emergency_stop": "out_of_band",
            "receipt_contract": receipt_contract,
        }
    }


CAPABILITIES = (
    CapabilitySpec(
        name="robot_get_state",
        description="Read the current bounded robot state projection. This does not command motion.",
        input_schema=_NO_ARGUMENTS,
        risk="low",
        required_scopes=(),
        timeout_seconds=10.0,
        extensions=_capability_extensions(
            effect_class="observation",
            physical_risk="low",
            reversibility="reversible",
            required_robot_state=[],
            stop_strategy="none",
            receipt_contract="robot.state.v1",
        ),
        read_only=True,
        destructive=False,
        idempotent=True,
    ),
    CapabilitySpec(
        name="robot_inspect",
        description="Capture current robot state plus the latest bounded physical action receipt.",
        input_schema=_NO_ARGUMENTS,
        risk="low",
        required_scopes=(),
        timeout_seconds=10.0,
        extensions=_capability_extensions(
            effect_class="observation",
            physical_risk="low",
            reversibility="reversible",
            required_robot_state=[],
            stop_strategy="none",
            receipt_contract="robot.observation-receipt.v1",
        ),
        read_only=True,
        destructive=False,
        idempotent=True,
    ),
    CapabilitySpec(
        name="robot_navigate_to",
        description=(
            "Command one bounded Nav2 navigation action. Requires a caller-generated "
            "idempotency key and produces a physical action receipt."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 128},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "yaw": {"type": "number"},
                "frame_id": {"type": "string", "default": "map", "maxLength": 128},
                "action_timeout_seconds": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 120,
                    "default": 60,
                },
            },
            "required": ["idempotency_key", "x", "y", "yaw"],
            "additionalProperties": False,
        },
        risk="high",
        required_scopes=("robot:motion",),
        timeout_seconds=150.0,
        extensions=_capability_extensions(
            effect_class="physical_motion",
            physical_risk="high",
            reversibility="compensatable",
            required_robot_state=["localized", "not_emergency_stopped", "navigation_idle"],
            stop_strategy="ros2_action_cancel",
            receipt_contract="robot.action-receipt.v1",
        ),
        read_only=False,
        destructive=True,
        idempotent=True,
    ),
    CapabilitySpec(
        name="robot_cancel_action",
        description=(
            "Request cancellation of one known robot action and report whether stop was "
            "confirmed. This is not an emergency-stop channel."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 128},
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": 20,
                    "default": 10,
                },
            },
            "anyOf": [{"required": ["action_id"]}, {"required": ["idempotency_key"]}],
            "additionalProperties": False,
        },
        risk="high",
        required_scopes=("robot:control",),
        timeout_seconds=25.0,
        extensions=_capability_extensions(
            effect_class="physical_motion",
            physical_risk="high",
            reversibility="compensatable",
            required_robot_state=["action_known"],
            stop_strategy="ros2_action_cancel",
            receipt_contract="robot.action-receipt.v1",
        ),
        read_only=False,
        destructive=False,
        idempotent=True,
    ),
)

CAPABILITY_BY_NAME = {item.name: item for item in CAPABILITIES}


def _text(value: Any, name: str, *, minimum: int = 1, maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise RobotBridgeError("invalid_arguments", f"{name} must be a string.")
    result = value.strip()
    if not minimum <= len(result) <= maximum:
        raise RobotBridgeError(
            "invalid_arguments",
            f"{name} must contain between {minimum} and {maximum} characters.",
        )
    return result


def _number(
    value: Any,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RobotBridgeError("invalid_arguments", f"{name} must be a number.")
    result = float(value)
    if not isfinite(result):
        raise RobotBridgeError("invalid_arguments", f"{name} must be finite.")
    if minimum is not None and result < minimum:
        raise RobotBridgeError("invalid_arguments", f"{name} is below the minimum.")
    if maximum is not None and result > maximum:
        raise RobotBridgeError("invalid_arguments", f"{name} exceeds the maximum.")
    return result


def _only(arguments: dict[str, Any], allowed: set[str]) -> None:
    unexpected = sorted(set(arguments) - allowed)
    if unexpected:
        raise RobotBridgeError(
            "invalid_arguments",
            "Unknown arguments were provided.",
            details={"unknown": unexpected},
        )


class RobotCapabilityService:
    def __init__(self, backend: RobotBackend) -> None:
        self.backend = backend

    async def invoke(self, name: str, arguments: dict[str, Any] | None) -> CapabilityResult:
        arguments = arguments or {}
        if not isinstance(arguments, dict):
            return CapabilityResult(
                RobotBridgeError(
                    "invalid_arguments", "Tool arguments must be an object."
                ).as_dict(),
                failed=True,
            )
        try:
            if name == "robot_get_state":
                _only(arguments, set())
                return CapabilityResult((await self.backend.get_state()).as_dict())
            if name == "robot_inspect":
                _only(arguments, set())
                state = await self.backend.get_state()
                receipt = ObservationReceipt(
                    capability="robot.inspect",
                    robot_id=state.robot_id,
                    environment=state.environment,
                    observed_at=utc_now(),
                    state=state,
                    last_action=await self.backend.latest_receipt(),
                )
                return CapabilityResult(receipt.as_dict())
            if name == "robot_navigate_to":
                return await self._navigate(arguments)
            if name == "robot_cancel_action":
                return await self._cancel(arguments)
            raise RobotBridgeError("capability_not_found", "The capability was not found.")
        except RobotBridgeError as exc:
            return CapabilityResult(exc.as_dict(), failed=True)

    async def _navigate(self, arguments: dict[str, Any]) -> CapabilityResult:
        _only(
            arguments,
            {"idempotency_key", "x", "y", "yaw", "frame_id", "action_timeout_seconds"},
        )
        request = NavigationRequest(
            idempotency_key=_text(
                arguments.get("idempotency_key"),
                "idempotency_key",
                minimum=8,
            ),
            goal=Pose2D(
                x=_number(arguments.get("x"), "x"),
                y=_number(arguments.get("y"), "y"),
                yaw=_number(arguments.get("yaw"), "yaw"),
                frame_id=_text(arguments.get("frame_id", "map"), "frame_id"),
            ),
            action_timeout_seconds=_number(
                arguments.get("action_timeout_seconds", 60),
                "action_timeout_seconds",
                minimum=1,
                maximum=120,
            ),
        )
        receipt = await self.backend.navigate_to(request)
        return CapabilityResult(
            receipt.as_dict(),
            failed=receipt.status is not PhysicalActionStatus.SUCCEEDED,
        )

    async def _cancel(self, arguments: dict[str, Any]) -> CapabilityResult:
        _only(arguments, {"action_id", "idempotency_key", "timeout_seconds"})
        action_id = arguments.get("action_id")
        idempotency_key = arguments.get("idempotency_key")
        if action_id is not None:
            action_id = _text(action_id, "action_id")
        if idempotency_key is not None:
            idempotency_key = _text(idempotency_key, "idempotency_key", minimum=8)
        if action_id is None and idempotency_key is None:
            raise RobotBridgeError(
                "invalid_cancel_target", "action_id or idempotency_key is required."
            )
        receipt = await self.backend.cancel_action(
            action_id=action_id,
            idempotency_key=idempotency_key,
            timeout_seconds=_number(
                arguments.get("timeout_seconds", 10),
                "timeout_seconds",
                minimum=0.1,
                maximum=20,
            ),
        )
        return CapabilityResult(
            receipt.as_dict(),
            failed=receipt.status
            in {PhysicalActionStatus.FAILED, PhysicalActionStatus.INDETERMINATE},
        )
