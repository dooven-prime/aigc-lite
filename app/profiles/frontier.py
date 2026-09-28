"""AI Frontier research-profile vocabulary.

These enums are intentionally outside the shared research core: causal status,
uncertainty classes, and the supported claim taxonomy belong to this profile.
"""

from __future__ import annotations

from enum import StrEnum

PROFILE_ID = "research.frontier"
IMPORTER_VERSION = "frontier-registry-import-v1"
PROMOTION_POLICY_VERSION = "frontier-promotion-gate-v1"


class FrontierClaimType(StrEnum):
    OBSERVATION = "OBSERVATION"
    DERIVED_MEASURE = "DERIVED_MEASURE"
    COMPARATIVE_FINDING = "COMPARATIVE_FINDING"
    CONDITIONAL_SYSTEM_HYPOTHESIS = "CONDITIONAL_SYSTEM_HYPOTHESIS"
    RESEARCH_CONCEPT = "RESEARCH_CONCEPT"


class FrontierCausalStatus(StrEnum):
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    TEMPORAL_ORDER_ONLY = "TEMPORAL_ORDER_ONLY"
    CAUSAL_EVIDENCE_PENDING = "CAUSAL_EVIDENCE_PENDING"


class FrontierUncertaintyStatus(StrEnum):
    OBSERVED = "observed"
    DERIVED_WITH_UNCERTAINTY = "derived_with_uncertainty"
    PARTIAL_COVERAGE = "partial_coverage"
    NOT_COMPARABLE = "not_comparable"
    MAPPING_PENDING = "mapping_pending"
    NOT_DISCLOSED = "not_disclosed"


CLAIM_TYPES = frozenset(item.value for item in FrontierClaimType)
CAUSAL_STATUSES = frozenset(item.value for item in FrontierCausalStatus)
UNCERTAINTY_STATUSES = frozenset(item.value for item in FrontierUncertaintyStatus)
