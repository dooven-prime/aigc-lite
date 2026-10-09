"""Parse a pinned OpenAI math catalogue into candidate-only records.

This adapter does not download PDFs, run Lean, or assert mathematical truth.
The upstream commit is fetched from fixed paths on a fixed host; every byte
that is actually imported is hashed separately from the asserted Git commit.
"""

from __future__ import annotations

import hashlib
import html
import re
from typing import Any

import httpx

from ...core.errors import InvalidEvidenceError, UpstreamRequestError
from ...core.qualification import canonical_hash

CONTRACT_VERSION = "research.openai-math-catalog.v1"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_FAMILY = re.compile(r"(?m)^\*\*(\d{3})\.\s+(.+?)\*\*")
_DECLARED_COUNTS = re.compile(
    r"\*\*(\d+) manuscripts covering (\d+) result families\.\*\*"
)
_LEAN_DOC = re.compile(r"\[Lean\]\((lean/docs/\d{3}\.md)\)")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_MAX_CONTENTS_BYTES = 2_000_000
_MAX_FORMALIZATION_BYTES = 1_000_000


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _plain(value: str) -> str:
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", value))).strip()


def _manuscript_link(chunk: str, start: int) -> tuple[str, str, int]:
    title_end = chunk.find("](", start + len("&emsp;["))
    if title_end < 0:
        raise InvalidEvidenceError("contents", "Malformed manuscript link")
    title = _plain(chunk[start + len("&emsp;[") : title_end])
    path_start = title_end + 2
    depth = 1
    position = path_start
    while position < len(chunk) and depth:
        if chunk[position] == "(":
            depth += 1
        elif chunk[position] == ")":
            depth -= 1
        position += 1
    if depth:
        raise InvalidEvidenceError("contents", "Unterminated manuscript link")
    path = chunk[path_start : position - 1]
    if (
        not path.startswith("preprints/")
        or not path.endswith(".pdf")
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or "\\" in path
    ):
        raise InvalidEvidenceError("contents", "Invalid manuscript source path")
    return title, path, position


