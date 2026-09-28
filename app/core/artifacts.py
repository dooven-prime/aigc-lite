"""Transport-neutral Artifact and Citation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ArtifactKind(StrEnum):
    """Stable storage/display categories for produced values."""

    TEXT = "text"
    MARKDOWN = "markdown"
    JSON = "json"
    FILE = "file"
    LINK = "link"


class CitationSourceKind(StrEnum):
    """Kinds of evidence that may support an Artifact or Run."""

    DOCUMENT = "document"
    URL = "url"
    TOOL = "tool"
    ARTIFACT = "artifact"


@dataclass(frozen=True, slots=True)
class ArtifactDraft:
    """A provider-produced value waiting to be persisted in a workspace."""

    name: str
    kind: ArtifactKind
    media_type: str
    content_text: str = ""
    uri: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CitationDraft:
    """Evidence for a Run or Artifact waiting to be persisted.

    ``artifact_index`` addresses the matching item in the ArtifactDraft tuple
    returned by the same tool invocation. It avoids leaking database ids into
    provider adapters.
    """

    source_kind: CitationSourceKind
    title: str
    source_id: str | None = None
    source_uri: str | None = None
    locator: dict = field(default_factory=dict)
    excerpt: str = ""
    metadata: dict = field(default_factory=dict)
    artifact_index: int | None = None
