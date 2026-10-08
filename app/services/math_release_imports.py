"""Preview and atomically admit a pinned math catalogue to candidate storage."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError as SQLAlchemyIntegrityError

from ..adapters.research_import import OpenAIMathReleaseAdapter
from ..core.contracts import RequestContext
from ..core.errors import InvalidEvidenceError, ResourceNotFoundError
from ..core.qualification import canonical_hash
from ..database import get_repository
from ..repository import Repository

RepositoryProvider = Callable[[], Repository]


class MathReleaseImportService:
    def __init__(
        self,
        repository_provider: RepositoryProvider = get_repository,
        *,
        adapter: OpenAIMathReleaseAdapter | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._adapter = adapter or OpenAIMathReleaseAdapter()

    def _load(self, source_commit: str) -> dict[str, Any]:
        contents_bytes, formalization_bytes = self._adapter.fetch(source_commit)
        normalized = self._adapter.parse(
            source_commit, contents_bytes, formalization_bytes
        )
        normalized["contents_bytes"] = contents_bytes
        normalized["formalization_bytes"] = formalization_bytes
        normalized["contents_text"] = contents_bytes.decode("utf-8")
        normalized["formalization_text"] = formalization_bytes.decode("utf-8")
        return normalized

    @staticmethod
    def _preview(value: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value[key]
            for key in (
                "contract_version",
                "source_commit",
                "contents_hash",
                "formalization_hash",
                "manifest_hash",
                "preview_hash",
                "family_count",
                "manuscript_count",
                "lean_linked_family_count",
            )
        } | {
            "source_repository": "openai/math",
            "admission_state": "candidate",
            "claim_revision_count": 0,
            "qualification_receipt_count": 0,
            "current_use_binding_count": 0,
            "sample_families": [
                {"id": item["external_family_id"], "title": item["title"]}
                for item in value["manifest"]["families"][:10]
            ],
        }

    def preview(self, context: RequestContext, source_commit: str) -> dict[str, Any]:
        del context
        return self._preview(self._load(source_commit))

    def commit(
        self,
        context: RequestContext,
        source_commit: str,
        expected_preview_hash: str,
    ) -> dict[str, Any]:
        normalized = self._load(source_commit)
        if not secrets.compare_digest(
            normalized["preview_hash"], expected_preview_hash.strip()
        ):
            raise InvalidEvidenceError(
                "expected_preview_hash", "Pinned source no longer matches preview"
            )
        repository = self._repository_provider()
        existing = repository.get_math_release_import_by_commit(
            context.workspace_id, source_commit
        )
        if existing is not None:
            return self._deduplicated(existing, normalized["preview_hash"])
        try:
            committed = repository.create_math_release_import(
                context.workspace_id,
                {**normalized, "requested_by": context.principal_id},
            )
        except (sqlite3.IntegrityError, SQLAlchemyIntegrityError):
            existing = repository.get_math_release_import_by_commit(
                context.workspace_id, source_commit
            )
            if existing is None:
                raise
            return self._deduplicated(existing, normalized["preview_hash"])
        return {**committed, "deduplicated": False}

    @staticmethod
    def _deduplicated(existing: dict, preview_hash: str) -> dict:
        if existing["preview_hash"] != preview_hash:
            raise InvalidEvidenceError(
                "source_commit", "A different catalogue is already bound to this commit"
            )
        return {**existing, "deduplicated": True}

    def get(
        self, context: RequestContext, import_id: str, *, include_families: bool = False
    ) -> dict:
        repository = self._repository_provider()
        value = repository.get_math_release_import(context.workspace_id, import_id)
        if value is None:
            raise ResourceNotFoundError("math_release_import", import_id)
        if canonical_hash(value["manifest"]) != value["manifest_hash"]:
            raise InvalidEvidenceError("manifest", "Candidate catalogue manifest hash changed")
        for label in ("contents", "formalization"):
            artifact = repository.get_artifact(
                context.workspace_id, value[f"{label}_artifact_id"]
            )
            if (
                artifact is None
                or hashlib.sha256(
                    (artifact.get("content_text") or "").encode("utf-8")
                ).hexdigest()
                != value[f"{label}_hash"]
            ):
                raise InvalidEvidenceError("source", "Candidate source Artifact changed")
        if not include_families:
            value.pop("manifest")
        return value

    def list(self, context: RequestContext, limit: int = 50) -> list[dict]:
        return self._repository_provider().list_math_release_imports(
            context.workspace_id, limit
        )
