"""Persistence port for immutable conversation imports."""

from __future__ import annotations

from typing import Any, Protocol


class ImportRepository(Protocol):
    def create_conversation_import(
        self, tenant_id: str, values: dict[str, Any]
    ) -> dict: ...

    def get_conversation_import(
        self, tenant_id: str, batch_id: str
    ) -> dict | None: ...

    def get_conversation_import_by_source(
        self, tenant_id: str, importer_id: str, source_content_hash: str
    ) -> dict | None: ...

    def list_conversation_imports(
        self, tenant_id: str, limit: int = 50
    ) -> list[dict]: ...
