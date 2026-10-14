"""Offline, candidate-only replay of a frozen MDE certificate input.

The two expected digests must come from a trusted source outside INPUT_JSON.
The keyring in INPUT_JSON is a replayed host-enrollment *assertion*, not proof
of rightful key ownership, revocation state, or real-world source history.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import sys
from pathlib import Path

from app.profiles.bio_observation_provenance import TrustedRecordKey

from .bio import TrustedLineageKey
from .bio_certificate import certify_bio_refutation
from .certificate_common import encode_certificate
from .citation_certificate import certify_citation_positive

REPLAY_VERSION = "mde.certificate-replay.v0"
BIO_KIND = "bio.parent-source-refutation.v0"
CITATION_KIND = "citation.positive-conditional-disambiguation.v0"
MAX_REPLAY_INPUT_BYTES = 8_388_608
_MAX_DEPTH = 64
_MAX_NODES = 32_768
_MAX_TEXT_BYTES = 4_194_304
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BIO_FIELDS = {
    "instrument_b64",
    "custodian_b64",
    "lineage_record_b64",
    "trusted_lineage_keys",
    "trusted_record_keys",
    "reduced_lineage_by_sample",
    "ablation_context",
}
_CITATION_FIELDS = {
    "bundle",
    "match_ids",
    "identity_attestations",
    "lineage_attestation",
    "identity_trust_roots",
    "lineage_trust_roots",
    "reduced_lineage_by_unit",
}


def _pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Replay input contains a duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("Replay input contains a non-finite number")


def _check_json_tree(value: object) -> None:
    """Bound nested untrusted JSON before invoking any domain checker."""
    stack = [(value, 0)]
    nodes = 0
    text_bytes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_NODES or depth > _MAX_DEPTH:
            raise ValueError("Replay input exceeds JSON structure limits")
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("Replay input has a non-string JSON key")
                text_bytes += len(key.encode("utf-8"))
                stack.append((child, depth + 1))
        elif type(item) is list:
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            text_bytes += len(item.encode("utf-8"))
        elif type(item) is int:
            if item.bit_length() > 256:
                raise ValueError("Replay input integer exceeds resource limit")
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError("Replay input contains a non-finite number")
        elif item is None or type(item) is bool:
            pass
        else:
            raise ValueError("Replay input is not JSON data")
        if text_bytes > _MAX_TEXT_BYTES:
            raise ValueError("Replay input exceeds text resource limit")


def _object(value: object, expected: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{label} has an invalid field set")
    return value


def _list(value: object, limit: int, label: str) -> list:
    if type(value) is not list or len(value) > limit:
        raise ValueError(f"{label} is not a bounded list")
    return value


def _nonempty_text(value: object, label: str) -> str:
    if type(value) is not str or not value or len(value) > 256 or value != value.strip():
        raise ValueError(f"{label} must be bounded nonempty text")
    return value


def _raw_bytes(value: object, label: str, *, max_bytes: int) -> bytes:
    if type(value) is not str or len(value) > ((max_bytes + 2) // 3) * 4:
        raise ValueError(f"{label} is not bounded base64")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{label} is not standard base64") from exc
    if len(decoded) > max_bytes or base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError(f"{label} is not canonical bounded base64")
    return decoded


def _public_key(value: object, label: str) -> bytes:
    key = _raw_bytes(value, label, max_bytes=32)
    if len(key) != 32:
        raise ValueError(f"{label} must identify an Ed25519 public key")
    return key


def parse_replay_input(raw: bytes) -> dict:
    """Read a bounded, exact-schema replay input without trusting its claims."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_REPLAY_INPUT_BYTES:
        raise ValueError("Replay input bytes are missing or too large")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_nonfinite,
        )
        _check_json_tree(value)
    except (UnicodeError, RecursionError, MemoryError, OverflowError) as exc:
        raise ValueError("Replay input could not be safely decoded") from exc
    if type(value) is not dict or value.get("replay_version") != REPLAY_VERSION:
        raise ValueError("Unknown replay input version")
    case = value.get("case")
    if case == BIO_KIND:
        _object(value, {"replay_version", "case", "bio"}, "Bio replay input")
        _object(value["bio"], _BIO_FIELDS, "Bio payload")
    elif case == CITATION_KIND:
        _object(value, {"replay_version", "case", "citation"}, "Citation replay input")
        _object(value["citation"], _CITATION_FIELDS, "Citation payload")
    else:
        raise ValueError("Unknown replay Case")
    return value


