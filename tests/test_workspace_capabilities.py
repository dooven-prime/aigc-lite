import asyncio
import json

from app.adapters.tools import workspace as workspace_adapter
from app.adapters.tools.workspace import WorkspaceCapabilityProviderSource
from app.core.contracts import RequestContext, ToolSource
from app.core.errors import ResourceNotFoundError
from app.repository import SQLiteRepository
from app.services.tool_catalog import create_default_tool_catalog
from app.services.tools import ToolService


class FakeMemoryService:
    def search(self, context, query, limit):
        return [{"id": "search-1", "workspace": context.workspace_id, "query": query, "limit": limit}]

    def get_run(self, context, run_id):
        if run_id == "missing":
            raise ResourceNotFoundError("run", run_id)
        return {
            "id": run_id,
            "workspace_id": context.workspace_id,
            "steps": [
                {
                    "id": "step-1",
                    "input_content": "input-secret",
                    "output_content": "output-value",
                }
            ],
        }


class FakeArtifactService:
    def get(self, context, artifact_id):
        return {
            "id": artifact_id,
            "workspace_id": context.workspace_id,
            "content_text": "abcdefghij",
            "citations": [{"id": "citation-1"}, {"id": "citation-2"}],
        }


class FakeResearchRegistryService:
    def get_claim(self, context, claim_id):
        return {
            "id": claim_id,
            "workspace_id": context.workspace_id,
            "relations": [{"id": "relation-1"}, {"id": "relation-2"}],
            "verification_attempts": [{"id": "attempt-1"}],
            "verification_plans": [],
            "verification_executions": [],
            "promotion_evaluations": [],
        }


class FakeQualificationService:
    def qualified_search(self, context, query, profile, limit):
        return [
            {
                "id": "claim-1",
                "workspace_id": context.workspace_id,
                "query": query,
                "profile_id": profile,
                "limit": limit,
            }
        ]

    def get_receipt(self, context, receipt_id):
        return {
            "qualification_receipt_id": receipt_id,
            "workspace_id": context.workspace_id,
        }

    def list_profiles(self):
        return [{"profile_id": "math.formal.v1", "policy_version": "1"}]


def _provider(workspace_id: str = "workspace-a"):
    source = WorkspaceCapabilityProviderSource(
        memory_service=FakeMemoryService(),
        artifact_service=FakeArtifactService(),
        research_registry_service=FakeResearchRegistryService(),
        qualification_service=FakeQualificationService(),
    )
    context = RequestContext(
        request_id="workspace-capability-test",
        workspace_id=workspace_id,
        principal_id="user-1",
    )
    return source.list_providers(context)[0]


def test_workspace_capabilities_are_read_only_and_context_bound() -> None:
    async def run():
        provider = _provider()
        specs = await provider.list_tools()
        search = await provider.call_tool(
            "workspace_search", {"query": "evidence", "limit": 3}
        )
        run_detail = await provider.call_tool(
            "workspace_get_run",
            {
                "run_id": "run-1",
                "include_step_content": True,
                "max_step_chars": 5,
            },
        )
        artifact = await provider.call_tool(
            "workspace_get_artifact",
            {"artifact_id": "artifact-1", "max_content_chars": 4, "citation_limit": 1},
        )
        claim = await provider.call_tool(
            "workspace_get_claim", {"claim_revision_id": "claim-1", "history_limit": 1}
        )
        qualified = await provider.call_tool(
            "workspace_qualified_search",
            {"query": "theorem", "profile": "math.formal.v1"},
        )
        receipt = await provider.call_tool(
            "workspace_get_qualification_receipt", {"receipt_id": "receipt-1"}
        )
        profiles = await provider.call_tool(
            "workspace_list_qualification_profiles", {}
        )
        return specs, search, run_detail, artifact, claim, qualified, receipt, profiles

    specs, search, run_detail, artifact, claim, qualified, receipt, profiles = (
        asyncio.run(run())
    )
    assert {spec.name for spec in specs} == {
        "workspace_search",
        "workspace_qualified_search",
        "workspace_get_run",
        "workspace_get_artifact",
        "workspace_get_claim",
        "workspace_get_qualification_receipt",
        "workspace_list_qualification_profiles",
    }
    assert all(spec.source is ToolSource.WORKSPACE for spec in specs)
    assert all(spec.workspace_id == "workspace-a" for spec in specs)
    assert all(spec.hints.read_only and not spec.hints.destructive for spec in specs)
    assert all(not spec.hints.open_world for spec in specs)
    assert all(spec.extensions["authority_mutation"] is False for spec in specs)

    search_value = json.loads(search.content)
    assert search_value["schema"] == "aigc-lite.workspace-capability.v1"
    assert search_value["result"][0]["workspace"] == "workspace-a"

    run_value = json.loads(run_detail.content)["result"]
    assert run_value["steps"][0]["input_content"] == "input"
    assert run_value["steps"][0]["input_content_chars"] == 12
    assert run_value["steps"][0]["input_content_truncated"] is True

    artifact_value = json.loads(artifact.content)["result"]
    assert artifact_value["content_text"] == "abcd"
    assert artifact_value["content_chars"] == 10
    assert artifact_value["citation_count"] == 2
    assert [item["id"] for item in artifact_value["citations"]] == ["citation-1"]

    claim_value = json.loads(claim.content)["result"]
    assert len(claim_value["relations"]) == 1
    assert claim_value["history_counts"]["relations"] == 2
    assert json.loads(qualified.content)["result"][0]["profile_id"] == "math.formal.v1"
    assert json.loads(receipt.content)["result"]["qualification_receipt_id"] == "receipt-1"
    assert json.loads(profiles.content)["result"][0]["profile_id"] == "math.formal.v1"


