"""Fail-closed dependency readiness projection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..database import get_repository
from ..migrations import current_schema_head
from ..repository import Repository

RepositoryProvider = Callable[[], Repository]
BooleanProvider = Callable[[], bool]
SchemaHeadProvider = Callable[[], str]


class ReadinessService:
    """Report bounded operational checks without exposing exception details."""

    def __init__(
        self,
        *,
        repository_provider: RepositoryProvider = get_repository,
        schema_head_provider: SchemaHeadProvider = current_schema_head,
        scheduler_enabled: BooleanProvider,
        scheduler_running: BooleanProvider,
    ) -> None:
        self._repository_provider = repository_provider
        self._schema_head_provider = schema_head_provider
        self._scheduler_enabled = scheduler_enabled
        self._scheduler_running = scheduler_running

    def snapshot(self) -> tuple[bool, dict[str, Any]]:
        expected_revision = self._schema_head_provider()
        database_status = "ok"
        migration_status = "ok"
        current_revision: str | None = None
        try:
            current_revision = self._repository_provider().schema_revision()
            if current_revision != expected_revision:
                migration_status = "stale"
        except Exception:  # noqa: BLE001 - readiness exposes only stable categories
            database_status = "unavailable"
            migration_status = "unavailable"

        if not self._scheduler_enabled():
            scheduler_status = "disabled"
        elif self._scheduler_running():
            scheduler_status = "ok"
        else:
            scheduler_status = "unavailable"

        ready = (
            database_status == "ok"
            and migration_status == "ok"
            and scheduler_status in {"ok", "disabled"}
        )
        return ready, {
            "status": "ready" if ready else "not_ready",
            "checks": {
                "database": {"status": database_status},
                "migration": {
                    "status": migration_status,
                    "current": current_revision,
                    "expected": expected_revision,
                },
                "scheduler": {"status": scheduler_status},
            },
        }
