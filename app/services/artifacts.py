"""Workspace-scoped Artifact and Citation persistence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence

from ..core.artifacts import ArtifactDraft, CitationDraft
from ..core.contracts import RequestContext
from ..core.errors import InvalidArtifactError, ResourceNotFoundError
from ..database import get_repository
from ..redaction import redact, redact_record_text, redact_text
from ..repository import Repository

RepositoryProvider = Callable[[], Repository]

_MAX_NAME_CHARS = 255
_MAX_MEDIA_TYPE_CHARS = 255
_MAX_URI_CHARS = 4_000
_MAX_CONTENT_CHARS = 1_000_000
_MAX_EXCERPT_CHARS = 20_000
_MAX_JSON_CHARS = 50_000


class ArtifactService:
    """Validate, redact, and persist produced values and their provenance."""

    def __init__(
        self, *, repository_provider: RepositoryProvider = get_repository
    ) -> None:
        self._repository_provider = repository_provider

    def create_artifact(
        self,
        context: RequestContext,
        draft: ArtifactDraft,
        *,
        run_id: str | None = None,
        step_id: str | None = None,
    ) -> dict:
        self._validate_run_step(context, run_id, step_id)
        name = redact_text(draft.name.strip())
        media_type = draft.media_type.strip().casefold()
        content = redact_record_text(draft.content_text)
        uri = redact_text(draft.uri.strip()) if draft.uri else None
        metadata = self._safe_mapping("metadata", redact(draft.metadata))
        self._bounded("name", name, _MAX_NAME_CHARS, required=True)
        self._bounded(
            "media_type", media_type, _MAX_MEDIA_TYPE_CHARS, required=True
        )
        self._bounded("content_text", content, _MAX_CONTENT_CHARS)
        self._bounded("uri", uri or "", _MAX_URI_CHARS)
        if not content and not uri:
            raise InvalidArtifactError(
                "content_text", "Artifact content or URI is required"
            )
        payload = content if content else uri or ""
        encoded = payload.encode("utf-8")
        return self._repository_provider().create_artifact(
            context.workspace_id,
            {
                "run_id": run_id,
                "step_id": step_id,
                "name": name,
                "kind": draft.kind.value,
                "media_type": media_type,
                "content_text": content,
                "uri": uri,
                "content_hash": hashlib.sha256(encoded).hexdigest(),
                "size_bytes": len(content.encode("utf-8")),
                "metadata": metadata,
            },
        )

    def create_citation(
        self,
        context: RequestContext,
        draft: CitationDraft,
        *,
        run_id: str | None = None,
        step_id: str | None = None,
        artifact_id: str | None = None,
    ) -> dict:
        self._validate_run_step(context, run_id, step_id)
        repository = self._repository_provider()
        if artifact_id is not None and repository.get_artifact(
            context.workspace_id, artifact_id
        ) is None:
            raise ResourceNotFoundError("artifact", artifact_id)
        title = redact_text(draft.title.strip())
        source_id = redact_text(draft.source_id.strip()) if draft.source_id else None
        source_uri = (
            redact_text(draft.source_uri.strip()) if draft.source_uri else None
        )
        excerpt = redact_record_text(draft.excerpt)
        locator = self._safe_mapping("locator", redact(draft.locator))
        metadata = self._safe_mapping("metadata", redact(draft.metadata))
        self._bounded("title", title, _MAX_NAME_CHARS, required=True)
        self._bounded("source_id", source_id or "", _MAX_URI_CHARS)
        self._bounded("source_uri", source_uri or "", _MAX_URI_CHARS)
        self._bounded("excerpt", excerpt, _MAX_EXCERPT_CHARS)
        if not source_id and not source_uri:
            raise InvalidArtifactError(
                "source", "Citation source id or URI is required"
            )
        return repository.create_citation(
            context.workspace_id,
            {
                "run_id": run_id,
                "step_id": step_id,
                "artifact_id": artifact_id,
                "source_kind": draft.source_kind.value,
                "source_id": source_id,
                "source_uri": source_uri,
                "title": title,
                "locator": locator,
                "excerpt": excerpt,
                "metadata": metadata,
            },
        )

    def record_tool_result(
        self,
        context: RequestContext,
        *,
        run_id: str,
        step_id: str,
        artifacts: Sequence[ArtifactDraft],
        citations: Sequence[CitationDraft],
    ) -> tuple[list[dict], list[dict]]:
        """Persist one tool result while resolving draft-local Artifact indexes."""
        stored_artifacts = [
            self.create_artifact(
                context, draft, run_id=run_id, step_id=step_id
            )
            for draft in artifacts
        ]
        stored_citations = []
        for draft in citations:
            artifact_id = None
            if draft.artifact_index is not None:
                if not 0 <= draft.artifact_index < len(stored_artifacts):
                    raise InvalidArtifactError(
                        "artifact_index", "Citation references an unknown Artifact"
                    )
                artifact_id = stored_artifacts[draft.artifact_index]["id"]
            stored_citations.append(
                self.create_citation(
                    context,
                    draft,
                    run_id=run_id,
                    step_id=step_id,
                    artifact_id=artifact_id,
                )
            )
        return stored_artifacts, stored_citations

    def list(
        self,
        context: RequestContext,
        *,
        limit: int = 50,
        run_id: str | None = None,
    ) -> list[dict]:
        if run_id is not None and self._repository_provider().get_run(
            context.workspace_id, run_id
        ) is None:
            raise ResourceNotFoundError("run", run_id)
        return self._repository_provider().list_artifacts(
            context.workspace_id, limit, run_id
        )

    def get(self, context: RequestContext, artifact_id: str) -> dict:
        repository = self._repository_provider()
        artifact = repository.get_artifact(context.workspace_id, artifact_id)
        if artifact is None:
            raise ResourceNotFoundError("artifact", artifact_id)
        artifact["citations"] = repository.list_citations(
            context.workspace_id, 500, artifact_id=artifact_id
        )
        return artifact

    def _validate_run_step(
        self,
        context: RequestContext,
        run_id: str | None,
        step_id: str | None,
    ) -> None:
        if step_id is not None and run_id is None:
            raise InvalidArtifactError(
                "step_id", "A Step-bound Artifact must also specify run_id"
            )
        if run_id is None:
            return
        run = self._repository_provider().get_run(context.workspace_id, run_id)
        if run is None:
            raise ResourceNotFoundError("run", run_id)
        if step_id is not None and step_id not in {
            step["id"] for step in run.get("steps", [])
        }:
            raise ResourceNotFoundError("run_step", step_id)

    @staticmethod
    def _bounded(field: str, value: str, maximum: int, *, required: bool = False) -> None:
        if required and not value:
            raise InvalidArtifactError(field, f"{field} is required")
        if len(value) > maximum:
            raise InvalidArtifactError(field, f"{field} exceeds {maximum} characters")

    @staticmethod
    def _safe_mapping(field: str, value: object) -> dict:
        if not isinstance(value, dict):
            raise InvalidArtifactError(field, f"{field} must be an object")
        try:
            rendered = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as exc:
            raise InvalidArtifactError(field, f"{field} must be JSON-compatible") from exc
        if len(rendered) > _MAX_JSON_CHARS:
            raise InvalidArtifactError(
                field, f"{field} exceeds {_MAX_JSON_CHARS} characters"
            )
        decoded = json.loads(rendered)
        return decoded
