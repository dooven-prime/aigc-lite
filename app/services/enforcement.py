"""Execution policy proposals, conservative permission diffs, and receipts."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..core.contracts import RequestContext
from ..core.enforcement import (
    ENFORCEMENT_RECEIPT_CONTRACT_VERSION,
    POLICY_PROPOSAL_CONTRACT_VERSION,
    EnforcementDecision,
    EnforcementIssuer,
    EnforcementOutcome,
    EnforcementReceiptDraft,
    EnforcementTargetType,
    ExecutionPolicySnapshot,
    PolicyPermission,
    PolicyProposalState,
    ReceiptSignatureVerification,
    VerifiedEnforcementReceipt,
    calculate_permission_diff,
)
from ..core.errors import InvalidExecutionPolicyError, ResourceNotFoundError
from ..core.qualification import canonical_hash
from ..database import get_repository
from ..redaction import is_sensitive_key, redact, redact_text
from ..repository import Repository

RepositoryProvider = Callable[[], Repository]

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_.:-]{0,99}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_MAX_POLICY_BYTES = 256_000
_MAX_RULES = 256
_MAX_EFFECTS = 500
_MAX_EVIDENCE_BYTES = 256_000


class EnforcementIssuerRegistry:
    """Host-owned registry; request payloads cannot create issuer identities."""

    def __init__(self, issuers: list[EnforcementIssuer] | None = None) -> None:
        self._issuers: dict[str, EnforcementIssuer] = {}
        for issuer in issuers or []:
            self.register(issuer)

    def register(self, issuer: EnforcementIssuer) -> None:
        if issuer.issuer_id in self._issuers:
            raise ValueError(f"Enforcement issuer already registered: {issuer.issuer_id}")
        if not _NAME.fullmatch(issuer.issuer_id) or not _NAME.fullmatch(
            issuer.backend_id
        ):
            raise ValueError("Enforcement issuer and backend IDs must be stable names")
        if (
            not issuer.identity.strip()
            or len(issuer.identity) > 500
            or redact_text(issuer.identity) != issuer.identity
        ):
            raise ValueError("Enforcement issuer identity is invalid")
        if not _ACTION.fullmatch(issuer.attestation_type):
            raise ValueError("Enforcement attestation type is invalid")
        self._issuers[issuer.issuer_id] = issuer

    def get(self, issuer_id: str) -> EnforcementIssuer:
        try:
            return self._issuers[issuer_id]
        except KeyError as exc:
            raise ResourceNotFoundError("enforcement_issuer", issuer_id) from exc

    def list(self) -> list[dict[str, Any]]:
        return [self._issuers[key].as_dict() for key in sorted(self._issuers)]


class EnforcementService:
    """Record proposals without applying them and evidence without self-claims."""

    def __init__(
        self,
        repository_provider: RepositoryProvider = get_repository,
        issuer_registry: EnforcementIssuerRegistry | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._issuer_registry = issuer_registry or EnforcementIssuerRegistry()

    def list_issuers(self) -> list[dict[str, Any]]:
        return self._issuer_registry.list()

    def propose_policy(
        self,
        context: RequestContext,
        *,
        target_type: EnforcementTargetType,
        target_id: str,
        candidate_policy: ExecutionPolicySnapshot,
        base_policy: ExecutionPolicySnapshot | None = None,
        rationale: str = "",
    ) -> dict:
        target_id = target_id.strip()
        if (
            not target_id
            or len(target_id) > 500
            or redact_text(target_id) != target_id
        ):
            raise InvalidExecutionPolicyError("target_id", "Target ID is invalid")
        if len(rationale) > 4_000:
            raise InvalidExecutionPolicyError(
                "rationale", "Policy proposal rationale is too large"
            )
        candidate = self._normalize_policy(candidate_policy, allow_genesis=False)
        if base_policy is None:
            base = ExecutionPolicySnapshot(
                policy_id=candidate.policy_id,
                revision=0,
            )
        else:
            base = self._normalize_policy(base_policy, allow_genesis=True)
        if base.policy_id != candidate.policy_id:
            raise InvalidExecutionPolicyError(
                "candidate_policy.policy_id",
                "Base and candidate policy IDs must match",
            )
        if candidate.revision != base.revision + 1:
            raise InvalidExecutionPolicyError(
                "candidate_policy.revision",
                "Candidate policy revision must immediately follow the reviewed base",
            )
        permission_diff = calculate_permission_diff(base, candidate)
        diff_value = permission_diff.as_dict()
        values = {
            "id": str(uuid4()),
            "contract_version": POLICY_PROPOSAL_CONTRACT_VERSION,
            "target_type": target_type.value,
            "target_id": target_id,
            "state": PolicyProposalState.PROPOSED.value,
            "base_policy_id": base.policy_id,
            "base_policy_revision": base.revision,
            "base_policy_hash": base.policy_hash,
            "candidate_policy_id": candidate.policy_id,
            "candidate_policy_revision": candidate.revision,
            "candidate_policy_hash": candidate.policy_hash,
            "base_policy": base.as_dict(),
            "candidate_policy": candidate.as_dict(),
            "permission_diff": diff_value,
            "diff_hash": diff_value["diff_hash"],
            "expands_authority": permission_diff.expands_authority,
            "expansion_count": permission_diff.expansion_count,
            "reduction_count": permission_diff.reduction_count,
            "rationale": redact_text(rationale.strip()),
            "requested_by": context.principal_id,
        }
        return self._repository_provider().create_policy_proposal(
            context.workspace_id, values
        )

    def get_policy_proposal(
        self, context: RequestContext, proposal_id: str
    ) -> dict:
        value = self._repository_provider().get_policy_proposal(
            context.workspace_id, proposal_id
        )
        if value is None:
            raise ResourceNotFoundError("policy_proposal", proposal_id)
        return value

    def list_policy_proposals(
        self,
        context: RequestContext,
        *,
        target_type: EnforcementTargetType | None = None,
        target_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return self._repository_provider().list_policy_proposals(
            context.workspace_id,
            target_type=target_type.value if target_type else None,
            target_id=target_id,
            limit=max(1, min(limit, 200)),
        )

    def record_receipt(
        self,
        workspace_id: str,
        *,
        issuer_id: str,
        draft: EnforcementReceiptDraft,
    ) -> dict:
        """Internal adapter seam. No public transport exposes this mutation."""

        return self._record_receipt(
            workspace_id,
            issuer_id=issuer_id,
            draft=draft,
            signed_payload=None,
            verification=None,
        )

    def record_verified_receipt(
        self,
        workspace_id: str,
        *,
        verified: VerifiedEnforcementReceipt,
    ) -> dict:
        """Persist only a verifier-produced signature result."""

        if not verified.draft.external_signature:
            raise InvalidExecutionPolicyError(
                "external_signature", "Verified receipt must retain its signature"
            )
        return self._record_receipt(
            workspace_id,
            issuer_id=verified.issuer_id,
            draft=verified.draft,
            signed_payload=verified.signed_payload,
            verification=verified.verification,
        )

    def _record_receipt(
        self,
        workspace_id: str,
        *,
        issuer_id: str,
        draft: EnforcementReceiptDraft,
        signed_payload: dict[str, Any] | None,
        verification: ReceiptSignatureVerification | None,
    ) -> dict:

        issuer = self._issuer_registry.get(issuer_id)
        repository = self._repository_provider()
        if repository.get_run(workspace_id, draft.run_id) is None:
            raise ResourceNotFoundError("agent_run", draft.run_id)
        self._validate_receipt_draft(draft)
        if draft.proposal_id:
            proposal = repository.get_policy_proposal(workspace_id, draft.proposal_id)
            if proposal is None:
                raise ResourceNotFoundError("policy_proposal", draft.proposal_id)
            if (
                proposal["candidate_policy_id"] != draft.policy_id
                or int(proposal["candidate_policy_revision"]) != draft.policy_revision
                or proposal["candidate_policy_hash"] != draft.policy_hash
            ):
                raise InvalidExecutionPolicyError(
                    "proposal_id",
                    "Receipt policy does not match the frozen proposal candidate",
                )

        issued_at = self._normalize_timestamp(draft.issued_at)
        observed = self._bounded_evidence("observed_effects", draft.observed_effects)
        denied = self._bounded_evidence("denied_effects", draft.denied_effects)
        bindings = self._bounded_evidence(
            "credential_bindings", draft.credential_bindings
        )
        attestation = self._bounded_mapping("attestation", redact(draft.attestation))
        if issuer.attestation_type != "none" and not attestation:
            raise InvalidExecutionPolicyError(
                "attestation", "Registered issuer requires attestation evidence"
            )
        payload = {
            "contract_version": ENFORCEMENT_RECEIPT_CONTRACT_VERSION,
            "workspace_id": workspace_id,
            "run_id": draft.run_id,
            "step_id": draft.step_id,
            "proposal_id": draft.proposal_id,
            **issuer.as_dict(),
            "enforcement_identity": issuer.identity,
            "policy_id": draft.policy_id,
            "policy_revision": draft.policy_revision,
            "policy_hash": draft.policy_hash,
            "execution_envelope_hash": (
                draft.execution_envelope_hash
                or canonical_hash(draft.execution_envelope)
            ),
            "tool_spec_hash": draft.tool_spec_hash,
            "arguments_digest": draft.arguments_digest,
            "decision": draft.decision.value,
            "outcome": draft.outcome.value,
            "observed_effects": observed,
            "denied_effects": denied,
            "credential_bindings": bindings,
            "image_digest": draft.image_digest,
            "toolchain_digest": draft.toolchain_digest,
            "sandbox_id": draft.sandbox_id,
            "workload_id": draft.workload_id,
            "external_signature": draft.external_signature,
            "attestation": attestation,
            "issued_at": issued_at,
        }
        # ``identity`` is represented by the explicitly named persistence field.
        payload.pop("identity")
        if verification is not None:
            payload["signature_verification"] = verification.as_dict()
        values = {
            "id": str(uuid4()),
            **payload,
            "receipt_hash": canonical_hash(payload),
            "signature_verified": verification is not None,
            "enforcement_request_id": (
                verification.request_id if verification is not None else None
            ),
            "enforcement_request_hash": (
                verification.request_hash if verification is not None else None
            ),
            "signature_algorithm": (
                verification.algorithm.value if verification is not None else None
            ),
            "signing_key_id": (
                verification.key_id if verification is not None else None
            ),
            "signed_payload_hash": (
                verification.signed_payload_hash if verification is not None else None
            ),
            "signature_verified_at": (
                verification.verified_at if verification is not None else None
            ),
            "signature_verifier_id": (
                verification.verifier_id if verification is not None else None
            ),
            "signature_verification": (
                verification.as_dict() if verification is not None else {}
            ),
            "signed_payload": signed_payload or {},
        }
        return repository.create_enforcement_receipt(workspace_id, values)

    def get_receipt(self, context: RequestContext, receipt_id: str) -> dict:
        value = self._repository_provider().get_enforcement_receipt(
            context.workspace_id, receipt_id
        )
        if value is None:
            raise ResourceNotFoundError("enforcement_receipt", receipt_id)
        return value

    def list_receipts(
        self,
        context: RequestContext,
        *,
        run_id: str | None = None,
        proposal_id: str | None = None,
        backend_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        return self._repository_provider().list_enforcement_receipts(
            context.workspace_id,
            run_id=run_id,
            proposal_id=proposal_id,
            backend_id=backend_id,
            limit=max(1, min(limit, 500)),
        )

    @classmethod
    def _normalize_policy(
        cls, policy: ExecutionPolicySnapshot, *, allow_genesis: bool
    ) -> ExecutionPolicySnapshot:
        policy_id = policy.policy_id.strip()
        if not _NAME.fullmatch(policy_id):
            raise InvalidExecutionPolicyError("policy_id", "Policy ID is invalid")
        if policy.default_action != "deny":
            raise InvalidExecutionPolicyError(
                "default_action", "Execution policies must deny by default"
            )
        minimum_revision = 0 if allow_genesis else 1
        if policy.revision < minimum_revision:
            raise InvalidExecutionPolicyError("revision", "Policy revision is invalid")
        if len(policy.permissions) > _MAX_RULES:
            raise InvalidExecutionPolicyError(
                "permissions", f"Policy exceeds {_MAX_RULES} permission rules"
            )
        permissions: list[PolicyPermission] = []
        seen: set[str] = set()
        for item in policy.permissions:
            resource = item.resource.strip()
            if (
                not resource
                or len(resource) > 2_000
                or redact_text(resource) != resource
            ):
                raise InvalidExecutionPolicyError(
                    "permissions.resource", "Permission resource is invalid"
                )
            actions = tuple(sorted(set(value.strip() for value in item.actions)))
            if not actions or len(actions) > 32 or any(
                not _ACTION.fullmatch(value) for value in actions
            ):
                raise InvalidExecutionPolicyError(
                    "permissions.actions", "Permission actions are invalid"
                )
            constraints = cls._bounded_mapping(
                "permissions.constraints", item.constraints, reject_secrets=True
            )
            normalized = PolicyPermission(
                domain=item.domain,
                resource=resource,
                actions=actions,
                constraints=constraints,
            )
            if normalized.key in seen:
                raise InvalidExecutionPolicyError(
                    "permissions", "Permission domain/resource pairs must be unique"
                )
            seen.add(normalized.key)
            permissions.append(normalized)
        limits: dict[str, int] = {}
        for name, value in policy.limits.items():
            normalized_name = name.strip()
            if not _ACTION.fullmatch(normalized_name):
                raise InvalidExecutionPolicyError(
                    "limits", "Policy limit names are invalid"
                )
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise InvalidExecutionPolicyError(
                    "limits", "Policy limits must be non-negative integers"
                )
            limits[normalized_name] = value
        normalized_policy = ExecutionPolicySnapshot(
            policy_id=policy_id,
            revision=policy.revision,
            permissions=tuple(permissions),
            limits=limits,
            default_action="deny",
        )
        if len(cls._json_bytes(normalized_policy.as_dict())) > _MAX_POLICY_BYTES:
            raise InvalidExecutionPolicyError(
                "policy", f"Policy exceeds {_MAX_POLICY_BYTES} encoded bytes"
            )
        return normalized_policy

    @staticmethod
    def _validate_receipt_draft(draft: EnforcementReceiptDraft) -> None:
        for field_name, value in (
            ("policy_hash", draft.policy_hash),
            ("tool_spec_hash", draft.tool_spec_hash),
            ("arguments_digest", draft.arguments_digest),
        ):
            if not _HASH.fullmatch(value):
                raise InvalidExecutionPolicyError(
                    field_name, f"{field_name} must be a lowercase SHA-256 digest"
                )
        if draft.execution_envelope_hash is not None and not _HASH.fullmatch(
            draft.execution_envelope_hash
        ):
            raise InvalidExecutionPolicyError(
                "execution_envelope_hash",
                "execution_envelope_hash must be a lowercase SHA-256 digest",
            )
        if not _NAME.fullmatch(draft.policy_id) or draft.policy_revision <= 0:
            raise InvalidExecutionPolicyError("policy", "Receipt policy is invalid")
        for field_name, value, maximum in (
            ("image_digest", draft.image_digest, 500),
            ("toolchain_digest", draft.toolchain_digest, 500),
            ("sandbox_id", draft.sandbox_id, 500),
            ("workload_id", draft.workload_id, 500),
            ("external_signature", draft.external_signature, 65_536),
        ):
            if value is not None and (
                not value.strip()
                or len(value) > maximum
                or (field_name != "external_signature" and redact_text(value) != value)
            ):
                raise InvalidExecutionPolicyError(
                    field_name, f"Receipt {field_name} is invalid"
                )
        allowed_outcomes = {
            EnforcementDecision.ALLOW: {
                EnforcementOutcome.SUCCEEDED,
                EnforcementOutcome.FAILED,
                EnforcementOutcome.TIMED_OUT,
                EnforcementOutcome.CANCELLED,
                EnforcementOutcome.INDETERMINATE,
            },
            EnforcementDecision.DENY: {EnforcementOutcome.DENIED},
            EnforcementDecision.QUARANTINE: {
                EnforcementOutcome.QUARANTINED,
                EnforcementOutcome.INDETERMINATE,
            },
        }
        if draft.outcome not in allowed_outcomes[draft.decision]:
            raise InvalidExecutionPolicyError(
                "outcome", "Receipt decision and outcome are inconsistent"
            )
        try:
            encoded = EnforcementService._json_bytes(draft.execution_envelope)
        except InvalidExecutionPolicyError as exc:
            raise InvalidExecutionPolicyError(
                "execution_envelope", "Execution envelope must be canonical JSON"
            ) from exc
        if len(encoded) > _MAX_EVIDENCE_BYTES:
            raise InvalidExecutionPolicyError(
                "execution_envelope", "Execution envelope is too large"
            )

    @staticmethod
    def _normalize_timestamp(value: str | None) -> str:
        if value is None:
            return datetime.now(UTC).isoformat()
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InvalidExecutionPolicyError(
                "issued_at", "Receipt timestamp must be ISO-8601"
            ) from exc
        if parsed.tzinfo is None:
            raise InvalidExecutionPolicyError(
                "issued_at", "Receipt timestamp must include a timezone"
            )
        return parsed.astimezone(UTC).isoformat()

    @classmethod
    def _bounded_evidence(
        cls, field_name: str, values: tuple[dict[str, Any], ...]
    ) -> list[dict[str, Any]]:
        if len(values) > _MAX_EFFECTS:
            raise InvalidExecutionPolicyError(
                field_name, f"{field_name} exceeds {_MAX_EFFECTS} records"
            )
        redacted = redact(list(values))
        if len(cls._json_bytes(redacted)) > _MAX_EVIDENCE_BYTES:
            raise InvalidExecutionPolicyError(field_name, f"{field_name} is too large")
        return redacted

    @classmethod
    def _bounded_mapping(
        cls,
        field_name: str,
        value: dict[str, Any],
        *,
        reject_secrets: bool = False,
    ) -> dict[str, Any]:
        if reject_secrets and (
            cls._contains_sensitive_key(value) or redact(value) != value
        ):
            raise InvalidExecutionPolicyError(
                field_name, "Policy constraints cannot contain credential material"
            )
        try:
            encoded = cls._json_bytes(value)
            normalized = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidExecutionPolicyError(
                field_name, f"{field_name} must be canonical JSON"
            ) from exc
        if not isinstance(normalized, dict) or len(encoded) > _MAX_EVIDENCE_BYTES:
            raise InvalidExecutionPolicyError(field_name, f"{field_name} is too large")
        return normalized

    @staticmethod
    def _json_bytes(value: Any) -> bytes:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise InvalidExecutionPolicyError(
                "json", "Value must contain only finite JSON data"
            ) from exc

    @classmethod
    def _contains_sensitive_key(cls, value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                is_sensitive_key(str(key)) or cls._contains_sensitive_key(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(cls._contains_sensitive_key(item) for item in value)
        return False
