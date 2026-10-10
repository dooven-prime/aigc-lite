"""Frozen-PDF citation proposals; no epistemic or execution authority writes."""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pypdf
from pydantic import ValidationError

from ..core.artifacts import ArtifactDraft, ArtifactKind
from ..core.citation_extraction import CitationExtractionProposal
from ..core.contracts import RequestContext
from ..core.errors import InvalidArtifactError
from ..redaction import redact_record_text
from .artifacts import ArtifactService

CONTRACT_VERSION = "CitationExtractionProposal.v1"
MAX_PDF_BYTES = 4_000_000
MAX_TEXT_CHARS = 600_000
_NUMERIC = re.compile(r"(?<!\w)\[\s*(\d{1,3})(?:\s*,\s*\d{1,3})*\s*\]")
_AUTHOR_YEAR = re.compile(
    r"\([^()\n]{0,100}\b(?:19|20)\d{2}[a-z]?(?:;[^()\n]{0,100}\b(?:19|20)\d{2}[a-z]?)*\)"
)
_REFERENCE = re.compile(r"(?m)^\[(\d{1,3})\]\s")
_ARXIV = re.compile(r"(?i)(?<!\w)arxiv:\d{4}\.\d{4,5}(?:v\d+)?(?!\w)")
_DOI = re.compile(r"(?i)(?<!\w)10\.\d{4,9}/[^\s<>]+")
_IDENTIFIER = re.compile(r"(?i)(?:arxiv:\d{4}\.\d{4,5}(?:v\d+)?|10\.\d{4,9}/\S+)")
_REFERENCES_HEADER = re.compile(r"(?im)^\s*References\s*$")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _anchor(page: int, text: str, start: int, end: int) -> dict:
    return {"page": page, "start": start, "end": end, "quote": text[start:end]}


def _identifiers(text: str) -> set[str]:
    """Extract whole identifiers; a prefix of a DOI/arXiv ID is never an identity."""
    values = {match.group().casefold() for match in _ARXIV.finditer(text)}
    values.update(match.group().rstrip(".,;:)").casefold() for match in _DOI.finditer(text))
    return values


def _body_status(pages: list[dict], anchor: dict) -> tuple[str, str | None]:
    """Conservatively classify an anchor using the frozen extracted-text layout."""
    headers = {page["page"]: _REFERENCES_HEADER.search(page["text"]) for page in pages}
    first_references_page = next((number for number, hit in headers.items() if hit), None)
    if first_references_page is None:
        return "abstained", "body_region_unconfirmed"
    page_number = anchor["page"]
    if page_number > first_references_page or (
        page_number == first_references_page and anchor["start"] >= headers[page_number].start()
    ):
        return "abstained", "outside_body_region"
    content = next(page["text"] for page in pages if page["page"] == page_number)
    line_start = content.rfind("\n", 0, anchor["start"]) + 1
    line_end = content.find("\n", anchor["end"])
    if line_end == -1:
        line_end = len(content)
    if _REFERENCE.match(content[line_start:line_end]):
        return "abstained", "reference_like_line"
    return "body_anchored", None


