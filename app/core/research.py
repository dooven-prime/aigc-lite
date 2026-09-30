"""Cross-domain records for evidence-backed research registries.

Domain profiles own the meaning of claim types and status axes.  The core only
defines the stable envelope needed to version a claim, bind it to sources, and
report whether its declared source closure is complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .assurance import VerificationIndependence
from .qualification import ValidationModality


class ResearchCaseStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    FROZEN = "frozen"
    WITHDRAWN = "withdrawn"


class ClaimClosureStatus(StrEnum):
    CLOSED = "closed"
    BLOCKED = "blocked"


class ClaimRelationType(StrEnum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    DEPENDS_ON = "depends_on"
    QUALIFIES = "qualifies"


class ClaimRelationStatus(StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


class VerificationKind(StrEnum):
    SOURCE_AUDIT = "source_audit"
    REPRODUCTION = "reproduction"
    CALCULATION = "calculation"
    EXPERIMENT = "experiment"
    REVIEW = "review"


class VerificationOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


class VerificationPlanStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class VerificationExecutor(StrEnum):
    AGENT = "agent"


class VerificationExecutionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INVALID_OUTPUT = "invalid_output"


class ClaimPromotionStage(StrEnum):
    REGISTERED = "registered"
    EVIDENCE_READY = "evidence_ready"
    REVIEW_READY = "review_ready"
    RELEASE_READY = "release_ready"
    WITHDRAWN = "withdrawn"


class PromotionGateDecision(StrEnum):
    PASSED = "passed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ResearchCaseDraft:
    name: str
    profile: str
    registry_id: str
    registry_version: str
    authority: str
    status: ResearchCaseStatus
    protocol_id: str
    receipt_id: str
    source_artifact_id: str
    source_ledger_artifact_id: str | None = None
    as_of_date: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceReferenceDraft:
    """A source-addressed witness declared by an imported registry."""

    ref_key: str
    source_key: str
    locator: str
    content_hash: str
    status: str = "declared"
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ClaimRevisionDraft:
    """A versioned claim envelope whose status axes are profile-owned."""

    claim_key: str
    statement: str
    claim_type: str
    scope: str
    method_revision: str
    lifecycle_status: str
    closure_status: ClaimClosureStatus
    status_axes: dict = field(default_factory=dict)
    blockers: tuple[str, ...] = ()
    source_ref_keys: tuple[str, ...] = ()
    revision_number: int = 1


@dataclass(frozen=True, slots=True)
class ClaimRelationDraft:
    target_claim_id: str
    relation_type: ClaimRelationType
    rationale: str
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VerificationAttemptDraft:
    kind: VerificationKind
    outcome: VerificationOutcome
    method: str
    scope: str
    input_digest: str
    output_digest: str
    validation_modality: ValidationModality = ValidationModality.AGENT_REVIEW
    # Deprecated internal compatibility fields. Transport clients cannot set
    # these; the service derives both projections from server-owned lineage.
    independence: VerificationIndependence = field(default_factory=VerificationIndependence)
    independent: bool | None = None
    run_id: str | None = None
    artifact_ids: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)
    plan_id: str | None = None
    verification_execution_id: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationPlanDraft:
    """Immutable input for one new version of an Agent verification plan."""

    plan_key: str
    name: str
    kind: VerificationKind
    method: str
    scope: str
    prompt: str
    system: str
    model: str | None = None
    # Legacy name retained for persisted/API compatibility. True requests an
    # automatic PromotionGate evaluation only; Agent execution never applies
    # the resulting workflow transition.
    auto_promote: bool = False
    metadata: dict = field(default_factory=dict)
