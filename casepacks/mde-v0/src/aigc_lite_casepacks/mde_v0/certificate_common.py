"""Canonical identity for candidate-only MDE disambiguation certificates.

The digest identifies a deterministic JSON body. It is not a signature,
QualificationReceipt, or independent authentication of source assertions.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

CERTIFICATE_VERSION = "mde.disambiguation-certificate.v0"
_CERTIFICATE_KINDS = {
    "bio.parent-source-refutation.v0",
    "citation.positive-conditional-disambiguation.v0",
}
_AUTHORITY_FLAGS = ("qualification_granted", "current_use_bound", "authorization_granted")
_HASH_DOMAIN = b"aigc-lite.mde.disambiguation-certificate.v0\n"
_MAX_CERTIFICATE_BYTES = 1_048_576
CERTIFICATE_CONTRACT_SHA256 = hashlib.sha256(
    Path(__file__).with_name("certificate_contract_v0.json").read_bytes()
).hexdigest()


def canonical_json_bytes(value: dict) -> bytes:
    """Encode a bounded, strictly JSON certificate object reproducibly."""
    if type(value) is not dict:
        raise ValueError("Certificate body must be a JSON object")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeError, MemoryError) as exc:
        raise ValueError("Certificate body is not bounded canonical JSON") from exc
    if len(encoded) > _MAX_CERTIFICATE_BYTES:
        raise ValueError("Certificate exceeds the bounded JSON size")
    return encoded


def finalize_certificate(body: dict) -> dict:
    """Add a versioned, domain-separated digest without nondeterministic fields."""
    if body.get("certificate_version") != CERTIFICATE_VERSION:
        raise ValueError("Unknown certificate contract version")
    if "certificate_sha256" in body:
        raise ValueError("Certificate body already has an identity")
    kind = body.get("certificate_kind")
    if (
        type(kind) is not str
        or kind not in _CERTIFICATE_KINDS
        or body.get("candidate_only") is not True
        or any(body.get(flag) is not False for flag in _AUTHORITY_FLAGS)
    ):
        raise ValueError("Certificate is outside the candidate-only v0 contract")
    declared_contract = body.get("certificate_contract_sha256", CERTIFICATE_CONTRACT_SHA256)
    if declared_contract != CERTIFICATE_CONTRACT_SHA256:
        raise ValueError("Certificate contract bytes do not match this checker")
    bound_body = {**body, "certificate_contract_sha256": CERTIFICATE_CONTRACT_SHA256}
    encoded = canonical_json_bytes(bound_body)
    digest = hashlib.sha256(_HASH_DOMAIN + encoded).hexdigest()
    return {**bound_body, "certificate_sha256": digest}


def verify_certificate_hash(certificate: dict) -> bool:
    """Check bytes-to-digest consistency, not the underlying MDE reasoning."""
    if type(certificate) is not dict:
        return False
    recorded = certificate.get("certificate_sha256")
    if type(recorded) is not str or len(recorded) != 64:
        return False
    try:
        rebuilt = finalize_certificate(
            {key: value for key, value in certificate.items() if key != "certificate_sha256"}
        )
    except ValueError:
        return False
    return hmac.compare_digest(recorded, rebuilt["certificate_sha256"])


def encode_certificate(certificate: dict) -> bytes:
    """Return the unique JSON bytes for a locally hash-consistent certificate."""
    if not verify_certificate_hash(certificate):
        raise ValueError("Certificate identity does not match its body")
    return canonical_json_bytes(certificate)


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate certificate JSON key")
        value[key] = item
    return value


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("Non-finite certificate JSON number")


def parse_certificate(raw: bytes) -> dict:
    """Reject noncanonical bytes, duplicate keys, and a mismatched digest.

    This checks serialization integrity only. Domain replay must regenerate
    the certificate from original inputs and historical host trust roots.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= _MAX_CERTIFICATE_BYTES:
        raise ValueError("Certificate JSON bytes are missing or too large")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
        if canonical_json_bytes(value) != raw or not verify_certificate_hash(value):
            raise ValueError("Certificate JSON is noncanonical or has wrong identity")
    except (RecursionError, UnicodeError, MemoryError, TypeError) as exc:
        raise ValueError("Certificate JSON could not be validated") from exc
    return value
