"""Stable contracts for importing external conversation records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

CONVERSATION_IMPORT_CONTRACT_VERSION = "conversation.import.v1"
CANDIDATE_ADMISSION_STATE = "candidate"


@dataclass(frozen=True, slots=True)
class ImportedMessage:
    """One external message node, including its original graph identity."""

    external_id: str
    parent_external_id: str | None
    role: str
    content_type: str
    content: str
    original_created_at: str | None = None
    normalized_created_at: str | None = None
    model: str | None = None
    citations: tuple[dict[str, Any], ...] = ()
    attachments: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    is_canonical: bool = True
    ordinal: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "parent_external_id": self.parent_external_id,
            "role": self.role,
            "content_type": self.content_type,
            "content": self.content,
            "original_created_at": self.original_created_at,
            "normalized_created_at": self.normalized_created_at,
            "model": self.model,
            "citations": list(self.citations),
            "attachments": list(self.attachments),
            "metadata": self.metadata,
            "is_canonical": self.is_canonical,
            "ordinal": self.ordinal,
        }


@dataclass(frozen=True, slots=True)
class ImportedConversation:
    """One external conversation and every preserved message branch."""

    external_id: str
    title: str
    messages: tuple[ImportedMessage, ...]
    original_created_at: str | None = None
    normalized_created_at: str | None = None
    original_updated_at: str | None = None
    normalized_updated_at: str | None = None
    current_message_external_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    ordinal: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "title": self.title,
            "original_created_at": self.original_created_at,
            "normalized_created_at": self.normalized_created_at,
            "original_updated_at": self.original_updated_at,
            "normalized_updated_at": self.normalized_updated_at,
            "current_message_external_id": self.current_message_external_id,
            "metadata": self.metadata,
            "ordinal": self.ordinal,
            "messages": [message.as_dict() for message in self.messages],
        }


@dataclass(frozen=True, slots=True)
class ConversationImportResult:
    conversations: tuple[ImportedConversation, ...]
    warnings: tuple[str, ...] = ()


class ConversationImporter(Protocol):
    """Adapter contract registered by the Conversation Import Registry."""

    importer_id: str
    version: int
    display_name: str
    description: str

    def parse(self, payload: object) -> ConversationImportResult: ...
