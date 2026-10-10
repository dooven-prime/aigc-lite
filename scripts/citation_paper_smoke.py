"""Pinned real-paper HTTP smoke and held-out citation-layout checks.

Run with --pdf-dir DIR containing the three named PDFs, or --download to fetch
only the pinned public URLs. PDFs and the temporary SQLite DB are not committed.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import sqlite3
import tempfile
import urllib.request
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.citation_extraction import create_citation_extraction_router
from app.auth import current_admin_user
from app.core.contracts import RequestContext
from app.repository import SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.citation_extraction import MAX_PDF_BYTES, CitationExtractionService

PAPERS = (
    {
        "role": "fixed_smoke",
        "name": "attention-is-all-you-need.pdf",
        "url": "https://arxiv.org/pdf/1706.03762",
        "sha256": "bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697",
        "marker": "[ 1]",
        "style": "numeric",
        "target_identifier": "arXiv:1607.06450",
    },
    {
        "role": "held_out_style",
        "name": "evidence-retrieval.pdf",
        "url": "https://aclanthology.org/2021.ranlp-1.174.pdf",
        "sha256": "d8bab2c19af822c5477f545ce4a1121c39d249b04b56c8d547932b2fc82b12d8",
        "marker": "(Parikh et al., 2016)",
        "style": "author_year",
        "target_identifier": None,
    },
    {
        "role": "held_out_style",
        "name": "claim-bench.pdf",
        "url": "https://aclanthology.org/2025.ijcnlp-long.127.pdf",
        "sha256": "67eb7c07dee6dcd95d0a2745eb9a7b781a68f4c01f0594426912045d171589a1",
        "marker": "(Lu et al., 2023; Wei et al., 2023)",
        "style": "author_year",
        "target_identifier": None,
    },
)
AUTHORITY_TABLES = ("qualification_receipts", "current_use_bindings", "authorization_grants")


def _bytes(paper: dict, pdf_dir: Path | None, download: bool) -> bytes:
    if pdf_dir is not None:
        content = (pdf_dir / paper["name"]).read_bytes()
    elif download:
        with urllib.request.urlopen(paper["url"], timeout=60) as response:
            content = response.read(MAX_PDF_BYTES + 1)
    else:
        raise ValueError("Pass --pdf-dir or --download")
    if len(content) > MAX_PDF_BYTES or hashlib.sha256(content).hexdigest() != paper["sha256"]:
        raise ValueError(f"Pinned source hash/size mismatch: {paper['name']}")
    return content


def _counts(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(path)) as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in AUTHORITY_TABLES
        }


def run(pdf_dir: Path | None, download: bool) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="aigc-lite-citation-smoke-") as directory:
        results = _run_in_directory(Path(directory), pdf_dir, download)
        gc.collect()
        return results


def _run_in_directory(directory: Path, pdf_dir: Path | None, download: bool) -> list[dict]:
    db_path = directory / "smoke.db"
    repository = SQLiteRepository(db_path)
    repository.init()
    service = CitationExtractionService(ArtifactService(repository_provider=lambda: repository))
    app = FastAPI()
    app.include_router(
        create_citation_extraction_router(
            service=service,
            request_context_factory=lambda _request, tenant: RequestContext(
                "pinned-paper-smoke", tenant.id, "smoke-admin"
            ),
            repository_provider=lambda: repository,
        )
    )
    app.dependency_overrides[current_admin_user] = lambda: {
        "id": "smoke-admin",
        "tenant_id": "smoke-workspace",
    }
    initial = _counts(db_path)
    results = []
    with TestClient(app) as client:
        for paper in PAPERS:
            pdf = _bytes(paper, pdf_dir, download)
            preview_response = client.post(
                "/api/research/citation-extractions/preview",
                data={"source_uri": paper["url"]},
                files={"file": (paper["name"], pdf, "application/pdf")},
            )
            preview_response.raise_for_status()
            preview = preview_response.json()
            matches = [
                item
                for item in preview["occurrences"]
                if item["anchor"]["quote"] == paper["marker"] and item["style"] == paper["style"]
            ]
            if not matches:
                raise AssertionError(f"Pinned marker not found: {paper['name']}")
            if paper["target_identifier"] and not any(
                match["target_identifier"] is not None
                and match["target_identifier"].casefold() == paper["target_identifier"].casefold()
                and match["status"] == "reference_anchored"
                and match["occurrence_id"] in {item["id"] for item in matches}
                for match in preview["bibliographic_matches"]
            ):
                raise AssertionError("Numeric reference did not match the frozen bibliography")
            commit_response = client.post(
                "/api/research/citation-extractions",
                data={
                    "source_uri": paper["url"],
                    "expected_preview_hash": preview["preview_hash"],
                },
                files={"file": (paper["name"], pdf, "application/pdf")},
            )
            commit_response.raise_for_status()
            committed = commit_response.json()
            artifact = repository.get_artifact("smoke-workspace", committed["artifact_id"])
            bundle = json.loads(artifact["content_text"])
            assert bundle["source_snapshot"]["pdf_sha256"] == paper["sha256"]
            assert bundle["admission_state"] == "candidate_only"
            assert _counts(db_path) == initial
            results.append(
                {
                    "role": paper["role"],
                    "paper": paper["name"],
                    "pdf_sha256": paper["sha256"],
                    "marker_found": True,
                    "occurrence_count": len(preview["occurrences"]),
                    "truncated": preview["truncated"],
                    "reference_anchored_count": sum(
                        item["status"] == "reference_anchored"
                        for item in preview["bibliographic_matches"]
                    ),
                    "authority_delta": {
                        table: _counts(db_path)[table] - initial[table]
                        for table in AUTHORITY_TABLES
                    },
                }
            )
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-dir", type=Path)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.pdf_dir, args.download), ensure_ascii=False, indent=2))
