"""Transport-neutral contracts for advisory review findings.

Review is deliberately separate from qualification and authorization.  A
finding can point at evidence and suggest work, but it cannot mutate semantic
authority, current-use bindings, or execution grants.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .qualification import canonical_hash

REVIEW_CONTRACT_VERSION = "review.workbench.v1"
REVIEWER_LINEAGE_VERSION = "reviewer.lineage.v1"


class ReviewSeverity(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    INFO = "INFO"


class ReviewFindingOrigin(StrEnum):
    DETERMINISTIC_RULE = "deterministic_rule"
    AGENT_SUGGESTION = "agent_suggestion"
    HUMAN_REVIEW = "human_review"


class ReviewFindingStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class ReviewRule:
    rule_id: str
    severity: ReviewSeverity
    description: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class ReviewProfile:
    profile_id: str
    version: int
    subject_type: str
    description: str
    rules: tuple[ReviewRule, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": REVIEW_CONTRACT_VERSION,
            "profile_id": self.profile_id,
            "version": self.version,
            "subject_type": self.subject_type,
            "description": self.description,
            "rules": [rule.as_dict() for rule in self.rules],
        }

    @property
    def content_hash(self) -> str:
        return canonical_hash(self.as_dict())


@dataclass(frozen=True, slots=True)
class ReviewFindingDraft:
    rule_id: str
    severity: ReviewSeverity
    title: str
    summary: str
    suggestion: str
    evidence_refs: tuple[dict[str, str], ...]
    origin: ReviewFindingOrigin = ReviewFindingOrigin.DETERMINISTIC_RULE

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "origin": self.origin.value,
            "title": self.title,
            "summary": self.summary,
            "suggestion": self.suggestion,
            "evidence_refs": list(self.evidence_refs),
        }
