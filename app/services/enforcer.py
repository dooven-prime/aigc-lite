"""Dispatch to external enforcers and verify their signed receipts."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..core.contracts import RequestContext
from ..core.enforcement import (
    ENFORCEMENT_DISPATCH_CONTRACT_VERSION,
    SIGNATURE_ENVELOPE_CONTRACT_VERSION,
    SIGNATURE_RECHECK_CONTRACT_VERSION,
    SIGNED_ENFORCEMENT_RECEIPT_CONTRACT_VERSION,
    EnforcementDecision,
    EnforcementDispatchState,
    EnforcementOutcome,
    EnforcementReceiptDraft,
    EnforcementSignatureAlgorithm,
    EnforcementVerificationKey,
    EnforcerRequest,
    ReceiptSignatureVerification,
    SignedEnforcementReceiptEnvelope,
    VerifiedEnforcementReceipt,
    canonical_enforcement_json,
    enforcement_payload_hash,
)
from ..core.errors import (
    ApplicationError,
    EnforcementDispatchIndeterminateError,
    EnforcerNotConfiguredError,
    ErrorCode,
    InvalidEnforcementReceiptError,
    InvalidExecutionPolicyError,
    ResourceNotFoundError,
)
from ..database import get_repository
from ..ports.enforcer import EnforcerAdapter
from ..redaction import redact
from ..repository import Repository
from .enforcement import EnforcementIssuerRegistry, EnforcementService

RepositoryProvider = Callable[[], Repository]
NowProvider = Callable[[], datetime]

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_MAX_SIGNED_PAYLOAD_BYTES = 256_000
_MAX_EFFECTS = 500
_MAX_CLOCK_SKEW = timedelta(minutes=2)
_MAX_DISPATCH_SECONDS = 300.0


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise InvalidEnforcementReceiptError(field, f"{field} must be ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidEnforcementReceiptError(field, f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise InvalidEnforcementReceiptError(field, f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _decode_base64url(value: str, field: str, *, expected_size: int | None = None) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 100_000:
        raise InvalidEnforcementReceiptError(field, f"{field} is invalid")
    try:
        encoded = value.encode("ascii")
        decoded = base64.b64decode(
            encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise InvalidEnforcementReceiptError(field, f"{field} is not base64url") from exc
    if expected_size is not None and len(decoded) != expected_size:
        raise InvalidEnforcementReceiptError(field, f"{field} has an invalid length")
    return decoded


class EnforcementVerificationKeyRegistry:
    """Host-owned keyring; wire payloads cannot register or rotate trust roots."""

    def __init__(self, keys: list[EnforcementVerificationKey] | None = None) -> None:
        self._keys: dict[tuple[str, str], tuple[EnforcementVerificationKey, bytes]] = {}
        for key in keys or []:
            self.register(key)

    def register(self, key: EnforcementVerificationKey) -> None:
        identity = (key.issuer_id, key.key_id)
        if identity in self._keys:
            raise ValueError("Enforcement verification key is already registered")
        if not _NAME.fullmatch(key.issuer_id) or not _NAME.fullmatch(key.key_id):
            raise ValueError("Enforcement issuer and key IDs must be stable names")
        if key.algorithm is not EnforcementSignatureAlgorithm.ED25519:
            raise ValueError("Only Ed25519 enforcement receipt keys are supported")
        try:
            public_key = _decode_base64url(
                key.public_key, "public_key", expected_size=32
            )
        except InvalidEnforcementReceiptError as exc:
            raise ValueError("Enforcement public key is invalid") from exc
        not_before = _timestamp(key.not_before, "not_before") if key.not_before else None
        not_after = _timestamp(key.not_after, "not_after") if key.not_after else None
        if not_before and not_after and not_after <= not_before:
            raise ValueError("Enforcement key validity window is invalid")
        self._keys[identity] = (key, public_key)

    def get(
        self,
        issuer_id: str,
        key_id: str,
        algorithm: EnforcementSignatureAlgorithm,
        issued_at: datetime,
    ) -> tuple[EnforcementVerificationKey, bytes]:
        item = self._keys.get((issuer_id, key_id))
        if item is None:
            raise InvalidEnforcementReceiptError(
                "key_id", "Receipt signing key is not trusted for this issuer"
            )
        key, public_key = item
        if key.algorithm is not algorithm:
            raise InvalidEnforcementReceiptError(
                "algorithm", "Receipt signature algorithm does not match the trusted key"
            )
        if key.revoked:
            raise InvalidEnforcementReceiptError("key_id", "Receipt signing key is revoked")
        not_before = _timestamp(key.not_before, "not_before") if key.not_before else None
        not_after = _timestamp(key.not_after, "not_after") if key.not_after else None
        if not_before and issued_at < not_before:
            raise InvalidEnforcementReceiptError(
                "issued_at", "Receipt predates the signing key validity window"
            )
        if not_after and issued_at > not_after:
            raise InvalidEnforcementReceiptError(
                "issued_at", "Receipt postdates the signing key validity window"
            )
        return key, public_key

    def list_public(self) -> list[dict[str, Any]]:
        values = []
        for (issuer_id, key_id), (key, public_key) in sorted(self._keys.items()):
            values.append(
                {
                    "issuer_id": issuer_id,
                    "key_id": key_id,
                    "algorithm": key.algorithm.value,
                    "public_key_fingerprint": hashlib.sha256(public_key).hexdigest(),
                    "not_before": key.not_before,
                    "not_after": key.not_after,
                    "revoked": key.revoked,
                }
            )
        return values


class EnforcerAdapterRegistry:
    """Deployer-owned adapter registry; adapters are never workspace-created."""

    def __init__(self, adapters: list[EnforcerAdapter] | None = None) -> None:
        self._adapters: dict[str, EnforcerAdapter] = {}
        for adapter in adapters or []:
            self.register(adapter)

    def register(self, adapter: EnforcerAdapter) -> None:
        if not _NAME.fullmatch(adapter.adapter_id) or not _NAME.fullmatch(
            adapter.issuer_id
        ):
            raise ValueError("Enforcer adapter and issuer IDs must be stable names")
        if adapter.adapter_id in self._adapters:
            raise ValueError(f"Enforcer adapter already registered: {adapter.adapter_id}")
        if not adapter.target_id.strip() or len(adapter.target_id) > 500:
            raise ValueError("Enforcer adapter target ID is invalid")
        self._adapters[adapter.adapter_id] = adapter

    def get(self, adapter_id: str) -> EnforcerAdapter:
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            raise EnforcerNotConfiguredError(adapter_id) from exc

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "adapter_id": item.adapter_id,
                "issuer_id": item.issuer_id,
                "target_type": item.target_type.value,
                "target_id": item.target_id,
            }
            for item in (self._adapters[key] for key in sorted(self._adapters))
        ]


class SignedEnforcementReceiptVerifier:
    """Verify signature, exact request binding, issuer identity, and freshness."""

    def __init__(
        self,
        *,
        issuer_registry: EnforcementIssuerRegistry,
        key_registry: EnforcementVerificationKeyRegistry,
        now_provider: NowProvider = _utc_now,
        max_clock_skew: timedelta = _MAX_CLOCK_SKEW,
    ) -> None:
        self._issuer_registry = issuer_registry
        self._key_registry = key_registry
        self._now_provider = now_provider
        self._max_clock_skew = max_clock_skew

    def verify(
        self,
        request: EnforcerRequest,
        envelope: SignedEnforcementReceiptEnvelope,
    ) -> VerifiedEnforcementReceipt:
        if envelope.contract_version != SIGNATURE_ENVELOPE_CONTRACT_VERSION:
            raise InvalidEnforcementReceiptError(
                "contract_version", "Signature envelope contract is unsupported"
            )
        payload = self._normalize_payload(envelope.payload)
        issued_at = _timestamp(payload.get("issued_at"), "issued_at")
        issuer_id = payload.get("issuer_id")
        if not isinstance(issuer_id, str):
            raise InvalidEnforcementReceiptError("issuer_id", "Receipt issuer is invalid")
        try:
            issuer = self._issuer_registry.get(issuer_id)
        except ResourceNotFoundError as exc:
            raise InvalidEnforcementReceiptError(
                "issuer_id", "Receipt issuer is not registered by the host"
            ) from exc
        key, public_key_bytes = self._key_registry.get(
            issuer_id, envelope.key_id, envelope.algorithm, issued_at
        )
        signature = _decode_base64url(envelope.signature, "signature", expected_size=64)
        payload_bytes = canonical_enforcement_json(payload)
        try:
            Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
                signature, payload_bytes
            )
        except InvalidSignature as exc:
            raise InvalidEnforcementReceiptError(
                "signature", "Receipt signature verification failed"
            ) from exc

        self._verify_bindings(request, payload, issuer.as_dict())
        now = self._now_provider().astimezone(UTC)
        requested_at = _timestamp(request.requested_at, "requested_at")
        expires_at = _timestamp(request.expires_at, "expires_at")
        if now > expires_at + self._max_clock_skew:
            raise InvalidEnforcementReceiptError(
                "expires_at", "Receipt arrived after the enforcement request expired"
            )
        if issued_at < requested_at - self._max_clock_skew:
            raise InvalidEnforcementReceiptError(
                "issued_at", "Receipt predates the enforcement request"
            )
        if issued_at > now + self._max_clock_skew or issued_at > expires_at + self._max_clock_skew:
            raise InvalidEnforcementReceiptError(
                "issued_at", "Receipt timestamp is outside the accepted window"
            )

        decision = self._enum(EnforcementDecision, payload["decision"], "decision")
        outcome = self._enum(EnforcementOutcome, payload["outcome"], "outcome")
        effects = self._mapping_sequence(payload["observed_effects"], "observed_effects")
        denied = self._mapping_sequence(payload["denied_effects"], "denied_effects")
        bindings = self._mapping_sequence(
            payload["credential_bindings"], "credential_bindings"
        )
        if (
            redact(list(effects)) != list(effects)
            or redact(list(denied)) != list(denied)
        ):
            raise InvalidEnforcementReceiptError(
                "observed_effects",
                "Signed effects must not contain credential material",
            )
        if redact(list(bindings)) != list(bindings):
            raise InvalidEnforcementReceiptError(
                "credential_bindings",
                "Signed receipt must contain credential references, not credential material",
            )
        attestation = payload["attestation"]
        if not isinstance(attestation, dict):
            raise InvalidEnforcementReceiptError(
                "attestation", "Receipt attestation must be an object"
            )
        if redact(attestation) != attestation:
            raise InvalidEnforcementReceiptError(
                "attestation", "Signed attestation must not contain credential material"
            )
        if issuer.attestation_type != "none" and not attestation:
            raise InvalidEnforcementReceiptError(
                "attestation", "Registered issuer requires attestation evidence"
            )
        signed_payload_hash = enforcement_payload_hash(payload)
        fingerprint = hashlib.sha256(public_key_bytes).hexdigest()
        verified_at = now.isoformat()
        draft = EnforcementReceiptDraft(
            run_id=payload["run_id"],
            step_id=payload["step_id"],
            proposal_id=payload["proposal_id"],
            policy_id=payload["policy_id"],
            policy_revision=payload["policy_revision"],
            policy_hash=payload["policy_hash"],
            execution_envelope={},
            execution_envelope_hash=payload["execution_envelope_hash"],
            tool_spec_hash=payload["tool_spec_hash"],
            arguments_digest=payload["arguments_digest"],
            decision=decision,
            outcome=outcome,
            observed_effects=effects,
            denied_effects=denied,
            credential_bindings=bindings,
            image_digest=payload["image_digest"],
            toolchain_digest=payload["toolchain_digest"],
            sandbox_id=payload["sandbox_id"],
            workload_id=payload["workload_id"],
            external_signature=envelope.signature,
            attestation=attestation,
            issued_at=issued_at.isoformat(),
        )
        verification = ReceiptSignatureVerification(
            request_id=request.request_id,
            request_hash=request.request_hash,
            nonce_hash=hashlib.sha256(request.nonce.encode()).hexdigest(),
            algorithm=envelope.algorithm,
            key_id=key.key_id,
            signed_payload_hash=signed_payload_hash,
            verified_at=verified_at,
            verifier_id=f"ed25519:{key.key_id}:{fingerprint}",
            details={
                "signature_valid": True,
                "request_binding_valid": True,
                "issuer_binding_valid": True,
                "freshness_valid": True,
                "public_key_fingerprint": fingerprint,
            },
        )
        return VerifiedEnforcementReceipt(
            issuer_id=issuer_id,
            draft=draft,
            signed_payload=payload,
            verification=verification,
        )

    def list_keys(self) -> list[dict[str, Any]]:
        return self._key_registry.list_public()

    def verify_stored_receipt(self, receipt: dict[str, Any]) -> dict[str, Any]:
        """Recheck an immutable receipt against the currently loaded trust roots."""

        if receipt.get("signature_verified") is not True:
            raise InvalidEnforcementReceiptError(
                "signature_verified", "Receipt has no historical signature verification"
            )
        payload = self._normalize_payload(receipt.get("signed_payload"))
        issued_at = _timestamp(payload["issued_at"], "issued_at")
        issuer_id = payload["issuer_id"]
        try:
            issuer = self._issuer_registry.get(issuer_id)
        except ResourceNotFoundError as exc:
            raise InvalidEnforcementReceiptError(
                "issuer_id", "Receipt issuer is no longer registered by the host"
            ) from exc
        try:
            algorithm = EnforcementSignatureAlgorithm(receipt.get("signature_algorithm"))
        except (TypeError, ValueError) as exc:
            raise InvalidEnforcementReceiptError(
                "signature_algorithm", "Stored signature algorithm is invalid"
            ) from exc
        key_id = receipt.get("signing_key_id")
        if not isinstance(key_id, str):
            raise InvalidEnforcementReceiptError(
                "signing_key_id", "Stored signing key ID is invalid"
            )
        key, public_key_bytes = self._key_registry.get(
            issuer_id, key_id, algorithm, issued_at
        )
        signature = _decode_base64url(
            receipt.get("external_signature"),
            "external_signature",
            expected_size=64,
        )
        payload_bytes = canonical_enforcement_json(payload)
        try:
            Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
                signature, payload_bytes
            )
        except InvalidSignature as exc:
            raise InvalidEnforcementReceiptError(
                "external_signature", "Stored receipt signature verification failed"
            ) from exc
        payload_hash = enforcement_payload_hash(payload)
        if receipt.get("signed_payload_hash") != payload_hash:
            raise InvalidEnforcementReceiptError(
                "signed_payload_hash", "Stored signed payload hash does not match"
            )
        expected = {
            "workspace_id": receipt.get("tenant_id"),
            "issuer_id": receipt.get("issuer_id"),
            "backend_id": receipt.get("backend_id"),
            "enforcement_identity": receipt.get("enforcement_identity"),
            "run_id": receipt.get("run_id"),
            "step_id": receipt.get("step_id"),
            "proposal_id": receipt.get("proposal_id"),
            "policy_id": receipt.get("policy_id"),
            "policy_revision": receipt.get("policy_revision"),
            "policy_hash": receipt.get("policy_hash"),
            "execution_envelope_hash": receipt.get("execution_envelope_hash"),
            "tool_spec_hash": receipt.get("tool_spec_hash"),
            "arguments_digest": receipt.get("arguments_digest"),
            "request_id": receipt.get("enforcement_request_id"),
            "request_hash": receipt.get("enforcement_request_hash"),
        }
        for field, value in expected.items():
            if payload.get(field) != value:
                raise InvalidEnforcementReceiptError(
                    field, f"Stored receipt {field} does not match its signed payload"
                )
        nonce_hash = hashlib.sha256(payload["nonce"].encode()).hexdigest()
        verification = receipt.get("signature_verification") or {}
        if verification.get("nonce_hash") != nonce_hash:
            raise InvalidEnforcementReceiptError(
                "nonce_hash", "Stored nonce hash does not match the signed payload"
            )
        fingerprint = hashlib.sha256(public_key_bytes).hexdigest()
        return {
            "contract_version": SIGNATURE_RECHECK_CONTRACT_VERSION,
            "receipt_id": receipt["id"],
            "historical_verification": True,
            "currently_valid": True,
            "issuer_id": issuer.issuer_id,
            "algorithm": algorithm.value,
            "key_id": key.key_id,
            "public_key_fingerprint": fingerprint,
            "signed_payload_hash": payload_hash,
            "checked_at": self._now_provider().astimezone(UTC).isoformat(),
            "limitations": [
                "signature_authenticates_payload_not_observation_truth",
                "hardware_attestation_not_verified",
                "original_execution_envelope_not_persisted",
            ],
        }

    @staticmethod
    def _normalize_payload(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise InvalidEnforcementReceiptError("payload", "Signed payload must be an object")
        try:
            encoded = canonical_enforcement_json(payload)
            normalized = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidEnforcementReceiptError(
                "payload", "Signed payload must be canonical JSON"
            ) from exc
        if len(encoded) > _MAX_SIGNED_PAYLOAD_BYTES:
            raise InvalidEnforcementReceiptError("payload", "Signed payload is too large")
        expected = {
            "contract_version",
            "request_id",
            "request_hash",
            "nonce",
            "workspace_id",
            "issuer_id",
            "backend_id",
            "enforcement_identity",
            "run_id",
            "step_id",
            "proposal_id",
            "policy_id",
            "policy_revision",
            "policy_hash",
            "execution_envelope_hash",
            "tool_spec_hash",
            "arguments_digest",
            "decision",
            "outcome",
            "observed_effects",
            "denied_effects",
            "credential_bindings",
            "image_digest",
            "toolchain_digest",
            "sandbox_id",
            "workload_id",
            "attestation",
            "issued_at",
        }
        if set(normalized) != expected:
            raise InvalidEnforcementReceiptError(
                "payload", "Signed receipt fields do not match the frozen contract"
            )
        if normalized["contract_version"] != SIGNED_ENFORCEMENT_RECEIPT_CONTRACT_VERSION:
            raise InvalidEnforcementReceiptError(
                "contract_version", "Signed receipt contract is unsupported"
            )
        string_fields = {
            "request_id",
            "request_hash",
            "nonce",
            "workspace_id",
            "issuer_id",
            "backend_id",
            "enforcement_identity",
            "run_id",
            "proposal_id",
            "policy_id",
            "policy_hash",
            "execution_envelope_hash",
            "tool_spec_hash",
            "arguments_digest",
            "decision",
            "outcome",
            "issued_at",
        }
        if any(
            not isinstance(normalized[field], str) or not normalized[field]
            for field in string_fields
        ):
            raise InvalidEnforcementReceiptError(
                "payload", "Signed receipt scalar fields are invalid"
            )
        if isinstance(normalized["policy_revision"], bool) or not isinstance(
            normalized["policy_revision"], int
        ) or normalized["policy_revision"] <= 0:
            raise InvalidEnforcementReceiptError(
                "policy_revision", "Signed receipt policy revision is invalid"
            )
        for field in (
            "step_id",
            "image_digest",
            "toolchain_digest",
            "sandbox_id",
            "workload_id",
        ):
            if normalized[field] is not None and (
                not isinstance(normalized[field], str) or not normalized[field]
            ):
                raise InvalidEnforcementReceiptError(
                    field, f"Signed receipt {field} is invalid"
                )
        for field in (
            "request_hash",
            "policy_hash",
            "execution_envelope_hash",
            "tool_spec_hash",
            "arguments_digest",
        ):
            if not _HASH.fullmatch(normalized[field]):
                raise InvalidEnforcementReceiptError(
                    field, f"Signed receipt {field} must be a SHA-256 digest"
                )
        return normalized

    @staticmethod
    def _verify_bindings(
        request: EnforcerRequest,
        payload: dict[str, Any],
        issuer: dict[str, Any],
    ) -> None:
        policy = request.policy
        expected = {
            "request_id": request.request_id,
            "request_hash": request.request_hash,
            "nonce": request.nonce,
            "workspace_id": request.workspace_id,
            "issuer_id": request.expected_issuer_id,
            "backend_id": issuer["backend_id"],
            "enforcement_identity": issuer["identity"],
            "run_id": request.run_id,
            "step_id": request.step_id,
            "proposal_id": request.proposal_id,
            "policy_id": policy.get("policy_id"),
            "policy_revision": policy.get("revision"),
            "policy_hash": request.policy_hash,
            "execution_envelope_hash": request.execution_envelope_hash,
            "tool_spec_hash": request.tool_spec_hash,
            "arguments_digest": request.arguments_digest,
        }
        for field, value in expected.items():
            actual = payload.get(field)
            if actual != value or (
                value is not None and type(actual) is not type(value)
            ):
                raise InvalidEnforcementReceiptError(
                    field, f"Signed receipt {field} does not match the frozen request"
                )

    @staticmethod
    def _enum(enum_type, value: Any, field: str):
        try:
            return enum_type(value)
        except (TypeError, ValueError) as exc:
            raise InvalidEnforcementReceiptError(field, f"Receipt {field} is invalid") from exc

    @staticmethod
    def _mapping_sequence(value: Any, field: str) -> tuple[dict[str, Any], ...]:
        if not isinstance(value, list) or len(value) > _MAX_EFFECTS:
            raise InvalidEnforcementReceiptError(field, f"Receipt {field} is invalid")
        if any(not isinstance(item, dict) for item in value):
            raise InvalidEnforcementReceiptError(field, f"Receipt {field} is invalid")
        return tuple(value)


class ExternalEnforcerService:
    """Build host-owned requests, dispatch them, verify, then persist evidence."""

    def __init__(
        self,
        *,
        enforcement_service: EnforcementService,
        adapter_registry: EnforcerAdapterRegistry,
        verifier: SignedEnforcementReceiptVerifier,
        repository_provider: RepositoryProvider = get_repository,
        now_provider: NowProvider = _utc_now,
    ) -> None:
        self._enforcement_service = enforcement_service
        self._adapter_registry = adapter_registry
        self._verifier = verifier
        self._repository_provider = repository_provider
        self._now_provider = now_provider

    def list_adapters(self) -> list[dict[str, Any]]:
        return self._adapter_registry.list()

    def list_verification_keys(self) -> list[dict[str, Any]]:
        return self._verifier.list_keys()

    def get_dispatch(
        self, context: RequestContext, dispatch_id: str
    ) -> dict[str, Any]:
        value = self._repository_provider().get_enforcement_dispatch(
            context.workspace_id, dispatch_id
        )
        if value is None:
            raise ResourceNotFoundError("enforcement_dispatch", dispatch_id)
        return value

    def list_dispatches(
        self,
        context: RequestContext,
        *,
        adapter_id: str | None = None,
        proposal_id: str | None = None,
        state: EnforcementDispatchState | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self._repository_provider().list_enforcement_dispatches(
            context.workspace_id,
            adapter_id=adapter_id,
            proposal_id=proposal_id,
            state=state.value if state else None,
            limit=max(1, min(limit, 500)),
        )

    def unresolved_dispatches(
        self,
        context: RequestContext,
        *,
        adapter_id: str,
        proposal_id: str | None = None,
    ) -> list[dict[str, Any]]:
        values = []
        for state in (
            EnforcementDispatchState.DISPATCHING,
            EnforcementDispatchState.INDETERMINATE,
        ):
            values.extend(
                self.list_dispatches(
                    context,
                    adapter_id=adapter_id,
                    proposal_id=proposal_id,
                    state=state,
                    limit=500,
                )
            )
        return sorted(
            values,
            key=lambda item: (item["updated_at"], item["id"]),
            reverse=True,
        )

    def verify_stored_receipt(
        self, context: RequestContext, receipt_id: str
    ) -> dict[str, Any]:
        receipt = self._enforcement_service.get_receipt(context, receipt_id)
        return self._verifier.verify_stored_receipt(receipt)

    async def dispatch(
        self,
        context: RequestContext,
        *,
        adapter_id: str,
        proposal_id: str,
        run_id: str,
        step_id: str | None,
        execution_envelope: dict[str, Any],
        tool_spec_hash: str,
        arguments_digest: str,
        expires_in_seconds: float = 30.0,
        binding_id: str | None = None,
        original_dispatch_id: str | None = None,
    ) -> dict[str, Any]:
        if (
            not isinstance(expires_in_seconds, (int, float))
            or isinstance(expires_in_seconds, bool)
            or not isfinite(float(expires_in_seconds))
            or not 0.1 <= float(expires_in_seconds) <= _MAX_DISPATCH_SECONDS
        ):
            raise InvalidExecutionPolicyError(
                "expires_in_seconds", "Enforcer request timeout must be between 0.1 and 300 seconds"
            )
        for field, value in (
            ("tool_spec_hash", tool_spec_hash),
            ("arguments_digest", arguments_digest),
        ):
            if not _HASH.fullmatch(value):
                raise InvalidExecutionPolicyError(field, f"{field} must be a SHA-256 digest")
        if redact(execution_envelope) != execution_envelope:
            raise InvalidExecutionPolicyError(
                "execution_envelope",
                "Execution envelope must use credential references, not credential material",
            )
        try:
            envelope_bytes = canonical_enforcement_json(execution_envelope)
        except (TypeError, ValueError) as exc:
            raise InvalidExecutionPolicyError(
                "execution_envelope", "Execution envelope must be canonical JSON"
            ) from exc
        if len(envelope_bytes) > _MAX_SIGNED_PAYLOAD_BYTES:
            raise InvalidExecutionPolicyError(
                "execution_envelope", "Execution envelope is too large"
            )

        repository = self._repository_provider()
        run = repository.get_run(context.workspace_id, run_id)
        if run is None:
            raise ResourceNotFoundError("agent_run", run_id)
        if step_id is not None and not any(
            item.get("id") == step_id for item in run.get("steps", [])
        ):
            raise ResourceNotFoundError("run_step", step_id)
        proposal = self._enforcement_service.get_policy_proposal(context, proposal_id)
        adapter = self._adapter_registry.get(adapter_id)
        if adapter.issuer_id not in {
            item["issuer_id"] for item in self._enforcement_service.list_issuers()
        }:
            raise EnforcerNotConfiguredError(adapter.issuer_id)
        if (
            adapter.target_type.value != proposal["target_type"]
            or adapter.target_id != proposal["target_id"]
        ):
            raise InvalidExecutionPolicyError(
                "adapter_id", "Enforcer adapter target does not match the frozen proposal"
            )

        now = self._now_provider().astimezone(UTC)
        expires_at = now + timedelta(seconds=float(expires_in_seconds))
        request = EnforcerRequest(
            request_id=str(uuid4()),
            nonce=secrets.token_urlsafe(32),
            workspace_id=context.workspace_id,
            run_id=run_id,
            step_id=step_id,
            proposal_id=proposal_id,
            expected_issuer_id=adapter.issuer_id,
            target_type=adapter.target_type,
            target_id=adapter.target_id,
            policy=proposal["candidate_policy"],
            policy_hash=proposal["candidate_policy_hash"],
            permission_diff=proposal["permission_diff"],
            diff_hash=proposal["diff_hash"],
            execution_envelope=execution_envelope,
            execution_envelope_hash=enforcement_payload_hash(execution_envelope),
            tool_spec_hash=tool_spec_hash,
            arguments_digest=arguments_digest,
            requested_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )
        dispatch_values = {
            "id": request.request_id,
            "contract_version": ENFORCEMENT_DISPATCH_CONTRACT_VERSION,
            "binding_id": binding_id,
            "adapter_id": adapter.adapter_id,
            "issuer_id": adapter.issuer_id,
            "target_type": adapter.target_type.value,
            "target_id": adapter.target_id,
            "run_id": run_id,
            "step_id": step_id,
            "proposal_id": proposal_id,
            "original_dispatch_id": original_dispatch_id,
            "request_hash": request.request_hash,
            "execution_envelope_hash": request.execution_envelope_hash,
            "tool_spec_hash": tool_spec_hash,
            "arguments_digest": arguments_digest,
            "requested_at": request.requested_at,
            "expires_at": request.expires_at,
            "state": EnforcementDispatchState.DISPATCHING.value,
        }
        try:
            repository.create_enforcement_dispatch(
                context.workspace_id, dispatch_values
            )
        except Exception as exc:
            unresolved = self.unresolved_dispatches(
                context,
                adapter_id=adapter.adapter_id,
            )
            if unresolved:
                raise EnforcementDispatchIndeterminateError(
                    unresolved[0]["id"], "active_adapter_conflict"
                ) from exc
            if original_dispatch_id:
                active_reconciliations = []
                for state in (
                    EnforcementDispatchState.DISPATCHING,
                    EnforcementDispatchState.INDETERMINATE,
                ):
                    active_reconciliations.extend(
                        repository.list_enforcement_dispatches(
                            context.workspace_id,
                            original_dispatch_id=original_dispatch_id,
                            state=state.value,
                            limit=500,
                        )
                    )
                if active_reconciliations:
                    raise EnforcementDispatchIndeterminateError(
                        active_reconciliations[0]["id"],
                        "active_reconciliation_conflict",
                    ) from exc
            raise
        try:
            async with asyncio.timeout(float(expires_in_seconds)):
                signed = await adapter.enforce(request)
        except TimeoutError as exc:
            self._mark_indeterminate(
                context.workspace_id,
                request.request_id,
                ErrorCode.ENFORCER_REQUEST_FAILED.value,
            )
            raise EnforcementDispatchIndeterminateError(
                request.request_id, "timeout"
            ) from exc
        except asyncio.CancelledError:
            self._mark_indeterminate(
                context.workspace_id,
                request.request_id,
                ErrorCode.AGENT_CANCELLED.value,
            )
            raise
        except Exception as exc:
            self._mark_indeterminate(
                context.workspace_id,
                request.request_id,
                (
                    exc.code.value
                    if isinstance(exc, ApplicationError)
                    else ErrorCode.ENFORCER_REQUEST_FAILED.value
                ),
            )
            raise EnforcementDispatchIndeterminateError(
                request.request_id, "transport_or_adapter_failure"
            ) from exc
        try:
            verified = self._verifier.verify(request, signed)
            receipt = self._enforcement_service.record_verified_receipt(
                context.workspace_id, verified=verified
            )
        except Exception as exc:
            self._mark_indeterminate(
                context.workspace_id,
                request.request_id,
                (
                    exc.code.value
                    if isinstance(exc, ApplicationError)
                    else ErrorCode.INVALID_ENFORCEMENT_RECEIPT.value
                ),
            )
            raise EnforcementDispatchIndeterminateError(
                request.request_id, "unverifiable_receipt"
            ) from exc

        state = (
            EnforcementDispatchState.INDETERMINATE
            if verified.draft.outcome
            in {EnforcementOutcome.TIMED_OUT, EnforcementOutcome.INDETERMINATE}
            else EnforcementDispatchState.TERMINAL
        )
        repository.finish_enforcement_dispatch(
            context.workspace_id,
            request.request_id,
            state=state.value,
            receipt_id=receipt["id"],
            workload_id=receipt.get("workload_id"),
        )
        if original_dispatch_id and state is EnforcementDispatchState.TERMINAL:
            self._close_reconciliation_family(
                context.workspace_id,
                original_dispatch_id,
                receipt_id=receipt["id"],
                workload_id=receipt.get("workload_id"),
            )
        receipt["dispatch_id"] = request.request_id
        receipt["dispatch_state"] = state.value
        return receipt

    async def reconcile_dispatch(
        self,
        context: RequestContext,
        dispatch_id: str,
        *,
        expires_in_seconds: float = 30.0,
    ) -> dict[str, Any]:
        requested = self.get_dispatch(context, dispatch_id)
        root_id = requested.get("original_dispatch_id") or requested["id"]
        root = self.get_dispatch(context, root_id)
        if root["state"] not in {
            EnforcementDispatchState.DISPATCHING.value,
            EnforcementDispatchState.INDETERMINATE.value,
        }:
            raise InvalidExecutionPolicyError(
                "dispatch_id", "Only an indeterminate dispatch can be reconciled"
            )
        if (
            root["state"] == EnforcementDispatchState.DISPATCHING.value
            and self._now_provider().astimezone(UTC)
            <= _timestamp(root["expires_at"], "expires_at")
        ):
            raise InvalidExecutionPolicyError(
                "dispatch_id", "Dispatch is still within its active request window"
            )
        return await self.dispatch(
            context,
            adapter_id=root["adapter_id"],
            proposal_id=root["proposal_id"],
            run_id=root["run_id"],
            step_id=root.get("step_id"),
            execution_envelope={
                "operation": "enforcement.reconcile.v1",
                "original_dispatch_id": root["id"],
                "original_request_hash": root["request_hash"],
                "workload_id": root.get("workload_id"),
            },
            tool_spec_hash=root["tool_spec_hash"],
            arguments_digest=root["arguments_digest"],
            expires_in_seconds=expires_in_seconds,
            # Reconciliation observes the existing workload; it does not open
            # a second execution slot for the bound tool.
            binding_id=None,
            original_dispatch_id=root["id"],
        )

    def _mark_indeterminate(
        self, workspace_id: str, dispatch_id: str, error_code: str
    ) -> None:
        self._repository_provider().finish_enforcement_dispatch(
            workspace_id,
            dispatch_id,
            state=EnforcementDispatchState.INDETERMINATE.value,
            last_error_code=error_code,
        )

    def _close_reconciliation_family(
        self,
        workspace_id: str,
        root_dispatch_id: str,
        *,
        receipt_id: str,
        workload_id: str | None,
    ) -> None:
        repository = self._repository_provider()
        root = repository.get_enforcement_dispatch(workspace_id, root_dispatch_id)
        if root is None:
            raise ResourceNotFoundError("enforcement_dispatch", root_dispatch_id)
        candidates = [root]
        for state in (
            EnforcementDispatchState.DISPATCHING,
            EnforcementDispatchState.INDETERMINATE,
        ):
            candidates.extend(
                repository.list_enforcement_dispatches(
                    workspace_id,
                    original_dispatch_id=root_dispatch_id,
                    state=state.value,
                    limit=500,
                )
            )
        for item in candidates:
            if item["state"] in {
                EnforcementDispatchState.DISPATCHING.value,
                EnforcementDispatchState.INDETERMINATE.value,
            }:
                repository.finish_enforcement_dispatch(
                    workspace_id,
                    item["id"],
                    state=EnforcementDispatchState.RECONCILED.value,
                    receipt_id=receipt_id,
                    workload_id=workload_id,
                )
