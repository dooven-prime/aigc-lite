"""Local, prediction-blind visual citation annotation helper.

The human reviews the PDF and selects a citation string; this helper only maps
that selected string into the frozen pypdf text and writes a draft Gold file.
It never runs citation predictions or changes the evaluation manifest.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from app.services.citation_extraction import CitationExtractionService
from scripts.citation_blind_eval import (
    CITATION_STYLES,
    SCHEMA,
    _check_code,
    _check_manifest,
    _digest,
    _read,
)

HTML_SOURCE = Path(__file__).with_suffix(".html")
PROTOCOL = "visual-page-review-assisted-v1"


def _helper_hashes() -> dict[str, str]:
    return {
        "annotation_helper_sha256": _digest(Path(__file__).read_bytes()),
        "annotation_ui_sha256": _digest(HTML_SOURCE.read_bytes()),
    }


def _normalized_with_offsets(value: str) -> tuple[str, list[tuple[int, int]]]:
    """Collapse layout whitespace and line-wrap hyphens, retaining byte-free indices."""
    chars: list[str] = []
    offsets: list[tuple[int, int]] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "-" and index > 0 and value[index - 1].isalpha():
            following = index + 1
            if following < len(value) and value[following] in "\r\n":
                while following < len(value) and value[following] in "\r\n":
                    following += 1
                if following < len(value) and value[following].isalpha():
                    index = following
                    continue
        if char.isspace():
            end = index + 1
            while end < len(value) and value[end].isspace():
                end += 1
            if chars and chars[-1] != " ":
                chars.append(" ")
                offsets.append((index, end))
            index = end
            continue
        chars.append(char.lower())
        offsets.append((index, index + 1))
        index += 1
    if chars and chars[-1] == " ":
        chars.pop()
        offsets.pop()
    return "".join(chars), offsets


def find_text_matches(page: int, text: str, visual_quote: str) -> list[dict]:
    """Return exact source spans for a human-selected visual string, never predictions."""
    needle, _ = _normalized_with_offsets(visual_quote.strip())
    if len(needle) < 3 or len(needle) > 500:
        raise ValueError("Select a citation string of 3–500 normalized characters")
    haystack, offsets = _normalized_with_offsets(text)
    matches: list[dict] = []
    position = 0
    while (position := haystack.find(needle, position)) >= 0:
        start = offsets[position][0]
        end = offsets[position + len(needle) - 1][1]
        matches.append(
            {
                "anchor": {"page": page, "start": start, "end": end, "quote": text[start:end]},
                "context": text[max(0, start - 55) : min(len(text), end + 55)],
            }
        )
        if len(matches) > 30:
            raise ValueError("Too many matches; select a longer citation string")
        position += 1
    return matches


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class GoldDraft:
    def __init__(
        self, manifest_path: Path, pdf_dir: Path, draft_path: Path, annotator_id: str
    ) -> None:
        self.manifest = _read(manifest_path)
        _check_manifest(self.manifest)
        _check_code(self.manifest)
        if not annotator_id.strip():
            raise ValueError("A human annotator ID is required")
        self.draft_path = draft_path
        self.pdfs: dict[str, bytes] = {}
        self.pages: dict[str, dict[int, str]] = {}
        for paper in self.manifest["papers"]:
            paper_id = paper["paper_id"]
            content = (pdf_dir / f"{paper_id}.pdf").read_bytes()
            if _digest(content) != paper["pdf_sha256"]:
                raise ValueError(f"{paper_id}: PDF digest differs from frozen manifest")
            snapshot = CitationExtractionService._snapshot(content, paper["source_uri"])
            self.pdfs[paper_id] = content
            self.pages[paper_id] = {item["page"]: item["text"] for item in snapshot["pages"]}
        if draft_path.exists():
            self.gold = _read(draft_path)
            if (
                self.gold.get("manifest_sha256") != self.manifest["manifest_sha256"]
                or self.gold.get("annotator_id") != annotator_id
                or self.gold.get("annotation_protocol") != PROTOCOL
                or any(self.gold.get(key) != digest for key, digest in _helper_hashes().items())
                or [item["paper_id"] for item in self.gold.get("papers", [])]
                != [item["paper_id"] for item in self.manifest["papers"]]
            ):
                raise ValueError("Existing draft belongs to a different cohort or annotator")
        else:
            self.gold = {
                "schema": SCHEMA,
                "manifest_sha256": self.manifest["manifest_sha256"],
                "annotator_id": annotator_id,
                "annotation_protocol": PROTOCOL,
                **_helper_hashes(),
                "papers": [
                    {
                        "paper_id": paper["paper_id"],
                        "exhaustive": False,
                        "reviewed_pages": [],
                        "occurrences": [],
                    }
                    for paper in self.manifest["papers"]
                ],
            }
            _atomic_json(draft_path, self.gold)

    def _paper(self, paper_id: str) -> dict:
        for paper in self.gold["papers"]:
            if paper["paper_id"] == paper_id:
                return paper
        raise ValueError("Unknown paper ID")

    def _page(self, paper_id: str, page: int) -> str:
        try:
            return self.pages[paper_id][page]
        except KeyError as exc:
            raise ValueError("Unknown paper or page") from exc

    def state(self) -> dict:
        return {
            "manifest_sha256": self.manifest["manifest_sha256"],
            "draft_path": str(self.draft_path),
            "papers": [
                {
                    **paper,
                    "page_count": len(self.pages[paper["paper_id"]]),
                }
                for paper in self.gold["papers"]
            ],
        }

    def page(self, paper_id: str, page: int) -> dict:
        return {"paper_id": paper_id, "page": page, "text": self._page(paper_id, page)}

    def matches(self, paper_id: str, page: int, visual_quote: str) -> list[dict]:
        return find_text_matches(page, self._page(paper_id, page), visual_quote)

    def add_occurrence(
        self,
        paper_id: str,
        page: int,
        visual_quote: str,
        style: str,
        candidate_index: int | None,
        unmappable_reason: str | None = None,
    ) -> dict:
        paper = self._paper(paper_id)
        if style not in CITATION_STYLES:
            raise ValueError("Choose a citation style")
        visual_quote = visual_quote.strip()
        matches = self.matches(paper_id, page, visual_quote)
        if candidate_index is None:
            if not unmappable_reason or len(unmappable_reason.strip()) < 12:
                raise ValueError("Unmapped citations require a specific human reason")
            if matches and not unmappable_reason.startswith("rejected_candidates:"):
                raise ValueError("Confirm why all available text matches were rejected")
            anchor = None
            quote = visual_quote
        else:
            if type(candidate_index) is not int or not 0 <= candidate_index < len(matches):
                raise ValueError("Select one of the displayed source-text matches")
            anchor = matches[candidate_index]["anchor"]
            quote = anchor["quote"]
            unmappable_reason = None
        if any(
            (anchor is not None and item.get("anchor") == anchor)
            or (anchor is None and item.get("anchor") is None and item["page"] == page
                and item.get("visual_quote") == visual_quote)
            for item in paper["occurrences"]
        ):
            raise ValueError("This citation occurrence is already recorded")
        occurrence = {
            "annotation_id": str(uuid4()),
            "page": page,
            "quote": quote,
            "visual_quote": visual_quote,
            "style": style,
            "anchor": anchor,
            "reference_anchor": None,
            "target_identifier": None,
            "unmappable_reason": unmappable_reason,
        }
        paper["occurrences"].append(occurrence)
        self._unreview_page(paper, page)
        _atomic_json(self.draft_path, self.gold)
        return occurrence

    def delete_occurrence(self, paper_id: str, annotation_id: str) -> None:
        paper = self._paper(paper_id)
        for item in paper["occurrences"]:
            if item["annotation_id"] == annotation_id:
                paper["occurrences"].remove(item)
                self._unreview_page(paper, item["page"])
                _atomic_json(self.draft_path, self.gold)
                return
        raise ValueError("Unknown annotation ID")

    @staticmethod
    def _unreview_page(paper: dict, page: int) -> None:
        paper["reviewed_pages"] = [value for value in paper["reviewed_pages"] if value != page]
        paper["exhaustive"] = False

    def review_page(self, paper_id: str, page: int, reviewed: bool) -> dict:
        self._page(paper_id, page)
        if type(reviewed) is not bool:
            raise ValueError("Page review state must be boolean")
        paper = self._paper(paper_id)
        pages = set(paper["reviewed_pages"])
        if reviewed:
            pages.add(page)
        else:
            pages.discard(page)
        paper["reviewed_pages"] = sorted(pages)
        paper["exhaustive"] = pages == set(self.pages[paper_id])
        _atomic_json(self.draft_path, self.gold)
        return {"reviewed_pages": paper["reviewed_pages"], "exhaustive": paper["exhaustive"]}


def serve(draft: GoldDraft, host: str, port: int) -> None:
    if host != "127.0.0.1":
        raise ValueError("The annotation helper only binds 127.0.0.1")
    token = secrets.token_hex(24)

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, value: dict | list) -> None:
            content = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def _send(self, content: bytes, mime: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            try:
                if path == "/":
                    html = HTML_SOURCE.read_text(encoding="utf-8").replace(
                        "__LOCAL_TOKEN__", token
                    )
                    self._send(html.encode("utf-8"), "text/html; charset=utf-8")
                elif path == "/api/state":
                    self._json(200, draft.state())
                elif path.startswith("/api/page/"):
                    _, _, _, paper_id, page = path.split("/", 4)
                    self._json(200, draft.page(unquote(paper_id), int(page)))
                elif path.startswith("/pdf/"):
                    paper_id = unquote(path.removeprefix("/pdf/"))
                    if paper_id not in draft.pdfs:
                        raise ValueError("Unknown paper ID")
                    self._send(draft.pdfs[paper_id], "application/pdf")
                else:
                    self._json(404, {"error": "Not found"})
            except (ValueError, KeyError) as exc:
                self._json(400, {"error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            if self.headers.get("X-Annotation-Token") != token:
                self._json(403, {"error": "Invalid local annotation token"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 10_000:
                    raise ValueError("Invalid request length")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object")
                path = urlsplit(self.path).path
                if path == "/api/matches":
                    result = draft.matches(data["paper_id"], int(data["page"]), data["visual_quote"])
                elif path == "/api/occurrences":
                    result = draft.add_occurrence(
                        data["paper_id"],
                        int(data["page"]),
                        data["visual_quote"],
                        data["style"],
                        data.get("candidate_index"),
                        data.get("unmappable_reason"),
                    )
                elif path == "/api/delete":
                    draft.delete_occurrence(data["paper_id"], data["annotation_id"])
                    result = {"deleted": True}
                elif path == "/api/review-page":
                    result = draft.review_page(data["paper_id"], int(data["page"]), data["reviewed"])
                else:
                    self._json(404, {"error": "Not found"})
                    return
                self._json(200, result)
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})

    server = HTTPServer((host, port), Handler)
    print(f"Local citation Gold annotator: http://{host}:{server.server_port}/")
    print("Only PDF bytes and frozen pypdf text are shown; no extractor predictions are loaded.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Local citation Gold annotator stopped.")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--annotator-id", required=True)
    parser.add_argument("--port", type=int, default=8014)
    args = parser.parse_args()
    draft = GoldDraft(args.manifest, args.pdf_dir, args.draft, args.annotator_id)
    serve(draft, "127.0.0.1", args.port)


if __name__ == "__main__":
    main()