class CitationExtractionService:
    def __init__(self, artifact_service: ArtifactService | None = None) -> None:
        self._artifacts = artifact_service or ArtifactService()

    @staticmethod
    def _snapshot(pdf_bytes: bytes, source_uri: str) -> dict:
        try:
            parsed = urlsplit(source_uri)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise InvalidArtifactError("source_uri", "Invalid HTTPS source URI") from exc
        if (
            parsed.scheme != "https"
            or not hostname
            or (port is not None and not 1 <= port <= 65535)
            or parsed.username is not None
            or parsed.password is not None
            or len(source_uri) > 4_000
        ):
            raise InvalidArtifactError(
                "source_uri", "An HTTPS source URI without embedded credentials is required"
            )
        if not pdf_bytes or len(pdf_bytes) > MAX_PDF_BYTES or not pdf_bytes.startswith(b"%PDF-"):
            raise InvalidArtifactError("file", "A nonempty PDF of at most 4 MB is required")
        try:
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes), strict=False)
            if not 1 <= len(reader.pages) <= 30:
                raise InvalidArtifactError("file", "PDF must contain 1–30 pages")
            pages = []
            total = 0
            for number, page in enumerate(reader.pages, start=1):
                content = page.extract_text() or ""
                total += len(content)
                if total > MAX_TEXT_CHARS:
                    raise InvalidArtifactError("file", "Extracted text exceeds 600,000 characters")
                pages.append(
                    {"page": number, "text": content, "text_sha256": _digest(content.encode())}
                )
        except InvalidArtifactError:
            raise
        except Exception as exc:
            raise InvalidArtifactError("file", "PDF text could not be extracted") from exc
        if not any(page["text"].strip() for page in pages):
            raise InvalidArtifactError("file", "PDF has no extractable text; OCR is not inferred")
        return {
            "pdf_sha256": _digest(pdf_bytes),
            "pdf_size_bytes": len(pdf_bytes),
            "source_uri": source_uri,
            "extractor": f"pypdf/{pypdf.__version__}",
            "pages": pages,
        }

    @staticmethod
    def _reference_entries(pages: list[dict]) -> dict[str, list[dict]]:
        references: dict[str, list[dict]] = {}
        inside_references = False
        for page in pages:
            text = page["text"]
            header = _REFERENCES_HEADER.search(text)
            if header:
                inside_references = True
            if inside_references:
                offset = header.end() if header else 0
                entries = list(_REFERENCE.finditer(text, offset))
                for index, match in enumerate(entries):
                    end = entries[index + 1].start() if index + 1 < len(entries) else len(text)
                    entry = _anchor(page["page"], text, match.start(), end)
                    references.setdefault(match.group(1), []).append(entry)
        return references

    @staticmethod
    def _baseline(pages: list[dict]) -> tuple[CitationExtractionProposal, bool]:
        occurrences: list[dict] = []
        truncated = False
        references = CitationExtractionService._reference_entries(pages)
        inside_references = False
        for page in pages:
            text = page["text"]
            header = _REFERENCES_HEADER.search(text)
            body_end = header.start() if header else (0 if inside_references else len(text))
            if header:
                inside_references = True
            for pattern, style in ((_NUMERIC, "numeric"), (_AUTHOR_YEAR, "author_year")):
                for marker in pattern.finditer(text[:body_end]):
                    if len(occurrences) >= 200:
                        truncated = True
                        continue
                    occurrences.append(
                        {
                            "id": f"o-{len(occurrences) + 1}",
                            "anchor": _anchor(page["page"], text, marker.start(), marker.end()),
                            "style": style,
                        }
                    )
        matches = []
        for occurrence in occurrences:
            if occurrence["style"] != "numeric":
                continue
            numeric = re.fullmatch(r"\[\s*(\d{1,3})\s*\]", occurrence["anchor"]["quote"])
            if not numeric or len(references.get(numeric.group(1), [])) != 1:
                continue
            entry = references[numeric.group(1)][0]
            identifiers = _identifiers(entry["quote"])
            matches.append(
                {
                    "id": f"b-{len(matches) + 1}",
                    "occurrence_id": occurrence["id"],
                    "reference_anchor": entry,
                    "target_identifier": next(iter(identifiers)) if len(identifiers) == 1 else None,
                }
            )
        proposal = CitationExtractionProposal.model_validate(
            {
                "contract_version": CONTRACT_VERSION,
                "occurrences": occurrences,
                "bibliographic_matches": matches,
                "claim_relation_proposals": [],
            }
        )
        return proposal, truncated

    @staticmethod
    def _normalize(snapshot: dict, proposal: CitationExtractionProposal) -> dict:
        pages = {page["page"]: page["text"] for page in snapshot["pages"]}
        references = CitationExtractionService._reference_entries(snapshot["pages"])

        def check_anchor(anchor: dict, field: str) -> None:
            content = pages.get(anchor["page"])
            if content is None or anchor["end"] > len(content) or anchor["start"] >= anchor["end"]:
                raise InvalidArtifactError(field, "Anchor is outside the frozen page text")
            if content[anchor["start"] : anchor["end"]] != anchor["quote"]:
                raise InvalidArtifactError(
                    field, "Anchor quote does not match the frozen page text"
                )

        values = proposal.model_dump()
        occurrences = {}
        occurrence_locations: set[tuple[int, int, int]] = set()
        for occurrence in values["occurrences"]:
            if occurrence["id"] in occurrences:
                raise InvalidArtifactError("occurrences", "Duplicate occurrence id")
            check_anchor(occurrence["anchor"], "occurrence.anchor")
            location = (
                occurrence["anchor"]["page"],
                occurrence["anchor"]["start"],
                occurrence["anchor"]["end"],
            )
            if location in occurrence_locations:
                raise InvalidArtifactError("occurrences", "Duplicate occurrence location")
            occurrence_locations.add(location)
            quote = occurrence["anchor"]["quote"]
            if occurrence["style"] == "numeric" and _NUMERIC.fullmatch(quote) is None:
                raise InvalidArtifactError(
                    "occurrence.style", "Numeric style does not match source marker"
                )
            if occurrence["style"] == "author_year" and _AUTHOR_YEAR.fullmatch(quote) is None:
                raise InvalidArtifactError(
                    "occurrence.style", "Author-year style does not match source marker"
                )
            occurrence["status"], occurrence["reason"] = _body_status(
                snapshot["pages"], occurrence["anchor"]
            )
            occurrences[occurrence["id"]] = occurrence
        match_ids: set[str] = set()
        matched_occurrences: set[str] = set()
        for match in values["bibliographic_matches"]:
            if match["id"] in match_ids:
                raise InvalidArtifactError("bibliographic_matches", "Duplicate match id")
            match_ids.add(match["id"])
            occurrence = occurrences.get(match["occurrence_id"])
            if occurrence is None:
                raise InvalidArtifactError(
                    "bibliographic_match.occurrence_id", "Unknown occurrence"
                )
            check_anchor(match["reference_anchor"], "bibliographic_match.reference_anchor")
            marker = re.fullmatch(r"\[\s*(\d{1,3})\s*\]", occurrence["anchor"]["quote"])
            ref = re.match(r"^\[(\d{1,3})\]\s", match["reference_anchor"]["quote"])
            identifier = match["target_identifier"]
            mechanically_matched = (
                occurrence["status"] == "body_anchored"
                and match["occurrence_id"] not in matched_occurrences
                and occurrence["style"] == "numeric"
                and marker is not None
                and ref is not None
                and marker.group(1) == ref.group(1)
                and match["reference_anchor"] in references.get(marker.group(1), [])
                and len(references.get(marker.group(1), [])) == 1
                and (
                    identifier is None
                    or (
                        _IDENTIFIER.fullmatch(identifier) is not None
                        and identifier.casefold()
                        in _identifiers(match["reference_anchor"]["quote"])
                    )
                )
            )
            match["status"] = "reference_anchored" if mechanically_matched else "abstained"
            match["reason"] = (
                None
                if mechanically_matched
                else "duplicate_occurrence_match"
                if match["occurrence_id"] in matched_occurrences
                else "unverified_bibliographic_identity"
            )
            if mechanically_matched:
                matched_occurrences.add(match["occurrence_id"])
            if not mechanically_matched:
                match["submitted_target_identifier"] = identifier
                match["target_identifier"] = None
        relation_ids: set[str] = set()
        for relation in values["claim_relation_proposals"]:
            if relation["id"] in relation_ids:
                raise InvalidArtifactError("claim_relation_proposals", "Duplicate relation id")
            relation_ids.add(relation["id"])
            occurrence = occurrences.get(relation["occurrence_id"])
            if occurrence is None:
                raise InvalidArtifactError("claim_relation.occurrence_id", "Unknown occurrence")
            check_anchor(relation["context_anchor"], "claim_relation.context_anchor")
            anchor = relation["context_anchor"]
            marker = occurrence["anchor"]
            if (
                anchor["page"] != marker["page"]
                or anchor["start"] > marker["start"]
                or anchor["end"] < marker["end"]
            ):
                raise InvalidArtifactError(
                    "claim_relation.context_anchor", "Context must contain its occurrence"
                )
            relation["status"] = "unverified_semantics"
        return values

    def _prepare(
        self, context: RequestContext, pdf_bytes: bytes, source_uri: str, proposal_json: str | None
    ) -> tuple[dict, str]:
        snapshot = self._snapshot(pdf_bytes, source_uri)
        if proposal_json:
            if len(proposal_json) > 200_000:
                raise InvalidArtifactError("proposal", "Proposal exceeds 200,000 characters")
            try:
                proposal = CitationExtractionProposal.model_validate_json(proposal_json)
            except ValidationError as exc:
                raise InvalidArtifactError(
                    "proposal", "Proposal violates CitationExtractionProposal.v1"
                ) from exc
            mode = "submitted_proposal"
        else:
            proposal, truncated = self._baseline(snapshot["pages"])
            mode = "deterministic_baseline"
        if proposal_json:
            truncated = None
        normalized = self._normalize(snapshot, proposal)
        bundle = {
            "contract_version": CONTRACT_VERSION,
            "admission_state": "candidate_only",
            "source_snapshot": snapshot,
            "checker_source_sha256": _digest(Path(__file__).read_bytes()),
            "proposal": normalized,
            "proposal_mode": mode,
            "occurrence_limit": 200,
            "truncated": truncated,
            "submitted_by_principal_id": context.principal_id,
            "limitations": [
                "PDF byte hash is recorded, but the original PDF is not embedded or source-authenticated.",
                "Offsets refer only to extracted Unicode page text; layout/OCR and parser correctness are not verified.",
                "Reference matching is not claim support, entailment, qualification, or knowledge admission.",
                "Proposer model identity is self-reported and is not verifier independence.",
            ],
        }
        content = redact_record_text(_json(bundle))
        if json.loads(content) != bundle:
            raise InvalidArtifactError("file", "Redaction would change frozen citation anchors")
        if len(content) > 1_000_000:
            raise InvalidArtifactError("file", "Frozen proposal exceeds Artifact content limit")
        return bundle, content

    def preview(
        self,
        context: RequestContext,
        pdf_bytes: bytes,
        source_uri: str,
        proposal_json: str | None = None,
    ) -> dict:
        bundle, content = self._prepare(context, pdf_bytes, source_uri, proposal_json)
        proposal = bundle["proposal"]
        return {
            "contract_version": CONTRACT_VERSION,
            "preview_hash": _digest(content.encode()),
            "pdf_sha256": bundle["source_snapshot"]["pdf_sha256"],
            "page_text_sha256": [
                page["text_sha256"] for page in bundle["source_snapshot"]["pages"]
            ],
            "proposal_mode": bundle["proposal_mode"],
            "occurrence_limit": bundle["occurrence_limit"],
            "truncated": bundle["truncated"],
            "occurrences": proposal["occurrences"],
            "bibliographic_matches": proposal["bibliographic_matches"],
            "claim_relation_proposals": proposal["claim_relation_proposals"],
            "admission_state": "candidate_only",
        }

    def commit(
        self,
        context: RequestContext,
        pdf_bytes: bytes,
        source_uri: str,
        expected_preview_hash: str,
        proposal_json: str | None = None,
    ) -> dict:
        bundle, content = self._prepare(context, pdf_bytes, source_uri, proposal_json)
        if _digest(content.encode()) != expected_preview_hash:
            raise InvalidArtifactError(
                "expected_preview_hash", "Frozen source or proposal changed since preview"
            )
        artifact = self._artifacts.create_artifact(
            context,
            ArtifactDraft(
                name="CitationExtractionProposal.v1",
                kind=ArtifactKind.JSON,
                media_type="application/json",
                content_text=content,
                metadata={
                    "contract_version": CONTRACT_VERSION,
                    "admission_state": "candidate_only",
                    "pdf_sha256": bundle["source_snapshot"]["pdf_sha256"],
                    "preview_hash": expected_preview_hash,
                },
            ),
        )
        return {
            "artifact_id": artifact["id"],
            "preview_hash": expected_preview_hash,
            "pdf_sha256": bundle["source_snapshot"]["pdf_sha256"],
            "admission_state": "candidate_only",
            "qualification_receipt_id": None,
            "current_use_binding_id": None,
            "authorization_grant_id": None,
        }
