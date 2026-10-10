"""Candidate-only citation extraction transport contract (v1).

Offsets address Unicode characters in the server-extracted page text, never
byte offsets in the PDF. These objects are proposals, not Citation/EvidenceEdge
or accepted ClaimRelation records.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextAnchor(_StrictModel):
    page: int = Field(ge=1, le=30)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=2_000)


class CitationOccurrenceCandidate(_StrictModel):
    id: str = Field(min_length=1, max_length=80)
    anchor: TextAnchor
    style: Literal["numeric", "author_year", "other"]


class BibliographicMatchCandidate(_StrictModel):
    id: str = Field(min_length=1, max_length=80)
    occurrence_id: str = Field(min_length=1, max_length=80)
    reference_anchor: TextAnchor
    target_identifier: str | None = Field(default=None, max_length=255)


class ClaimRelationProposal(_StrictModel):
    id: str = Field(min_length=1, max_length=80)
    occurrence_id: str = Field(min_length=1, max_length=80)
    context_anchor: TextAnchor
    target_claim_revision_id: str | None = Field(default=None, max_length=100)
    relation_hint: Literal["mentions", "possibly_supports", "possibly_contradicts", "related"]


class CitationExtractionProposal(_StrictModel):
    contract_version: Literal["CitationExtractionProposal.v1"]
    occurrences: list[CitationOccurrenceCandidate] = Field(default_factory=list, max_length=200)
    bibliographic_matches: list[BibliographicMatchCandidate] = Field(
        default_factory=list, max_length=200
    )
    claim_relation_proposals: list[ClaimRelationProposal] = Field(
        default_factory=list, max_length=100
    )
    proposer_model_route: str | None = Field(default=None, max_length=255)
    proposer_prompt_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