def _bio_certificate(payload: dict) -> dict:
    lineage_keys = []
    for row in _list(payload["trusted_lineage_keys"], 64, "Bio lineage keyring"):
        item = _object(
            row,
            {"issuer_id", "key_id", "parent_registry", "public_key_b64"},
            "Bio lineage key",
        )
        lineage_keys.append(TrustedLineageKey(
            issuer_id=_nonempty_text(item["issuer_id"], "lineage issuer"),
            key_id=_nonempty_text(item["key_id"], "lineage key ID"),
            parent_registry=_nonempty_text(item["parent_registry"], "parent registry"),
            public_key=_public_key(item["public_key_b64"], "lineage public key"),
        ))
    record_keys = []
    for row in _list(payload["trusted_record_keys"], 64, "Bio L1 keyring"):
        item = _object(
            row, {"role", "issuer_id", "key_id", "public_key_b64"}, "Bio L1 key"
        )
        record_keys.append(TrustedRecordKey(
            role=_nonempty_text(item["role"], "L1 role"),
            issuer_id=_nonempty_text(item["issuer_id"], "L1 issuer"),
            key_id=_nonempty_text(item["key_id"], "L1 key ID"),
            public_key=_public_key(item["public_key_b64"], "L1 public key"),
        ))
    reductions = payload["reduced_lineage_by_sample"]
    if type(reductions) is not dict or len(reductions) > 64:
        raise ValueError("Bio reductions must be a bounded object")
    decoded_reductions = {
        _nonempty_text(sample_id, "Bio reduction sample ID"): _raw_bytes(
            raw, "Bio reduced lineage", max_bytes=100_000
        )
        for sample_id, raw in reductions.items()
    }
    context = payload["ablation_context"]
    if type(context) is not str or context not in {
        "synthetic_test_resigning", "caller_supplied_counterfactual"
    }:
        raise ValueError("Unknown Bio ablation context")
    return certify_bio_refutation(
        _raw_bytes(payload["instrument_b64"], "instrument", max_bytes=1_000_000),
        _raw_bytes(payload["custodian_b64"], "custodian", max_bytes=1_000_000),
        _raw_bytes(payload["lineage_record_b64"], "lineage", max_bytes=100_000),
        lineage_keys,
        record_keys,
        reduced_lineage_by_sample=decoded_reductions,
        ablation_context=context,
    )


def _citation_roots(value: object, label: str) -> dict[tuple[str, str], bytes]:
    result: dict[tuple[str, str], bytes] = {}
    for row in _list(value, 128, label):
        item = _object(row, {"issuer_id", "key_id", "public_key_b64"}, label)
        identity = (
            _nonempty_text(item["issuer_id"], f"{label} issuer"),
            _nonempty_text(item["key_id"], f"{label} key ID"),
        )
        if identity in result:
            raise ValueError(f"{label} repeats an enrolled key identity")
        result[identity] = _public_key(item["public_key_b64"], f"{label} public key")
    return result


def _citation_certificate(payload: dict) -> dict:
    if type(payload["bundle"]) is not dict:
        raise ValueError("Citation candidate bundle must be an object")
    match_ids = _list(payload["match_ids"], 2, "Citation match IDs")
    if len(match_ids) != 2:
        raise ValueError("Citation requires exactly two match IDs")
    selected = tuple(_nonempty_text(item, "Citation match ID") for item in match_ids)
    if len(set(selected)) != 2:
        raise ValueError("Citation match IDs must be distinct")
    identities = _list(payload["identity_attestations"], 2, "Citation identities")
    if len(identities) != 2 or any(type(item) is not dict for item in identities):
        raise ValueError("Citation requires two identity attestations")
    if type(payload["lineage_attestation"]) is not dict:
        raise ValueError("Citation lineage attestation must be an object")
    reductions = payload["reduced_lineage_by_unit"]
    if type(reductions) is not dict or len(reductions) > 32:
        raise ValueError("Citation reductions must be a bounded object")
    return certify_citation_positive(
        payload["bundle"],
        selected,
        identities,
        payload["lineage_attestation"],
        identity_trust_roots=_citation_roots(
            payload["identity_trust_roots"], "Citation identity keyring"
        ),
        lineage_trust_roots=_citation_roots(
            payload["lineage_trust_roots"], "Citation lineage keyring"
        ),
        reduced_lineage_by_unit=reductions,
    )


def replay_input(
    raw: bytes,
    *,
    expected_input_sha256: str,
    expected_certificate_json_sha256: str,
) -> bytes:
    """Recompute canonical certificate bytes under externally pinned inputs.

    Neither expectation may be read from the candidate input. Hash matches
    establish deterministic replay, not independent source truth or authority.
    """
    for label, digest in (
        ("input", expected_input_sha256),
        ("certificate JSON", expected_certificate_json_sha256),
    ):
        if type(digest) is not str or _SHA256.fullmatch(digest) is None:
            raise ValueError(f"Expected {label} SHA-256 must be lowercase hex")
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_REPLAY_INPUT_BYTES:
        raise ValueError("Replay input bytes are missing or too large")
    actual_input = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(actual_input, expected_input_sha256):
        raise ValueError("Frozen replay input SHA-256 does not match")
    request = parse_replay_input(raw)
    certificate = (
        _bio_certificate(request["bio"])
        if request["case"] == BIO_KIND
        else _citation_certificate(request["citation"])
    )
    encoded = encode_certificate(certificate)
    actual_certificate = hashlib.sha256(encoded).hexdigest()
    if not hmac.compare_digest(actual_certificate, expected_certificate_json_sha256):
        raise ValueError("Recomputed certificate JSON SHA-256 does not match")
    return encoded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_json", type=Path)
    parser.add_argument("--expected-input-sha256", required=True)
    parser.add_argument("--expected-certificate-json-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        with args.input_json.open("rb") as source:
            raw = source.read(MAX_REPLAY_INPUT_BYTES + 1)
        result = replay_input(
            raw,
            expected_input_sha256=args.expected_input_sha256,
            expected_certificate_json_sha256=args.expected_certificate_json_sha256,
        )
    except (OSError, ValueError) as exc:
        parser.exit(2, f"certificate replay refused: {exc}\n")
    sys.stdout.buffer.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
