"""Explicit, versioned inventory of source-specific research import adapters.

The inventory is read-only. It does not turn a source descriptor into a
generic upload route or grant an importer qualification authority.
"""

from __future__ import annotations

from typing import Protocol

from ..adapters.research_import import (
    FrontierRegistryAdapter,
    OpenAIMathReleaseAdapter,
    RimeConsumerWitnessAdapter,
)
from ..core.errors import InvalidEvidenceError


class ResearchImporter(Protocol):
    importer_id: str
    version: int
    display_name: str
    description: str
    source_format: str
    target_surface: str
    preview_endpoint: str | None
    commit_endpoint: str
    importer_contract: str


class ResearchImportRegistry:
    def __init__(self, importers: list[ResearchImporter]) -> None:
        self._importers: dict[tuple[str, int], ResearchImporter] = {}
        for importer in importers:
            key = (importer.importer_id, importer.version)
            if not importer.importer_id or importer.version < 1 or key in self._importers:
                raise ValueError(f"Invalid or duplicate research importer: {key}")
            if importer.target_surface not in {
                "research_claim_candidate",
                "catalogue_candidate_only",
            }:
                raise ValueError(f"Unsupported research import target: {importer.target_surface}")
            self._importers[key] = importer

    @classmethod
    def builtins(cls) -> ResearchImportRegistry:
        return cls([FrontierRegistryAdapter(), OpenAIMathReleaseAdapter(), RimeConsumerWitnessAdapter()])

    def get(self, importer_id: str, version: int) -> ResearchImporter:
        try:
            return self._importers[(importer_id, version)]
        except KeyError as exc:
            raise InvalidEvidenceError(
                "importer", "Research importer ID/version is not registered"
            ) from exc

    @staticmethod
    def _describe(importer: ResearchImporter) -> dict:
        return {
            "importer_id": importer.importer_id,
            "version": importer.version,
            "display_name": importer.display_name,
            "description": importer.description,
            "source_format": importer.source_format,
            "target_surface": importer.target_surface,
            "preview_endpoint": importer.preview_endpoint,
            "commit_endpoint": importer.commit_endpoint,
            "importer_contract": importer.importer_contract,
            "qualification_granted": False,
            "knowledge_admitted": False,
        }

    def describe(self, importer_id: str, version: int) -> dict:
        return self._describe(self.get(importer_id, version))

    def list(self) -> list[dict]:
        return [
            self._describe(importer)
            for _, importer in sorted(self._importers.items())
        ]
