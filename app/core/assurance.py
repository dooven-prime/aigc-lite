"""Portable assurance and verification-independence contracts.

Assurance is intentionally a vector, never a scalar score.  These contracts
describe disclosed relationships; domain profiles remain responsible for
deciding which verification methods are scientifically meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

ASSURANCE_BUNDLE_VERSION = "assurance.bundle.v1"
INDEPENDENCE_CONTRACT_VERSION = "verification.independence.v1"


class OverlapAssessment(StrEnum):
    UNKNOWN = "unknown"
    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"
    NOT_APPLICABLE = "not_applicable"


class ConflictAssessment(StrEnum):
    UNKNOWN = "unknown"
    ABSENT = "absent"
    PRESENT = "present"
    NOT_APPLICABLE = "not_applicable"


class IndependenceBasis(StrEnum):
    DIFFERENT_MODEL_FAMILY = "different_model_family"
    DIFFERENT_PROVIDER = "different_provider"
    INDEPENDENT_SOURCE_RETRIEVAL = "independent_source_retrieval"
    SEPARATE_VALIDATOR = "separate_validator"
    NO_SHARED_GENERATION_CONTEXT = "no_shared_generation_context"
    SEPARATE_TOOLCHAIN = "separate_toolchain"
    HUMAN_INDEPENDENT_REVIEW = "human_independent_review"


@dataclass(frozen=True, slots=True)
class VerificationIndependence:
    """Disclosed overlap between generation and verification contexts.

    A declaration qualifies for the generic promotion gate only when it names
    a separate validator, discloses no shared author or hidden generation
    state, and declares no human conflict of interest.  Model/provider/source
    overlap remains visible but is not collapsed into a score.
    """

    model_family_overlap: OverlapAssessment = OverlapAssessment.UNKNOWN
    provider_overlap: OverlapAssessment = OverlapAssessment.UNKNOWN
    prompt_overlap: OverlapAssessment = OverlapAssessment.UNKNOWN
    evidence_overlap: OverlapAssessment = OverlapAssessment.UNKNOWN
    toolchain_overlap: OverlapAssessment = OverlapAssessment.UNKNOWN
    author_overlap: OverlapAssessment = OverlapAssessment.UNKNOWN
    hidden_state_overlap: OverlapAssessment = OverlapAssessment.UNKNOWN
    human_conflict_of_interest: ConflictAssessment = ConflictAssessment.UNKNOWN
    basis: tuple[IndependenceBasis, ...] = ()
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": INDEPENDENCE_CONTRACT_VERSION,
            "model_family_overlap": self.model_family_overlap.value,
            "provider_overlap": self.provider_overlap.value,
            "prompt_overlap": self.prompt_overlap.value,
            "evidence_overlap": self.evidence_overlap.value,
            "toolchain_overlap": self.toolchain_overlap.value,
            "author_overlap": self.author_overlap.value,
            "hidden_state_overlap": self.hidden_state_overlap.value,
            "human_conflict_of_interest": self.human_conflict_of_interest.value,
            "basis": [item.value for item in self.basis],
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(cls, value: object) -> VerificationIndependence:
        if not isinstance(value, dict):
            return cls()

        def overlap(name: str) -> OverlapAssessment:
            try:
                return OverlapAssessment(value.get(name, OverlapAssessment.UNKNOWN.value))
            except (TypeError, ValueError):
                return OverlapAssessment.UNKNOWN

        try:
            conflict = ConflictAssessment(
                value.get("human_conflict_of_interest", ConflictAssessment.UNKNOWN.value)
            )
        except (TypeError, ValueError):
            conflict = ConflictAssessment.UNKNOWN
        raw_bases = value.get("basis", [])
        if not isinstance(raw_bases, list):
            raw_bases = []
        bases = []
        for item in raw_bases:
            try:
                bases.append(IndependenceBasis(item))
            except (TypeError, ValueError):
                continue
        raw_limitations = value.get("limitations", [])
        if not isinstance(raw_limitations, list):
            raw_limitations = []
        limitations = tuple(item for item in raw_limitations if isinstance(item, str))
        return cls(
            model_family_overlap=overlap("model_family_overlap"),
            provider_overlap=overlap("provider_overlap"),
            prompt_overlap=overlap("prompt_overlap"),
            evidence_overlap=overlap("evidence_overlap"),
            toolchain_overlap=overlap("toolchain_overlap"),
            author_overlap=overlap("author_overlap"),
            hidden_state_overlap=overlap("hidden_state_overlap"),
            human_conflict_of_interest=conflict,
            basis=tuple(dict.fromkeys(bases)),
            limitations=limitations,
        )

    def qualification(self) -> tuple[bool, tuple[str, ...]]:
        """Return the generic gate result and explicit failure reasons."""

        reasons: list[str] = []
        basis = set(self.basis)
        if IndependenceBasis.SEPARATE_VALIDATOR not in basis:
            reasons.append("separate_validator_not_declared")
        if IndependenceBasis.NO_SHARED_GENERATION_CONTEXT not in basis:
            reasons.append("no_shared_generation_context_not_declared")
        if self.author_overlap is not OverlapAssessment.NONE:
            reasons.append("author_separation_not_established")
        if self.hidden_state_overlap not in {
            OverlapAssessment.NONE,
            OverlapAssessment.NOT_APPLICABLE,
        }:
            reasons.append("hidden_state_separation_not_established")
        if self.human_conflict_of_interest not in {
            ConflictAssessment.ABSENT,
            ConflictAssessment.NOT_APPLICABLE,
        }:
            reasons.append("human_conflict_of_interest_not_cleared")
        return not reasons, tuple(reasons)


def unassessed_independence(*limitations: str) -> VerificationIndependence:
    return VerificationIndependence(limitations=tuple(limitations))
