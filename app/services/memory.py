"""Application queries for the workspace execution memory."""

from __future__ import annotations

from ..core.contracts import RequestContext
from ..core.errors import ResourceNotFoundError
from ..database import get_repository
from .gateway import RepositoryProvider


class MemoryService:
    """Read tenant-scoped conversations, knowledge, and execution records."""

    def __init__(self, *, repository_provider: RepositoryProvider = get_repository) -> None:
        self._repository_provider = repository_provider

    def list_runs(self, context: RequestContext, limit: int = 50) -> list[dict]:
        return self._repository_provider().list_runs(context.workspace_id, limit)

    def get_run(self, context: RequestContext, run_id: str) -> dict:
        value = self._repository_provider().get_run(context.workspace_id, run_id)
        if value is None:
            raise ResourceNotFoundError("run", run_id)
        return value

    def search(
        self, context: RequestContext, query: str, limit: int = 20
    ) -> list[dict]:
        return self._repository_provider().search_memory(
            context.workspace_id, query, limit
        )
