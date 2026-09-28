"""Port for workspace execution-memory search."""

from __future__ import annotations

from typing import Protocol


class SearchBackend(Protocol):
    """Search one workspace without exposing storage-specific query syntax."""

    backend_id: str

    def search(self, workspace_id: str, query: str, limit: int = 20) -> list[dict]: ...
