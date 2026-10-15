"""Persistence port for one model request and its accounting observation."""

from __future__ import annotations

from typing import Any, Protocol


class ModelUsageRepository(Protocol):
    def begin_model_usage(self, values: dict[str, Any]) -> dict[str, Any]: ...

    def finish_model_usage(
        self, tenant_id: str, attempt_id: str, values: dict[str, Any]
    ) -> dict[str, Any]: ...

    def get_model_usage(self, tenant_id: str, attempt_id: str) -> dict[str, Any] | None: ...

    def list_model_usage(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]: ...
