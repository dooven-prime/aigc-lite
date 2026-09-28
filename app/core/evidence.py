"""Cross-domain contracts for evidence-bearing work.

The core deliberately stops before domain semantics. Decision candidates,
scientific observables, and organizational value concepts belong to profiles;
these records only describe how work was scoped, executed, reviewed, and
frozen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ProtocolStatus(StrEnum):
    DRAFT = "draft"
    REGISTERED = "registered"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    UNRESOLVED = "unresolved"
    FROZEN = "frozen"


class EvidenceResolution(StrEnum):
    """Epistemic result; unknown is intentionally distinct from rejection."""

    SUPPORTED = "supported"
    INSUFFICIENT = "insufficient"
    UNDETERMINED = "undetermined"
    REJECTED = "rejected"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


class ReviewerKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    AUTOMATED = "automated"


@dataclass(frozen=True, slots=True)
class ProtocolDraft:
    name: str
    profile: str
    purpose: str
    scope: str
    completion_predicate: str
    stop_conditions: tuple[str, ...] = ()
    budget: dict = field(default_factory=dict)
    source_uri: str | None = None
    status: ProtocolStatus = ProtocolStatus.DRAFT
    version: int = 1


@dataclass(frozen=True, slots=True)
class ClaimDraft:
    statement: str
    resolution: EvidenceResolution
    scope: str
    evidence_refs: tuple[str, ...] = ()
    prohibited_upgrades: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionReceiptDraft:
    status: str
    input_digest: str
    output_digest: str
    runtime: dict = field(default_factory=dict)
    budget: dict = field(default_factory=dict)
    artifact_ids: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewDraft:
    status: ReviewStatus
    reviewer_kind: ReviewerKind
    finding: str
    reviewer_id: str | None = None
    independent: bool = False
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FreezeManifestDraft:
    name: str
    version: int
    members: tuple[dict, ...]
    content_hash: str
