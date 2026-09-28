"""Decision-profile contracts kept outside the cross-domain core."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DecisionResolution(StrEnum):
    DECIDED = "decided"
    MANUAL_REVIEW = "manual_review"
    UNDETERMINED = "undetermined"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class DecisionCaseDraft:
    source_case_id: str
    family: str
    state_text: str
    questions: dict
    answers: dict
    resolution: DecisionResolution
    confidence: float
    entropy: float
    threshold: float
    run_id: str | None = None
    step_id: str | None = None
