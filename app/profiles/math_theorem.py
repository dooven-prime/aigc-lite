"""Built-in qualification profile for machine-checked mathematical theorems."""

from __future__ import annotations

from ..core.qualification import (
    QualificationCriterion,
    QualificationProfile,
    ValidationModality,
)

CLAIM_KIND = "MATHEMATICAL_THEOREM"
PROFILE_ID = "math.formal.v1"
PROFILE_VERSION = 1
POLICY_VERSION = "math.formal.policy.v1"
KERNEL_CERTIFICATE_VERSION = "math.kernel-certificate.v1"

MATH_FORMAL_PROFILE = QualificationProfile(
    profile_id=PROFILE_ID,
    version=PROFILE_VERSION,
    claim_kind=CLAIM_KIND,
    policy_version=POLICY_VERSION,
    description=(
        "Qualifies one frozen theorem revision using a content-bound formal proof, "
        "an orthogonal kernel check, closed dependencies, and semantic alignment."
    ),
    accepted_modalities=(
        ValidationModality.KERNEL_CHECK,
        ValidationModality.EXPERT_REVIEW,
    ),
    criteria=(
        QualificationCriterion(
            "statement_identity", "The checked statement matches the frozen revision."
        ),
        QualificationCriterion(
            "formal_artifact_present", "A content-addressed formal proof is present."
        ),
        QualificationCriterion(
            "kernel_check", "A declared proof kernel accepted the formal artifact."
        ),
        QualificationCriterion(
            "undeclared_axioms", "The kernel certificate declares no extra axioms."
        ),
        QualificationCriterion(
            "dependency_closure", "Every theorem dependency is current and qualified."
        ),
        QualificationCriterion(
            "semantic_alignment_review",
            "A non-generative review confirms the formal statement answers the claim.",
        ),
    ),
)
