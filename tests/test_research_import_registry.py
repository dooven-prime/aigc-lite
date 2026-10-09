import pytest

from app.adapters.research_import import FrontierRegistryAdapter, OpenAIMathReleaseAdapter
from app.core.errors import InvalidEvidenceError
from app.services.research_import_registry import ResearchImportRegistry


def test_builtin_importers_are_versioned_and_admission_bounded() -> None:
    registry = ResearchImportRegistry.builtins()
    values = {item["importer_id"]: item for item in registry.list()}
    assert set(values) == {"frontier.registry", "openai.math"}
    assert values["frontier.registry"]["target_surface"] == "research_claim_candidate"
    assert values["frontier.registry"]["preview_endpoint"] is None
    assert values["openai.math"]["target_surface"] == "catalogue_candidate_only"
    assert values["openai.math"]["preview_endpoint"] == (
        "/api/research/math-release-imports/preview"
    )
    assert all(item["version"] == 1 for item in values.values())
    assert all(not item["qualification_granted"] for item in values.values())
    assert all(not item["knowledge_admitted"] for item in values.values())
    assert registry.describe("openai.math", 1) == values["openai.math"]

    with pytest.raises(InvalidEvidenceError, match="not registered"):
        registry.get("openai.math", 2)
    with pytest.raises(ValueError, match="duplicate"):
        ResearchImportRegistry([FrontierRegistryAdapter(), FrontierRegistryAdapter()])


def test_registry_holds_actual_adapters_not_generic_upload_routes() -> None:
    registry = ResearchImportRegistry.builtins()
    assert isinstance(registry.get("frontier.registry", 1), FrontierRegistryAdapter)
    assert isinstance(registry.get("openai.math", 1), OpenAIMathReleaseAdapter)
    assert not hasattr(registry, "commit")
