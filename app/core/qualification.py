"""Stable contracts for the Qualification Plane.

Qualification is deliberately modelled as a relation over frozen inputs.  It
is never a mutable trust flag on an Artifact or Claim.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

QUALIFICATION_CONTRACT_VERSION = "qualification.plane.v1"
EVIDENCE_CLOSURE_VERSION = "evidence.closure.v1"
VERIFIER_LINEAGE_VERSION = "verifier.lineage.v1"


class QualificationVerdict(StrEnum):
    ADMITTED = "ADMITTED"
    BLOCKED = "BLOCKED"
    UNRESOLVED = "UNRESOLVED"
    STALE = "STALE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class EvidenceAxisState(StrEnum):
    SATISFIED = "satisfied"
    FAILED = "failed"
    UNDETERMINED = "undetermined"
    NOT_APPLICABLE = "not_applicable"


class ValidationModality(StrEnum):
    KERNEL_CHECK = "kernel_check"
    EXACT_REPLAY = "exact_replay"
    NUMERICAL_EQUIVALENCE = "numerical_equivalence"
    INDEPENDENT_RECOMPUTATION = "independent_recomputation"
    EXPERT_REVIEW = "expert_review"
    AGENT_REVIEW = "agent_review"
    WET_LAB = "wet_lab"
    PHYSICAL_EXPERIMENT = "physical_experiment"
    FIELD_OBSERVATION = "field_observation"


class EvidenceEdgeType(StrEnum):
    DERIVED_FROM = "derived_from"
    EXECUTED_WITH = "executed_with"
    PRODUCED = "produced"
    VERIFIED_BY = "verified_by"
    REVIEWED_BY = "reviewed_by"
    DEPENDS_ON = "depends_on"
    QUALIFIED_BY = "qualified_by"


class CurrentUseState(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    REVOKED = "revoked"


class AuthorizationState(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class QualificationCriterion:
    code: str
    description: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class QualificationProfile:
    profile_id: str
    version: int
    claim_kind: str
    policy_version: str
    criteria: tuple[QualificationCriterion, ...]
    accepted_modalities: tuple[ValidationModality, ...] = ()
    description: str = ""

    @property
    def key(self) -> str:
        return f"{self.profile_id}@{self.version}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": QUALIFICATION_CONTRACT_VERSION,
            "profile_id": self.profile_id,
            "version": self.version,
            "claim_kind": self.claim_kind,
            "policy_version": self.policy_version,
            "description": self.description,
            "accepted_modalities": [item.value for item in self.accepted_modalities],
            "criteria": [
                {
                    "code": item.code,
                    "description": item.description,
                    "required": item.required,
                }
                for item in self.criteria
            ],
        }

    @property
    def content_hash(self) -> str:
        return canonical_hash(self.as_dict())


@dataclass(frozen=True, slots=True)
class VerifierLineage:
    """Server-bound identity and execution lineage for one verification.

    These values are observations made by the runtime.  They are not a client
    assertion that a verifier was independent.
    """

    principal_id: str | None
    organization_id: str
    run_id: str | None = None
    model_provider: str | None = None
    model_family: str | None = None
    model_route: str | None = None
    prompt_template_hash: str | None = None
    toolchain_hash: str | None = None
    environment_hash: str | None = None
    data_snapshot_hash: str | None = None
    verification_plan_hash: str | None = None
    runtime_derived: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": VERIFIER_LINEAGE_VERSION,
            "principal_id": self.principal_id,
            "organization_id": self.organization_id,
            "run_id": self.run_id,
            "model_provider": self.model_provider,
            "model_family": self.model_family,
            "model_route": self.model_route,
            "prompt_template_hash": self.prompt_template_hash,
            "toolchain_hash": self.toolchain_hash,
            "environment_hash": self.environment_hash,
            "data_snapshot_hash": self.data_snapshot_hash,
            "verification_plan_hash": self.verification_plan_hash,
            "runtime_derived": self.runtime_derived,
        }


@dataclass(frozen=True, slots=True)
class CriterionReceipt:
    code: str
    state: EvidenceAxisState
    reason: str
    evidence_node_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "state": self.state.value,
            "reason": self.reason,
            "evidence_node_ids": list(self.evidence_node_ids),
        }


@dataclass(frozen=True, slots=True)
class EvidenceClosure:
    claim_revision_id: str
    claim_semantic_hash: str
    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": EVIDENCE_CLOSURE_VERSION,
            "claim_revision_id": self.claim_revision_id,
            "claim_semantic_hash": self.claim_semantic_hash,
            "nodes": list(self.nodes),
            "edges": list(self.edges),
            "limitations": list(self.limitations),
        }

    @property
    def content_hash(self) -> str:
        return canonical_hash(self.as_dict())


@dataclass(frozen=True, slots=True)
class QualificationDecision:
    verdict: QualificationVerdict
    criteria: tuple[CriterionReceipt, ...]
    blockers: tuple[str, ...] = ()
    evidence_vector: dict[str, EvidenceAxisState] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "criteria": [item.as_dict() for item in self.criteria],
            "blockers": list(self.blockers),
            "evidence_vector": {
                key: value.value for key, value in sorted(self.evidence_vector.items())
            },
        }


@dataclass(frozen=True, slots=True)
class AuthorizationGrantDraft:
    qualification_receipt_id: str
    action: str
    target: str
    scope: dict[str, Any]
    conditions: dict[str, Any] = field(default_factory=dict)
    expires_at: str | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    max_calls: int = 1


@dataclass(frozen=True, slots=True)
class MathTheoremCandidateDraft:
    claim_key: str
    name: str
    statement: str
    scope: str
    definitions: tuple[str, ...] = ()
    negative_boundaries: tuple[str, ...] = ()
    dependency_claim_ids: tuple[str, ...] = ()
    parent_revision_id: str | None = None


def canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def claim_semantic_snapshot(claim: dict[str, Any]) -> dict[str, Any]:
    """Return only fields that define one exact semantic ClaimRevision."""

    return {
        "claim_key": claim.get("claim_key"),
        "revision_number": int(claim.get("revision_number") or 1),
        "claim_kind": claim.get("claim_type"),
        "statement": claim.get("statement"),
        "scope": claim.get("scope"),
        "definitions": claim.get("definitions") or [],
        "negative_boundaries": claim.get("negative_boundaries") or [],
        "dependency_claim_ids": claim.get("dependency_claim_ids") or [],
        "parent_revision_id": claim.get("parent_revision_id"),
    }


def claim_semantic_hash(claim: dict[str, Any]) -> str:
    return canonical_hash(claim_semantic_snapshot(claim))
