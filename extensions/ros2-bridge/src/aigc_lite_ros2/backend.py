"""Port implemented by simulator and ROS 2/Nav2 adapters."""

from __future__ import annotations

from typing import Protocol

from .contracts import NavigationRequest, PhysicalActionReceipt, RobotState


class RobotBackend(Protocol):
    async def get_state(self) -> RobotState: ...

    async def navigate_to(self, request: NavigationRequest) -> PhysicalActionReceipt: ...

    async def cancel_action(
        self,
        *,
        action_id: str | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> PhysicalActionReceipt: ...

    async def latest_receipt(self) -> PhysicalActionReceipt | None: ...

    async def close(self) -> None: ...