class OpenAIMathReleaseAdapter:
    """Fetch and normalize only the upstream catalogue at an exact Git commit."""

    importer_id = "openai.math"
    version = 1
    display_name = "OpenAI mathematics catalogue"
    description = "Import a pinned catalogue manifest without creating theorem claims."
    source_format = "fixed-commit CONTENTS.md + lean/formalization.yaml"
    target_surface = "catalogue_candidate_only"
    preview_endpoint = "/api/research/math-release-imports/preview"
    commit_endpoint = "/api/research/math-release-imports"
    importer_contract = CONTRACT_VERSION

    def fetch(self, source_commit: str) -> tuple[bytes, bytes]:
        self._validate_commit(source_commit)
        base = f"https://raw.githubusercontent.com/openai/math/{source_commit}"
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            contents = self._fetch_file(client, f"{base}/CONTENTS.md", _MAX_CONTENTS_BYTES)
            formalization = self._fetch_file(
                client, f"{base}/lean/formalization.yaml", _MAX_FORMALIZATION_BYTES
            )
        return contents, formalization

    @staticmethod
    def _fetch_file(client: httpx.Client, url: str, maximum: int) -> bytes:
        try:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise UpstreamRequestError("Pinned research catalogue is unavailable")
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > maximum:
                        raise InvalidEvidenceError("source", "Research catalogue exceeds size limit")
                return bytes(payload)
        except httpx.HTTPError as exc:
            raise UpstreamRequestError("Pinned research catalogue fetch failed") from exc

    @staticmethod
    def _validate_commit(source_commit: str) -> None:
        if not _COMMIT.fullmatch(source_commit):
            raise InvalidEvidenceError("source_commit", "An exact 40-character Git SHA is required")

    def parse(
        self,
        source_commit: str,
        contents_bytes: bytes,
        formalization_bytes: bytes,
    ) -> dict[str, Any]:
        self._validate_commit(source_commit)
        if not contents_bytes or len(contents_bytes) > _MAX_CONTENTS_BYTES:
            raise InvalidEvidenceError("contents", "CONTENTS.md is empty or too large")
        if not formalization_bytes or len(formalization_bytes) > _MAX_FORMALIZATION_BYTES:
            raise InvalidEvidenceError(
                "formalization", "formalization.yaml is empty or too large"
            )
        try:
            contents = contents_bytes.decode("utf-8-sig")
            formalization_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InvalidEvidenceError("source", "Catalogue files must be UTF-8") from exc
        declared = _DECLARED_COUNTS.search(contents)
        if declared is None:
            raise InvalidEvidenceError("contents", "Catalogue count declaration is missing")
        matches = list(_FAMILY.finditer(contents))
        if not matches or len(matches) > 2_000:
            raise InvalidEvidenceError("contents", "No bounded result-family catalogue found")
        families = []
        seen_family_ids: set[str] = set()
        seen_paths: set[str] = set()
        for ordinal, match in enumerate(matches):
            family_id = match.group(1)
            if family_id in seen_family_ids:
                raise InvalidEvidenceError("contents", "Duplicate result-family identifier")
            seen_family_ids.add(family_id)
            end = matches[ordinal + 1].start() if ordinal + 1 < len(matches) else len(contents)
            chunk = contents[match.start() : end]
            heading_end = chunk.find("</td>")
            if heading_end < 0:
                raise InvalidEvidenceError("contents", "Result family has no closing cell")
            title = _plain(match.group(2))
            summary = _plain(chunk[match.end() - match.start() : heading_end])
            if not title or not summary:
                raise InvalidEvidenceError("contents", "Result family lacks a catalogue summary")
            if len(title) > 500 or len(summary) > 20_000:
                raise InvalidEvidenceError("contents", "Result-family text exceeds limit")
            manuscripts = []
            for link in re.finditer(r"&emsp;\[", chunk):
                manuscript_title, path, after_link = _manuscript_link(chunk, link.start())
                if path in seen_paths:
                    raise InvalidEvidenceError("contents", "Duplicate manuscript source path")
                seen_paths.add(path)
                cell_end = chunk.find("</td>", after_link)
                if cell_end < 0:
                    raise InvalidEvidenceError("contents", "Manuscript has no closing cell")
                abstract = _plain(chunk[after_link:cell_end])
                if not manuscript_title or len(manuscript_title) > 500 or len(abstract) > 20_000:
                    raise InvalidEvidenceError("contents", "Manuscript text exceeds limit")
                manuscripts.append(
                    {
                        "ordinal": len(manuscripts),
                        "title": manuscript_title,
                        "path": path,
                        "abstract": abstract,
                        "admission_state": "candidate",
                        "artifact_integrity": "not_downloaded",
                    }
                )
            if not manuscripts:
                raise InvalidEvidenceError("contents", "Result family has no manuscripts")
            lean_docs = sorted(set(_LEAN_DOC.findall(chunk[:heading_end])))
            families.append(
                {
                    "external_family_id": family_id,
                    "ordinal": ordinal,
                    "title": title,
                    "catalog_claim_candidate": summary,
                    "catalog_claim_text_hash": _digest(summary.encode("utf-8")),
                    "claim_semantics": "catalog_summary_not_frozen_theorem",
                    "admission_state": "candidate",
                    "lean_documentation_paths": lean_docs,
                    "manuscripts": manuscripts,
                }
            )
        manifest = {
            "contract_version": CONTRACT_VERSION,
            "source_repository": "openai/math",
            "source_commit": source_commit,
            "authority": "candidate_only",
            "formalization_catalog_status": "raw_source_only_not_verified",
            "families": families,
        }
        manuscript_count = sum(len(item["manuscripts"]) for item in families)
        if (manuscript_count, len(families)) != (
            int(declared.group(1)),
            int(declared.group(2)),
        ):
            raise InvalidEvidenceError(
                "contents", "Parsed catalogue does not match declared counts"
            )
        contents_hash = _digest(contents_bytes)
        formalization_hash = _digest(formalization_bytes)
        manifest_hash = canonical_hash(manifest)
        preview_hash = canonical_hash(
            {
                "contract_version": CONTRACT_VERSION,
                "source_commit": source_commit,
                "contents_hash": contents_hash,
                "formalization_hash": formalization_hash,
                "manifest_hash": manifest_hash,
            }
        )
        return {
            "contract_version": CONTRACT_VERSION,
            "source_commit": source_commit,
            "contents_hash": contents_hash,
            "formalization_hash": formalization_hash,
            "manifest_hash": manifest_hash,
            "preview_hash": preview_hash,
            "family_count": len(families),
            "manuscript_count": manuscript_count,
            "lean_linked_family_count": sum(
                bool(item["lean_documentation_paths"]) for item in families
            ),
            "manifest": manifest,
        }
