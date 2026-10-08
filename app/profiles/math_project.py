"""A deliberately narrow, project-closed Lean qualification profile."""

from __future__ import annotations

from ..core.qualification import QualificationCriterion, QualificationProfile, ValidationModality
from .math_theorem import CLAIM_KIND

PROFILE_ID = "math.formal.project.v2"
PROJECT_RECEIPT_VERSION = "math.project-verification.v1"
SNAPSHOT_VERSION = "math.source-snapshot.v1"

MATH_PROJECT_PROFILE = QualificationProfile(
    profile_id=PROFILE_ID,
    version=2,
    claim_kind=CLAIM_KIND,
    policy_version="math.formal.project.policy.v2",
    description=(
        "A frozen theorem, pinned source bytes, clean project/dependency closure, "
        "Comparator statement identity and Lean kernel acceptance. Qualification "
        "does not admit the theorem into current knowledge."
    ),
    accepted_modalities=(ValidationModality.KERNEL_CHECK, ValidationModality.EXPERT_REVIEW),
    criteria=tuple(
        QualificationCriterion(code, description)
        for code, description in (
            (
                "source_snapshot",
                "The PDF, challenge, solution, and build inputs are content bound.",
            ),
            ("statement_identity", "Comparator checked the frozen challenge against the solution."),
            ("kernel_check", "The Lean kernel accepted that exact solution declaration."),
            ("dependency_closure", "Every project dependency matches its pinned revision."),
            (
                "semantic_alignment_review",
                "A non-model reviewer checked paper-to-challenge meaning.",
            ),
        )
    ),
)
