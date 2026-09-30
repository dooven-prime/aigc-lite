"""Registry for versioned external conversation import adapters."""

from __future__ import annotations

from ..adapters.conversation_import import (
    ChatGPTConversationImporter,
    DeepSeekConversationImporter,
)
from ..core.conversation_imports import ConversationImporter
from ..core.errors import InvalidConversationImportError


class ConversationImportRegistry:
    def __init__(self, importers: list[ConversationImporter] | None = None) -> None:
        self._importers: dict[str, ConversationImporter] = {}
        for importer in importers or []:
            if importer.importer_id in self._importers:
                raise ValueError(f"Duplicate conversation importer: {importer.importer_id}")
            self._importers[importer.importer_id] = importer

    @classmethod
    def builtins(cls) -> ConversationImportRegistry:
        return cls([ChatGPTConversationImporter(), DeepSeekConversationImporter()])

    def get(self, importer_id: str) -> ConversationImporter:
        importer = self._importers.get(importer_id)
        if importer is None:
            raise InvalidConversationImportError(
                "importer_id", "Conversation importer is not registered"
            )
        return importer

    def list(self) -> list[dict]:
        return [
            {
                "importer_id": importer.importer_id,
                "version": importer.version,
                "display_name": importer.display_name,
                "description": importer.description,
            }
            for importer in sorted(
                self._importers.values(), key=lambda item: item.importer_id
            )
        ]
