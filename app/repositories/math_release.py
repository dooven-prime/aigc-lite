"""Atomic source-Artifact and candidate catalogue persistence."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from .common import utc_now


def _rows(tenant_id: str, values: dict[str, Any]) -> tuple[list[dict], dict]:
    created_at = utc_now()
    artifacts = []
    for label, kind, media_type in (
        ("contents", "markdown", "text/markdown"),
        ("formalization", "text", "text/yaml"),
    ):
        artifact_id = str(uuid4())
        values[f"{label}_artifact_id"] = artifact_id
        artifacts.append(
            {
                "id": artifact_id,
                "tenant_id": tenant_id,
                "series_id": artifact_id,
                "version": 1,
                "run_id": None,
                "step_id": None,
                "name": f"openai-math-{values['source_commit']}-{label}",
                "kind": kind,
                "media_type": media_type,
                "content_text": values[f"{label}_text"],
                "uri": None,
                "content_hash": values[f"{label}_hash"],
                "size_bytes": len(values[f"{label}_bytes"]),
                "metadata": json.dumps(
                    {
                        "source_repository": "openai/math",
                        "source_commit": values["source_commit"],
                        "artifact_role": f"math_release_{label}_source",
                        "admission_state": "candidate",
                        "qualification_granted": False,
                    }
                ),
                "created_at": created_at,
            }
        )
    row = {
        "id": str(uuid4()),
        "tenant_id": tenant_id,
        "contract_version": values["contract_version"],
        "source_repository": "openai/math",
        "source_commit": values["source_commit"],
        "contents_artifact_id": values["contents_artifact_id"],
        "contents_hash": values["contents_hash"],
        "formalization_artifact_id": values["formalization_artifact_id"],
        "formalization_hash": values["formalization_hash"],
        "manifest_hash": values["manifest_hash"],
        "preview_hash": values["preview_hash"],
        "manifest": json.dumps(values["manifest"], ensure_ascii=False),
        "family_count": values["family_count"],
        "manuscript_count": values["manuscript_count"],
        "lean_linked_family_count": values["lean_linked_family_count"],
        "admission_state": "candidate",
        "requested_by": values.get("requested_by"),
        "created_at": created_at,
    }
    return artifacts, row


def _decode(row: Any, *, include_manifest: bool = False) -> dict:
    result = dict(row)
    if include_manifest:
        result["manifest"] = json.loads(result["manifest"])
    else:
        result.pop("manifest", None)
    return result


class SQLiteMathReleaseRepositoryMixin:
    def create_math_release_import(self, tenant_id: str, values: dict[str, Any]) -> dict:
        artifacts, row = _rows(tenant_id, dict(values))
        with self._connect() as db:
            db.executemany(
                "INSERT INTO artifacts(id, tenant_id, series_id, version, run_id, step_id, "
                "name, kind, media_type, content_text, uri, content_hash, size_bytes, metadata, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [tuple(artifact.values()) for artifact in artifacts],
            )
            db.execute(
                "INSERT INTO math_release_imports(id, tenant_id, contract_version, "
                "source_repository, source_commit, contents_artifact_id, contents_hash, "
                "formalization_artifact_id, formalization_hash, manifest_hash, preview_hash, "
                "manifest, family_count, manuscript_count, lean_linked_family_count, "
                "admission_state, requested_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(row.values()),
            )
        return _decode(row)

    def get_math_release_import(self, tenant_id: str, import_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM math_release_imports WHERE tenant_id = ? AND id = ?",
                (tenant_id, import_id),
            ).fetchone()
        return _decode(row, include_manifest=True) if row is not None else None

    def get_math_release_import_by_commit(
        self, tenant_id: str, source_commit: str
    ) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM math_release_imports WHERE tenant_id = ? AND source_commit = ?",
                (tenant_id, source_commit),
            ).fetchone()
        return _decode(row) if row is not None else None

    def list_math_release_imports(self, tenant_id: str, limit: int = 50) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM math_release_imports WHERE tenant_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (tenant_id, max(1, min(limit, 200))),
            ).fetchall()
        return [_decode(row) for row in rows]


class PostgresMathReleaseRepositoryMixin:
    def create_math_release_import(self, tenant_id: str, values: dict[str, Any]) -> dict:
        from sqlalchemy import text

        artifacts, row = _rows(tenant_id, dict(values))
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO artifacts(id, tenant_id, series_id, version, run_id, step_id, "
                    "name, kind, media_type, content_text, uri, content_hash, size_bytes, metadata, "
                    "created_at) VALUES (:id, :tenant_id, :series_id, :version, :run_id, "
                    ":step_id, :name, :kind, :media_type, :content_text, :uri, :content_hash, "
                    ":size_bytes, :metadata, :created_at)"
                ),
                artifacts,
            )
            connection.execute(
                text(
                    "INSERT INTO math_release_imports(id, tenant_id, contract_version, "
                    "source_repository, source_commit, contents_artifact_id, contents_hash, "
                    "formalization_artifact_id, formalization_hash, manifest_hash, preview_hash, "
                    "manifest, family_count, manuscript_count, lean_linked_family_count, "
                    "admission_state, requested_by, created_at) VALUES "
                    "(:id, :tenant_id, :contract_version, :source_repository, :source_commit, "
                    ":contents_artifact_id, :contents_hash, :formalization_artifact_id, "
                    ":formalization_hash, :manifest_hash, :preview_hash, :manifest, "
                    ":family_count, :manuscript_count, :lean_linked_family_count, "
                    ":admission_state, :requested_by, :created_at)"
                ),
                row,
            )
        return _decode(row)

    def get_math_release_import(self, tenant_id: str, import_id: str) -> dict | None:
        row = self._one(
            "SELECT * FROM math_release_imports WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": import_id},
        )
        return _decode(row, include_manifest=True) if row is not None else None

    def get_math_release_import_by_commit(
        self, tenant_id: str, source_commit: str
    ) -> dict | None:
        row = self._one(
            "SELECT * FROM math_release_imports WHERE tenant_id = :tenant_id "
            "AND source_commit = :source_commit",
            {"tenant_id": tenant_id, "source_commit": source_commit},
        )
        return _decode(row) if row is not None else None

    def list_math_release_imports(self, tenant_id: str, limit: int = 50) -> list[dict]:
        rows = self._many(
            "SELECT * FROM math_release_imports WHERE tenant_id = :tenant_id "
            "ORDER BY created_at DESC, id DESC LIMIT :limit",
            {"tenant_id": tenant_id, "limit": max(1, min(limit, 200))},
        )
        return [_decode(row) for row in rows]