def test_workspace_capabilities_fail_closed_with_stable_errors() -> None:
    async def run():
        provider = _provider()
        invalid = await provider.call_tool(
            "workspace_search", {"query": "ok", "unexpected": True}
        )
        missing = await provider.call_tool(
            "workspace_get_run", {"run_id": "missing"}
        )
        unknown = await provider.call_tool("workspace_promote_claim", {})
        return invalid, missing, unknown

    invalid, missing, unknown = asyncio.run(run())
    assert invalid.failed and json.loads(invalid.content) == {
        "error": "invalid_tool_arguments"
    }
    assert missing.failed and json.loads(missing.content) == {
        "error": "resource_not_found"
    }
    assert unknown.failed and json.loads(unknown.content) == {
        "error": "tool_not_available"
    }


def test_workspace_capability_size_limit_keeps_error_json_valid(monkeypatch) -> None:
    monkeypatch.setattr(workspace_adapter.settings, "max_tool_result_chars", 40)

    result = asyncio.run(
        _provider().call_tool("workspace_list_qualification_profiles", {})
    )

    assert result.failed
    assert json.loads(result.content) == {"error": "tool_result_too_large"}


def test_workspace_capability_calls_use_the_normal_tool_ledger(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "workspace-capabilities.db")
    repository.init()
    source_run = repository.create_run(
        "workspace-a", "", "source-request", None, "test-model"
    )
    repository.append_run_step(
        "workspace-a",
        source_run["id"],
        1,
        "model",
        "test-model",
        "succeeded",
        "private input",
        "bounded output",
        {},
    )
    repository.finish_run("workspace-a", source_run["id"], "succeeded")

    service = ToolService(
        repository_provider=lambda: repository,
        tool_catalog=create_default_tool_catalog(lambda: repository),
    )
    context = RequestContext(
        request_id="capability-call",
        workspace_id="workspace-a",
        principal_id="user-1",
    )

    async def run():
        discovered = await service.discover(context)
        result, invocation_run_id = await service.invoke(
            context,
            "workspace_get_run",
            {"run_id": source_run["id"]},
            transport="mcp",
        )
        isolated, _ = await service.invoke(
            RequestContext(
                request_id="cross-workspace-call",
                workspace_id="workspace-b",
                principal_id="user-2",
            ),
            "workspace_get_run",
            {"run_id": source_run["id"]},
            transport="mcp",
        )
        return discovered, result, invocation_run_id, isolated

    discovered, result, invocation_run_id, isolated = asyncio.run(run())
    assert "workspace_get_run" in {spec.name for spec in discovered.specs}
    assert not result.failed
    payload = json.loads(result.content)
    assert payload["result"]["id"] == source_run["id"]
    assert "input_content" not in payload["result"]["steps"][0]

    invocation = repository.get_run("workspace-a", invocation_run_id)
    assert invocation["status"] == "succeeded"
    assert invocation["steps"][0]["name"] == "workspace_get_run"
    assert invocation["steps"][0]["metadata"]["source"] == "workspace"
    assert invocation["steps"][0]["metadata"]["contract"] == (
        "aigc-lite.workspace-capability.v1"
    )
    assert isolated.failed
    assert json.loads(isolated.content) == {"error": "resource_not_found"}
