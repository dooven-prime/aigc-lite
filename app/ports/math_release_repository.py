"""Persistence port for candidate-only mathematics catalogue imports."""

from __future__ import annotations

from typing import Any, Protocol


class MathReleaseRepository(Protocol):
    def create_math_release_import(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def get_math_release_import(
        self, tenant_id: str, import_id: str
    ) -> dict | None: ...

    def get_math_release_import_by_commit(
        self, tenant_id: str, source_commit: str
    ) -> dict | None: ...

    def list_math_release_imports(
        self, tenant_id: str, limit: int = 50
    ) -> list[dict]: ...
