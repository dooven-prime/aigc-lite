import asyncio
import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import database, main
from app.core.artifacts import (
    ArtifactDraft,
    ArtifactKind,
    CitationDraft,
    CitationSourceKind,
)
from app.core.contracts import (
    RequestContext,
    ToolProviderResult,
    ToolSource,
    ToolSpec,
)
from app.core.errors import ResourceNotFoundError
from app.repository import SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.tool_catalog import ToolCatalog
from app.services.tools import ToolService


def _run_with_step(repository: SQLiteRepository) -> tuple[dict, dict]:
    run = repository.create_run("workspace-a", "", "request-1", None, "tool-catalog")
    step = repository.append_run_step(
        "workspace-a",
        run["id"],
        1,
        "tool",
        "research",
        "succeeded",
        "{}",
        "result",
    )
    return run, step


def test_artifact_service_links_run_step_redacts_and_searches(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "artifacts.db")
    repository.init()
    run, step = _run_with_step(repository)
    service = ArtifactService(repository_provider=lambda: repository)
    context = RequestContext(request_id="request-1", workspace_id="workspace-a")

    artifacts, citations = service.record_tool_result(
        context,
        run_id=run["id"],
        step_id=step["id"],
        artifacts=(
            ArtifactDraft(
                name="Research result",
                kind=ArtifactKind.JSON,
                media_type="application/json",
                content_text='{"api_key":"secret", "answer":"evidence marker"}',
                uri="https://example.test/report?token=secret",
                metadata={"authorization": "Bearer secret", "format": "report"},
            ),
        ),
        citations=(
            CitationDraft(
                source_kind=CitationSourceKind.URL,
                title="Primary source",
                source_uri="https://example.test/source?api_key=secret",
                excerpt="evidence marker Bearer secret",
                locator={"section": "results", "token": "secret"},
                artifact_index=0,
            ),
        ),
    )

    artifact = service.get(context, artifacts[0]["id"])
    assert artifact["run_id"] == run["id"]
    assert artifact["step_id"] == step["id"]
    assert artifact["content_text"] == '{"api_key": "***", "answer": "evidence marker"}'
    assert "secret" not in artifact["uri"]
    assert artifact["metadata"]["authorization"] == "***"
    assert artifact["citations"][0]["id"] == citations[0]["id"]
    assert artifact["citations"][0]["locator"]["token"] == "***"

    detail = repository.get_run("workspace-a", run["id"])
    assert [item["id"] for item in detail["artifacts"]] == [artifact["id"]]
    assert [item["id"] for item in detail["citations"]] == [citations[0]["id"]]
    assert {item["kind"] for item in repository.search_memory(
        "workspace-a", "evidence marker", 20
    )} == {"artifact", "citation"}

    other = RequestContext(request_id="request-2", workspace_id="workspace-b")
    with pytest.raises(ResourceNotFoundError):
        service.get(other, artifact["id"])
    assert service.list(other) == []


class StructuredProvider:
    provider_id = "research"

    async def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="research__report",
                native_name="report",
                description="Produce a structured report.",
                input_schema={"type": "object"},
                source=ToolSource.MCP,
                provider_id=self.provider_id,
            )
        ]

    async def call_tool(
        self, native_name: str, arguments: dict[str, Any]
    ) -> ToolProviderResult:
        return ToolProviderResult(
            content='{"summary":"catalog artifact"}',
            artifacts=(
                ArtifactDraft(
                    name="report result",
                    kind=ArtifactKind.JSON,
                    media_type="application/json",
                    content_text='{"summary":"catalog artifact"}',
                ),
            ),
            citations=(
                CitationDraft(
                    source_kind=CitationSourceKind.TOOL,
                    title="research:report",
                    source_id="research:report",
                    artifact_index=0,
                ),
            ),
        )


def test_tool_service_persists_catalog_artifacts_and_citations(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "tool-artifacts.db")
    repository.init()
    service = ToolService(
        repository_provider=lambda: repository,
        tool_catalog=ToolCatalog([StructuredProvider()]),
    )
    context = RequestContext(request_id="request-1", workspace_id="workspace-a")

    result, run_id = asyncio.run(
        service.invoke(context, "research__report", {}, transport="http")
    )

    assert not result.failed
    detail = repository.get_run("workspace-a", run_id)
    assert detail["status"] == "succeeded"
    assert detail["artifacts"][0]["name"] == "report result"
    assert detail["citations"][0]["artifact_id"] == detail["artifacts"][0]["id"]


def test_artifact_http_surface_is_workspace_scoped(tmp_path, monkeypatch) -> None:
    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "artifact-api.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)

    with TestClient(main.app) as client:
        context = RequestContext(request_id="api", workspace_id="default")
        artifact = main.artifact_service.create_artifact(
            context,
            ArtifactDraft(
                name="api-result.md",
                kind=ArtifactKind.MARKDOWN,
                media_type="text/markdown",
                content_text="artifact api marker",
            ),
        )
        listed = client.get("/api/artifacts")
        detail = client.get(f"/api/artifacts/{artifact['id']}")
        missing = client.get("/api/artifacts/unknown")

    assert listed.status_code == 200
    assert listed.json()[0]["id"] == artifact["id"]
    assert detail.status_code == 200
    assert detail.json()["citations"] == []
    assert missing.status_code == 404
