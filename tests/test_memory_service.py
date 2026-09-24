import pytest

from app.core.contracts import RequestContext
from app.core.errors import ErrorCode, ResourceNotFoundError
from app.repository import SQLiteRepository
from app.services.memory import MemoryService


def test_memory_service_enforces_workspace_context(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "memory-service.db")
    repository.init()
    session = repository.create_session("workspace-a", "Private")
    run = repository.create_run(
        "workspace-a", session["id"], "request-1", None, "test-model"
    )
    service = MemoryService(repository_provider=lambda: repository)
    alpha = RequestContext(request_id="a", workspace_id="workspace-a")
    beta = RequestContext(request_id="b", workspace_id="workspace-b")

    assert service.get_run(alpha, run["id"])["id"] == run["id"]
    with pytest.raises(ResourceNotFoundError) as captured:
        service.get_run(beta, run["id"])
    assert captured.value.code == ErrorCode.RESOURCE_NOT_FOUND
