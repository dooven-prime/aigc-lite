"""Contracts for policy proposals and externally issued enforcement evidence.

These objects describe authority without granting it.  A PolicyProposal is an
immutable request to change one runtime policy.  PermissionDiff is derived by
the host, never accepted from a client.  EnforcementReceipt records what a
registered enforcement backend observed; it is not a model-authored result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .qualification import canonical_hash

EXECUTION_POLICY_CONTRACT_VERSION = "execution.policy.v1"
POLICY_PROPOSAL_CONTRACT_VERSION = "policy.proposal.v1"
PERMISSION_DIFF_CONTRACT_VERSION = "permission.diff.v1"
ENFORCEMENT_RECEIPT_CONTRACT_VERSION = "enforcement.receipt.v1"


class PermissionDomain(StrEnum):
    FILESYSTEM = "filesystem"
    NETWORK = "network"
    PROCESS = "process"
    PROVIDER = "provider"
    CREDENTIAL = "credential"


class EnforcementTargetType(StrEnum):
    TOOL_EXECUTION_BACKEND = "tool_execution_backend"
    TOOL_PROVIDER = "tool_provider"
    ROBOT_CONTROLLER = "robot_controller"


class PermissionChangeType(StrEnum):
    PERMISSION_ADDED = "permission_added"
    PERMISSION_REMOVED = "permission_removed"
    ACTIONS_ADDED = "actions_added"
    ACTIONS_REMOVED = "actions_removed"
    CONSTRAINTS_CHANGED = "constraints_changed"
    LIMIT_ADDED = "limit_added"
    LIMIT_REMOVED = "limit_removed"
    LIMIT_INCREASED = "limit_increased"
    LIMIT_REDUCED = "limit_reduced"


class PolicyProposalState(StrEnum):
    PROPOSED = "proposed"


class EnforcementDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    QUARANTINE = "quarantine"


class EnforcementOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    QUARANTINED = "quarantined"
    INDETERMINATE = "indeterminate"


class EnforcementTrustDomain(StrEnum):
    APPLICATION_PROCESS = "application_process"
    EXTERNAL_RUNTIME = "external_runtime"
    INDEPENDENT_INFRASTRUCTURE = "independent_infrastructure"


@dataclass(frozen=True, slots=True)
class PolicyPermission:
    domain: PermissionDomain
    resource: str
    actions: tuple[str, ...]
    constraints: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.domain.value}:{self.resource}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain.value,
            "resource": self.resource,
            "actions": sorted(set(self.actions)),
            "constraints": self.constraints,
        }


@dataclass(frozen=True, slots=True)
class ExecutionPolicySnapshot:
    policy_id: str
    revision: int
    permissions: tuple[PolicyPermission, ...] = ()
    limits: dict[str, int] = field(default_factory=dict)
    default_action: str = "deny"

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": EXECUTION_POLICY_CONTRACT_VERSION,
            "policy_id": self.policy_id,
            "revision": self.revision,
            "default_action": self.default_action,
            "permissions": [
                item.as_dict()
                for item in sorted(self.permissions, key=lambda value: value.key)
            ],
            "limits": dict(sorted(self.limits.items())),
        }

    @property
    def policy_hash(self) -> str:
        return canonical_hash(self.as_dict())


@dataclass(frozen=True, slots=True)
class PermissionChange:
    change_type: PermissionChangeType
    key: str
    before: Any
    after: Any
    expands_authority: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "change_type": self.change_type.value,
            "key": self.key,
            "before": self.before,
            "after": self.after,
            "expands_authority": self.expands_authority,
        }


@dataclass(frozen=True, slots=True)
class PermissionDiff:
    base_policy_hash: str
    candidate_policy_hash: str
    changes: tuple[PermissionChange, ...]

    @property
    def expansion_count(self) -> int:
        return sum(1 for item in self.changes if item.expands_authority)

    @property
    def reduction_count(self) -> int:
        return sum(1 for item in self.changes if not item.expands_authority)

    @property
    def expands_authority(self) -> bool:
        return self.expansion_count > 0

    def as_dict(self) -> dict[str, Any]:
        value = {
            "contract_version": PERMISSION_DIFF_CONTRACT_VERSION,
            "base_policy_hash": self.base_policy_hash,
            "candidate_policy_hash": self.candidate_policy_hash,
            "expands_authority": self.expands_authority,
            "expansion_count": self.expansion_count,
            "reduction_count": self.reduction_count,
            "changes": [item.as_dict() for item in self.changes],
        }
        return {**value, "diff_hash": canonical_hash(value)}


@dataclass(frozen=True, slots=True)
class EnforcementIssuer:
    """Host-owned identity for one trusted receipt-producing adapter."""

    issuer_id: str
    backend_id: str
    identity: str
    trust_domain: EnforcementTrustDomain
    attestation_type: str = "none"

    def as_dict(self) -> dict[str, Any]:
        return {
            "issuer_id": self.issuer_id,
            "backend_id": self.backend_id,
            "identity": self.identity,
            "trust_domain": self.trust_domain.value,
            "attestation_type": self.attestation_type,
        }


@dataclass(frozen=True, slots=True)
class EnforcementReceiptDraft:
    run_id: str
    policy_id: str
    policy_revision: int
    policy_hash: str
    execution_envelope: dict[str, Any]
    tool_spec_hash: str
    arguments_digest: str
    decision: EnforcementDecision
    outcome: EnforcementOutcome
    observed_effects: tuple[dict[str, Any], ...] = ()
    denied_effects: tuple[dict[str, Any], ...] = ()
    credential_bindings: tuple[dict[str, Any], ...] = ()
    step_id: str | None = None
    proposal_id: str | None = None
    image_digest: str | None = None
    toolchain_digest: str | None = None
    sandbox_id: str | None = None
    workload_id: str | None = None
    external_signature: str | None = None
    attestation: dict[str, Any] = field(default_factory=dict)
    issued_at: str | None = None


def calculate_permission_diff(
    base: ExecutionPolicySnapshot,
    candidate: ExecutionPolicySnapshot,
) -> PermissionDiff:
    """Return a deterministic conservative authority delta.

    Constraint semantics are backend-specific.  Any constraint change is
    therefore treated as an expansion unless a future domain verifier proves
    otherwise.
    """

    changes: list[PermissionChange] = []
    base_permissions = {item.key: item for item in base.permissions}
    candidate_permissions = {item.key: item for item in candidate.permissions}
    for key in sorted(base_permissions.keys() | candidate_permissions.keys()):
        before = base_permissions.get(key)
        after = candidate_permissions.get(key)
        if before is None and after is not None:
            changes.append(
                PermissionChange(
                    PermissionChangeType.PERMISSION_ADDED,
                    key,
                    None,
                    after.as_dict(),
                    True,
                )
            )
            continue
        if after is None and before is not None:
            changes.append(
                PermissionChange(
                    PermissionChangeType.PERMISSION_REMOVED,
                    key,
                    before.as_dict(),
                    None,
                    False,
                )
            )
            continue
        assert before is not None and after is not None
        before_actions = set(before.actions)
        after_actions = set(after.actions)
        added = sorted(after_actions - before_actions)
        removed = sorted(before_actions - after_actions)
        if added:
            changes.append(
                PermissionChange(
                    PermissionChangeType.ACTIONS_ADDED,
                    key,
                    [],
                    added,
                    True,
                )
            )
        if removed:
            changes.append(
                PermissionChange(
                    PermissionChangeType.ACTIONS_REMOVED,
                    key,
                    removed,
                    [],
                    False,
                )
            )
        if before.constraints != after.constraints:
            changes.append(
                PermissionChange(
                    PermissionChangeType.CONSTRAINTS_CHANGED,
                    key,
                    before.constraints,
                    after.constraints,
                    True,
                )
            )

    for name in sorted(base.limits.keys() | candidate.limits.keys()):
        before = base.limits.get(name)
        after = candidate.limits.get(name)
        key = f"limit:{name}"
        if before is None and after is not None:
            change_type = PermissionChangeType.LIMIT_ADDED
            expands = False
        elif after is None and before is not None:
            change_type = PermissionChangeType.LIMIT_REMOVED
            expands = True
        elif after is not None and before is not None and after > before:
            change_type = PermissionChangeType.LIMIT_INCREASED
            expands = True
        elif after is not None and before is not None and after < before:
            change_type = PermissionChangeType.LIMIT_REDUCED
            expands = False
        else:
            continue
        changes.append(PermissionChange(change_type, key, before, after, expands))

    return PermissionDiff(
        base_policy_hash=base.policy_hash,
        candidate_policy_hash=candidate.policy_hash,
        changes=tuple(changes),
    )
