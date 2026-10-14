"""Candidate-only biological-parent disambiguation from signed lineage records.

The host supplies enrolled lineage keys. A signature authenticates an issuer's
statement about exact L1 bytes; it cannot authenticate the physical samples or
establish quality control, statistical independence, or qualification.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.core.errors import InvalidEvidenceError
from app.profiles import bio_information_sufficiency as bio_v1_observation
from app.profiles import bio_observation_provenance as l1_record_verifier
from app.profiles.bio_information_sufficiency import CASE_ID, TARGET, _visible_records
from app.profiles.bio_observation_provenance import TrustedRecordKey, _verified

CONTRACT_VERSION = "bio.sample-lineage.mde.v0"
LINEAGE_SEMANTICS = "canonical_biological_parent_with_split_alias_resolution"
SIGNING_DOMAIN = b"aigc-lite.bio-sample-lineage.mde.v0\n"
CHECKER_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
# The versioned Bio contract is packaged beside this checker. The frozen v1
# case lock remains an external source artifact and is identified by digest.
CHECKER_DEPENDENCY_SHA256 = {
    "bio_mde_contract": "6fd7b26d9d91ed0146b5be75b829ec956eee2c0188facbacd3bc8337c478b81f",
    "bio_v1_observation": hashlib.sha256(
        Path(bio_v1_observation.__file__).read_bytes()
    ).hexdigest(),
    "l1_record_signature_verifier": hashlib.sha256(
        Path(l1_record_verifier.__file__).read_bytes()
    ).hexdigest(),
    "bio_v1_case_lock": "1412c7ef497a2ad081559575697cc90e45c3267b00b510e3e1f83a0a0a0f937b",
}
_ENVELOPE_FIELDS = {"contract_version", "issuer_id", "key_id", "payload", "signature"}
_PAYLOAD_FIELDS = {
    "case_id",
    "target",
    "instrument_sha256",
    "custodian_sha256",
    "parent_registry",
    "lineage_semantics",
    "complete_for_recorded_samples",
    "sample_parents",
}
_MAPPING_FIELDS = {"sample_id", "canonical_parent_id"}
MAX_L1_RECORD_BYTES = 1_000_000
MAX_LINEAGE_RECORD_BYTES = 100_000
MAX_TOTAL_INPUT_BYTES = 3_000_000
MAX_LINEAGE_RECORDS = 64
MAX_TRUSTED_KEYS_PER_ROLE = 64
MAX_TRUST_LABEL_CHARS = 256
MAX_JSON_DEPTH = 64
_LIMITATIONS = [
    "A verified signature authenticates the issuer's statement, not the actual sample history.",
    "L1 record signatures are checked here; the L1 Gate and experiment replay are not run.",
    "Canonical parent IDs and completeness are assertions by the enrolled lineage issuer.",
    "Public-key fingerprints do not establish signer independence or signing-time revocation status.",
    "Distinct parent IDs do not establish QC, statistical independence, or broader biological validity.",
    "No qualification, current-use binding, or execution authorization is issued.",
]


@dataclass(frozen=True, slots=True)
class TrustedLineageKey:
    """Host-enrolled issuer authority for one canonical parent registry."""

    issuer_id: str
    key_id: str
    parent_registry: str
    public_key: bytes

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value and value == value.strip()
            for value in (self.issuer_id, self.key_id, self.parent_registry)
        ):
            raise ValueError("Lineage issuer, key ID, and parent registry are required")
        if not isinstance(self.public_key, bytes) or len(self.public_key) != 32:
            raise ValueError("Expected an enrolled Ed25519 public key")
        Ed25519PublicKey.from_public_bytes(self.public_key)


def signed_bytes(envelope: dict) -> bytes:
    """Exact versioned signing message for an external lineage issuer."""
    return SIGNING_DOMAIN + json.dumps(
        {key: envelope[key] for key in ("contract_version", "issuer_id", "key_id", "payload")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"Nonfinite JSON number: {value}")


def _check_json_depth(raw: bytes) -> None:
    """Bound nesting before json.loads while ignoring brackets inside strings."""
    depth = 0
    quoted = escaped = False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:  # backslash
                escaped = True
            elif char == 34:  # quote
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):  # [ or {
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("JSON nesting exceeds resource limit")
        elif char in (93, 125):  # ] or }
            depth -= 1
            if depth < 0:
                raise ValueError("Invalid JSON nesting")
    if depth or quoted:
        raise ValueError("Invalid JSON nesting or string")


def _read_json_object(raw: bytes, *, max_bytes: int) -> dict:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= max_bytes:
        raise ValueError("Expected bounded raw JSON bytes")
    _check_json_depth(raw)
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_no_duplicate_keys,
        parse_constant=_reject_nonfinite,
    )
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _keyring(trusted_keys: Sequence[TrustedLineageKey]) -> dict[tuple[str, str], TrustedLineageKey]:
    keys: dict[tuple[str, str], TrustedLineageKey] = {}
    for key in trusted_keys:
        if not isinstance(key, TrustedLineageKey):
            raise ValueError("Lineage trust roots must be host-enrolled TrustedLineageKey values")
        if any(
            len(value) > MAX_TRUST_LABEL_CHARS
            for value in (key.issuer_id, key.key_id, key.parent_registry)
        ):
            raise ValueError("Lineage trust metadata exceeds resource limit")
        identity = (key.issuer_id, key.key_id)
        if identity in keys or any(key.public_key == item.public_key for item in keys.values()):
            raise ValueError("Duplicate or shared enrolled lineage key")
        keys[identity] = key
    return keys


def _record_keyring(
    trusted_record_keys: Sequence[TrustedRecordKey],
) -> dict[tuple[str, str, str], bytes]:
    keys: dict[tuple[str, str, str], bytes] = {}
    for key in trusted_record_keys:
        if not isinstance(key, TrustedRecordKey):
            raise ValueError("L1 trust roots must be host-enrolled TrustedRecordKey values")
        if any(
            len(value) > MAX_TRUST_LABEL_CHARS
            for value in (key.role, key.issuer_id, key.key_id)
        ):
            raise ValueError("L1 trust metadata exceeds resource limit")
        identity = (key.role, key.issuer_id, key.key_id)
        if identity in keys or key.public_key in keys.values():
            raise ValueError("Duplicate or shared enrolled L1 record key")
        keys[identity] = key.public_key
    return keys


def _trust_descriptors(
    record_keys: dict[tuple[str, str, str], bytes],
    lineage_keys: dict[tuple[str, str], TrustedLineageKey],
) -> list[dict]:
    values = [
        {
            "role": role,
            "issuer_id": issuer,
            "key_id": key_id,
            "parent_registry": None,
            "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
        }
        for (role, issuer, key_id), public_key in record_keys.items()
    ]
    values.extend(
        {
            "role": "lineage",
            "issuer_id": issuer,
            "key_id": key_id,
            "parent_registry": key.parent_registry,
            "public_key_sha256": hashlib.sha256(key.public_key).hexdigest(),
        }
        for (issuer, key_id), key in lineage_keys.items()
    )
    return sorted(
        values,
        key=lambda item: (
            item["role"],
            item["issuer_id"],
            item["key_id"],
            item["parent_registry"] or "",
            item["public_key_sha256"],
        ),
    )


def _trust_configuration_sha256(descriptors: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(descriptors, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _verify_lineage(
    envelope: dict,
    keys: dict[tuple[str, str], TrustedLineageKey],
    instrument_sha256: str,
    custodian_sha256: str,
    sample_ids: set[str],
) -> tuple[str, str, bool, dict[str, str]]:
    if set(envelope) != _ENVELOPE_FIELDS or envelope.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("Invalid lineage envelope or contract version")
    issuer = envelope.get("issuer_id")
    key_id = envelope.get("key_id")
    if not isinstance(issuer, str) or not isinstance(key_id, str):
        raise ValueError("Invalid lineage signer identity")
    key = keys.get((issuer, key_id))
    payload = envelope.get("payload")
    if key is None or not isinstance(payload, dict) or set(payload) != _PAYLOAD_FIELDS:
        raise ValueError("Unenrolled signer or invalid lineage payload")
    signature_text = envelope.get("signature")
    if not isinstance(signature_text, str) or len(signature_text) > 100:
        raise ValueError("Invalid lineage signature encoding")
    try:
        signature = base64.b64decode(
            signature_text.encode("ascii") + b"=" * (-len(signature_text) % 4),
            altchars=b"-_",
            validate=True,
        )
        if len(signature) != 64:
            raise ValueError("Invalid Ed25519 signature length")
        Ed25519PublicKey.from_public_bytes(key.public_key).verify(signature, signed_bytes(envelope))
    except (InvalidSignature, ValueError, KeyError, TypeError, UnicodeEncodeError, binascii.Error) as exc:
        raise ValueError("Lineage signature did not verify") from exc
    if payload["case_id"] != CASE_ID or payload["target"] != TARGET:
        raise ValueError("Lineage targets a different frozen question")
    if (
        payload["instrument_sha256"] != instrument_sha256
        or payload["custodian_sha256"] != custodian_sha256
    ):
        raise ValueError("Lineage does not bind the exact supplied L1 record bytes")
    registry = payload["parent_registry"]
    if registry != key.parent_registry or payload["lineage_semantics"] != LINEAGE_SEMANTICS:
        raise ValueError("Lineage registry or parent semantics are outside enrolled authority")
    complete = payload["complete_for_recorded_samples"]
    entries = payload["sample_parents"]
    if not isinstance(complete, bool) or not isinstance(entries, list) or len(entries) > 64:
        raise ValueError("Invalid lineage coverage declaration")
    mapping: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _MAPPING_FIELDS:
            raise ValueError("Invalid sample-parent mapping")
        sample_id = entry["sample_id"]
        parent_id = entry["canonical_parent_id"]
        if (
            not isinstance(sample_id, str)
            or sample_id not in sample_ids
            or sample_id in mapping
            or not isinstance(parent_id, str)
            or not parent_id
            or parent_id != parent_id.strip()
        ):
            raise ValueError("Unknown, duplicate, or invalid sample-parent mapping")
        mapping[sample_id] = parent_id
    if complete and set(mapping) != sample_ids:
        raise ValueError("Complete lineage attestation omits recorded samples")
    return issuer, registry, complete, mapping


def diagnose_bio_mde(
    instrument_bytes: bytes,
    custodian_bytes: bytes,
    lineage_records: Sequence[bytes],
    trusted_keys: Sequence[TrustedLineageKey],
    trusted_record_keys: Sequence[TrustedRecordKey],
) -> dict:
    """Decide only the frozen parent-distinctness target under signed lineage.

    A signed common parent refutes the target with partial coverage. A positive
    result needs a complete attestation and one canonical parent for each L1
    sample. Any invalid or conflicting supplied lineage fails closed.
    """
    report = {
        "case_id": CASE_ID,
        "contract_version": CONTRACT_VERSION,
        "checker_source_sha256": CHECKER_SOURCE_SHA256,
        "checker_dependency_sha256": CHECKER_DEPENDENCY_SHA256.copy(),
        "target": TARGET,
        "instrument_sha256": None,
        "custodian_sha256": None,
        "lineage_record_sha256": [],
        "trusted_public_keys": [],
        "trust_configuration_sha256": None,
        "verified_public_keys": [],
        "status": "UNRESOLVED",
        "reason": "invalid_l1_record_bytes",
        "lineage_assertion_authenticated": False,
        "l1_record_signatures_verified": False,
        "complete_coverage_attested": False,
        "covered_sample_ids": [],
        "missing_sample_ids": [],
        "trusted_issuer_id": None,
        "parent_registry": None,
        "qualification_granted": False,
        "limitations": list(_LIMITATIONS),
    }
    if not isinstance(instrument_bytes, bytes) or not isinstance(custodian_bytes, bytes):
        report["reason"] = "invalid_input_type"
        return report
    if not isinstance(lineage_records, (list, tuple)):
        report["reason"] = "invalid_input_type"
        return report
    if not isinstance(trusted_keys, (list, tuple)) or not isinstance(
        trusted_record_keys, (list, tuple)
    ):
        report["reason"] = "invalid_trust_configuration"
        return report
    if (
        len(lineage_records) > MAX_LINEAGE_RECORDS
        or len(trusted_keys) > MAX_TRUSTED_KEYS_PER_ROLE
        or len(trusted_record_keys) > MAX_TRUSTED_KEYS_PER_ROLE
        or len(instrument_bytes) > MAX_L1_RECORD_BYTES
        or len(custodian_bytes) > MAX_L1_RECORD_BYTES
    ):
        report["reason"] = "resource_limit_exceeded"
        return report
    if any(not isinstance(raw, bytes) for raw in lineage_records):
        report["reason"] = "invalid_input_type"
        return report
    if (
        any(len(raw) > MAX_LINEAGE_RECORD_BYTES for raw in lineage_records)
        or len(instrument_bytes) + len(custodian_bytes)
        + sum(len(raw) for raw in lineage_records) > MAX_TOTAL_INPUT_BYTES
    ):
        report["reason"] = "resource_limit_exceeded"
        return report
    try:
        instrument_sha256 = hashlib.sha256(instrument_bytes).hexdigest()
        custodian_sha256 = hashlib.sha256(custodian_bytes).hexdigest()
        report["instrument_sha256"] = instrument_sha256
        report["custodian_sha256"] = custodian_sha256
        report["lineage_record_sha256"] = [
            hashlib.sha256(raw).hexdigest() for raw in lineage_records
        ]
    except (RecursionError, MemoryError):
        report["reason"] = "resource_limit_exceeded"
        return report
    try:
        record_keys = _record_keyring(trusted_record_keys)
        keys = _keyring(trusted_keys)
        descriptors = _trust_descriptors(record_keys, keys)
        report["trusted_public_keys"] = descriptors
        report["trust_configuration_sha256"] = _trust_configuration_sha256(descriptors)
    except (ValueError, TypeError, RecursionError, MemoryError):
        report["reason"] = "invalid_trust_configuration"
        return report
    try:
        instrument = _read_json_object(instrument_bytes, max_bytes=1_000_000)
        custodian = _read_json_object(custodian_bytes, max_bytes=1_000_000)
        _, samples = _visible_records(instrument, custodian)
        if (
            custodian["payload"]["study_id"] != instrument["payload"]["study"]["study_id"]
            or custodian["payload"]["instrument_sha256"] != instrument_sha256
        ):
            return report
    except (ValueError, UnicodeError, InvalidEvidenceError, RecursionError, MemoryError):
        return report
    try:
        l1_verified = (
            _verified(instrument, "instrument", record_keys)
            and _verified(custodian, "custodian", record_keys)
            and instrument["issuer_id"] != custodian["issuer_id"]
        )
    except (RecursionError, MemoryError):
        report["reason"] = "resource_limit_exceeded"
        return report
    if not l1_verified:
        report["reason"] = "l1_record_signatures_unverified"
        return report
    report["l1_record_signatures_verified"] = True
    report["verified_public_keys"] = [
        item
        for item in descriptors
        if (item["role"], item["issuer_id"], item["key_id"])
        in {
            ("instrument", instrument["issuer_id"], instrument["key_id"]),
            ("custodian", custodian["issuer_id"], custodian["key_id"]),
        }
    ]
    sample_ids = {entry["sample_id"] for entry in samples}
    report["missing_sample_ids"] = sorted(sample_ids)
    if not lineage_records:
        report["reason"] = "missing_lineage_record"
        return report
    mapping: dict[str, str] = {}
    issuer_id = registry_id = None
    complete_attested = False
    lineage_key_ids: set[str] = set()
    try:
        for raw in lineage_records:
            envelope = _read_json_object(raw, max_bytes=100_000)
            issuer, registry, complete, fragment = _verify_lineage(
                envelope, keys, instrument_sha256, custodian_sha256, sample_ids
            )
            if issuer_id is not None and (issuer != issuer_id or registry != registry_id):
                raise ValueError("Different lineage authorities or parent registries")
            issuer_id, registry_id = issuer, registry
            lineage_key_ids.add(envelope["key_id"])
            for sample_id, parent_id in fragment.items():
                if sample_id in mapping and mapping[sample_id] != parent_id:
                    raise ValueError("Conflicting signed parent assignments")
                mapping[sample_id] = parent_id
            complete_attested |= complete
    except (ValueError, UnicodeError, RecursionError, MemoryError):
        report["reason"] = "invalid_or_conflicting_lineage"
        return report
    report["lineage_assertion_authenticated"] = True
    report["trusted_issuer_id"] = issuer_id
    report["parent_registry"] = registry_id
    report["verified_public_keys"].extend(
        item
        for item in descriptors
        if item["role"] == "lineage"
        and item["issuer_id"] == issuer_id
        and item["key_id"] in lineage_key_ids
    )
    report["complete_coverage_attested"] = complete_attested
    report["covered_sample_ids"] = sorted(mapping)
    report["missing_sample_ids"] = sorted(sample_ids - mapping.keys())
    if len(set(mapping.values())) < len(mapping):
        report["status"] = "TARGET_FALSE_UNDER_ATTESTED_LINEAGE"
        report["reason"] = "signed_assertion_common_canonical_parent"
    elif complete_attested and set(mapping) == sample_ids:
        report["status"] = "TARGET_TRUE_UNDER_ATTESTED_LINEAGE"
        report["reason"] = "complete_distinct_canonical_parents"
    else:
        report["reason"] = "lineage_coverage_incomplete"
    return report
