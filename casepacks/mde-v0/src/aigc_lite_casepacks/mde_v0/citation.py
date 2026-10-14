"""Candidate-only MDE diagnostic for the frozen two-citation Case.

The trusted keys are supplied by the host, never by an evidence envelope. A
signature authenticates an issuer's statement under that enrollment; it does
not independently establish the truth of a publication or data history.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.core.errors import InvalidEvidenceError
from app.profiles import citation_information_sufficiency as citation_v1_observation
from app.profiles.citation_information_sufficiency import _visible_bundle

CASE_ID = "citation.primary-evidence-root-mde.v0"
IDENTITY_VERSION = "citation.target-identity-evidence.v0"
LINEAGE_VERSION = "citation.primary-root-lineage-evidence.v0"
UNRESOLVED = "UNRESOLVED"
TARGET_TRUE = "TARGET_TRUE_UNDER_ATTESTED_LINEAGE"
TARGET_FALSE = "TARGET_FALSE_UNDER_ATTESTED_LINEAGE"
CHECKER_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CHECKER_DEPENDENCY_SHA256 = {
    "frozen_v1_checker": hashlib.sha256(
        Path(citation_v1_observation.__file__).read_bytes()
    ).hexdigest(),
    # The lock is not packaged in the wheel. This source-tree value is pinned
    # by the test against the actual frozen lock bytes.
    "frozen_v1_case_lock": "163866d9cc7a9613414300444f2dbc9dbbe95330842daa6c27165702716fd108",
}

_ENVELOPE_FIELDS = {"contract_version", "issuer_id", "key_id", "payload", "signature"}
_IDENTITY_FIELDS = {
    "candidate_bundle_sha256",
    "match_id",
    "candidate_identifier",
    "canonical_work_id",
    "metadata_record_sha256",
}
_LINEAGE_FIELDS = {
    "candidate_bundle_sha256",
    "selected_match_ids",
    "works",
    "nodes",
    "edges",
    "alias_index_complete",
    "source_record_sha256",
}
_WORK_FIELDS = {
    "match_id",
    "canonical_work_id",
    "used_source_node_ids",
    "coverage_complete",
}
_NODE_FIELDS = {"node_id", "kind"}
_EDGE_FIELDS = {"kind", "from", "to"}
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_JSON_BYTES = 1_048_576
_MAX_JSON_NODES = 4_096
_MAX_JSON_DEPTH = 24
_MAX_TRUST_KEYS = 128
_LIMITATIONS = [
    "The signed records are synthetic issuer assertions in this Case, not independently verified publication or data histories.",
    "The source-record digests are format-checked but their underlying bytes are not fetched or verified here.",
    "Coverage and cross-work alias completeness are signer assertions, not independently measured by this diagnostic.",
    "Separate enrolled keys and issuer IDs do not establish organizational independence or real-world source truth.",
    "Historical replay needs the host trust-configuration snapshot; a fingerprint alone cannot establish past revocation state.",
    "The diagnostic does not establish claim support, statistical independence, qualification, current use, or execution authority.",
]


def _bounded_canonical_bytes(value: object) -> bytes:
    """Serialize bounded JSON without leaking resource exceptions."""
    try:
        return _bounded_canonical_bytes_impl(value)
    except (MemoryError, RecursionError) as exc:
        raise ValueError("Citation MDE canonical JSON resource limit exceeded") from exc


def _bounded_canonical_bytes_impl(value: object) -> bytes:
    """Traverse untrusted JSON iteratively before canonical serialization."""
    stack = [(value, 0)]
    seen_containers: set[int] = set()
    nodes = 0
    text_bytes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            raise ValueError("Citation MDE JSON resource limit exceeded")
        if type(item) is dict:
            if id(item) in seen_containers:
                raise ValueError("Citation MDE repeated or cyclic JSON container")
            seen_containers.add(id(item))
            if len(item) > _MAX_JSON_NODES - nodes:
                raise ValueError("Citation MDE JSON resource limit exceeded")
            for key, child in item.items():
                if type(key) is not str or len(key) > _MAX_JSON_BYTES:
                    raise ValueError("Citation MDE JSON key invalid or too large")
                try:
                    text_bytes += len(key.encode("utf-8"))
                except UnicodeError as exc:
                    raise ValueError("Citation MDE JSON key is not UTF-8") from exc
                stack.append((child, depth + 1))
        elif type(item) is list:
            if id(item) in seen_containers:
                raise ValueError("Citation MDE repeated or cyclic JSON container")
            seen_containers.add(id(item))
            if len(item) > _MAX_JSON_NODES - nodes:
                raise ValueError("Citation MDE JSON resource limit exceeded")
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if len(item) > _MAX_JSON_BYTES:
                raise ValueError("Citation MDE JSON resource limit exceeded")
            try:
                text_bytes += len(item.encode("utf-8"))
            except UnicodeError as exc:
                raise ValueError("Citation MDE JSON string is not UTF-8") from exc
        elif item is None or type(item) is bool:
            pass
        elif type(item) is int:
            if item.bit_length() > 256:
                raise ValueError("Citation MDE JSON integer resource limit exceeded")
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError("Citation MDE JSON number invalid")
        else:
            raise ValueError("Citation MDE input must be JSON data")
        if text_bytes > _MAX_JSON_BYTES:
            raise ValueError("Citation MDE JSON resource limit exceeded")
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, OverflowError, MemoryError, UnicodeError) as exc:
        raise ValueError("Citation MDE canonical JSON unavailable") from exc
    if len(encoded) > _MAX_JSON_BYTES:
        raise ValueError("Citation MDE JSON resource limit exceeded")
    return encoded


def _bounded_hash(value: object) -> str:
    return hashlib.sha256(_bounded_canonical_bytes(value)).hexdigest()


def _key_fingerprint(role: str, issuer_id: str, key_id: str, key: bytes) -> dict:
    return {
        "role": role,
        "issuer_id": issuer_id,
        "key_id": key_id,
        "public_key_sha256": hashlib.sha256(key).hexdigest(),
    }


def _trust_config_fingerprint(
    identity_roots: Mapping[tuple[str, str], bytes],
    lineage_roots: Mapping[tuple[str, str], bytes],
) -> tuple[str, list[dict]] | None:
    descriptors: list[dict] = []
    for role, roots in (("identity", identity_roots), ("lineage", lineage_roots)):
        if not isinstance(roots, Mapping) or len(roots) > _MAX_TRUST_KEYS:
            return None
        for entry, key in roots.items():
            if (
                not isinstance(entry, tuple)
                or len(entry) != 2
                or not _nonempty(entry[0])
                or not _nonempty(entry[1])
                or not isinstance(key, bytes)
                or len(key) != 32
            ):
                return None
            descriptors.append(_key_fingerprint(role, entry[0], entry[1], key))
    descriptors.sort(key=lambda item: (item["role"], item["issuer_id"], item["key_id"]))
    try:
        return _bounded_hash(descriptors), descriptors
    except (ValueError, MemoryError, RecursionError):
        return None


def citation_mde_signing_bytes(role: str, envelope: dict) -> bytes:
    """Return role-separated canonical bytes for an Ed25519 evidence envelope.

    This helper is for external source adapters and tests. The private key must
    remain with the source issuer; the verifier accepts only host-enrolled keys.
    """
    if role not in {"identity", "lineage"}:
        raise ValueError("Unknown Citation MDE signing role")
    if not isinstance(envelope, dict):
        raise ValueError("Invalid Citation MDE envelope")
    _bounded_canonical_bytes(envelope)
    if set(envelope) != _ENVELOPE_FIELDS:
        raise ValueError("Invalid Citation MDE envelope")
    signed = {field: envelope[field] for field in _ENVELOPE_FIELDS - {"signature"}}
    encoded = _bounded_canonical_bytes(signed)
    return b"citation-mde.v0\x00" + role.encode("ascii") + b"\x00" + encoded


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= 256


def _digest(value: object) -> bool:
    return isinstance(value, str) and _HEX_SHA256.fullmatch(value) is not None


def _verify_envelope(
    envelope: object,
    role: str,
    version: str,
    trust_roots: Mapping[tuple[str, str], bytes],
) -> tuple[dict, str, bytes] | None:
    if not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_FIELDS:
        return None
    issuer_id, key_id = envelope["issuer_id"], envelope["key_id"]
    if (
        envelope["contract_version"] != version
        or not _nonempty(issuer_id)
        or not _nonempty(key_id)
        or not isinstance(envelope["payload"], dict)
        or not isinstance(trust_roots, Mapping)
    ):
        return None
    key = trust_roots.get((issuer_id, key_id))
    signature_text = envelope["signature"]
    if not isinstance(key, bytes) or len(key) != 32 or not isinstance(signature_text, str):
        return None
    if len(signature_text) != 86 or not re.fullmatch(r"[A-Za-z0-9_-]{86}", signature_text):
        return None
    try:
        signature = base64.urlsafe_b64decode(signature_text + "==")
        if (
            len(signature) != 64
            or base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
            != signature_text
        ):
            return None
        Ed25519PublicKey.from_public_bytes(key).verify(
            signature, citation_mde_signing_bytes(role, envelope)
        )
    except (
        ValueError,
        TypeError,
        KeyError,
        InvalidSignature,
        UnicodeError,
        OverflowError,
        RecursionError,
        MemoryError,
    ):
        return None
    return envelope["payload"], issuer_id, key


def _identity_payload_valid(payload: dict, digest: str, match_id: str, target: str) -> bool:
    return (
        set(payload) == _IDENTITY_FIELDS
        and payload["candidate_bundle_sha256"] == digest
        and payload["match_id"] == match_id
        and payload["candidate_identifier"] == target
        and _nonempty(payload["canonical_work_id"])
        and _digest(payload["metadata_record_sha256"])
    )


def _lineage_payload_valid(payload: dict, digest: str, match_ids: tuple[str, str]) -> bool:
    if (
        set(payload) != _LINEAGE_FIELDS
        or payload["candidate_bundle_sha256"] != digest
        or payload["selected_match_ids"] != list(match_ids)
        or type(payload["alias_index_complete"]) is not bool
        or not _digest(payload["source_record_sha256"])
        or not isinstance(payload["works"], list)
        or len(payload["works"]) != 2
        or not isinstance(payload["nodes"], list)
        or not isinstance(payload["edges"], list)
        or len(payload["nodes"]) > 512
        or len(payload["edges"]) > 1024
    ):
        return False
    works = payload["works"]
    if any(not isinstance(work, dict) or set(work) != _WORK_FIELDS for work in works):
        return False
    if {work["match_id"] for work in works if isinstance(work["match_id"], str)} != set(
        match_ids
    ):
        return False
    for work in works:
        sources = work["used_source_node_ids"]
        if (
            not _nonempty(work["canonical_work_id"])
            or type(work["coverage_complete"]) is not bool
            or not isinstance(sources, list)
            or len(sources) > 512
            or any(not _nonempty(item) for item in sources)
            or len(set(sources)) != len(sources)
            or (work["coverage_complete"] and not sources)
        ):
            return False
    return True


def _roots_from_graph(payload: dict) -> tuple[dict[str, set[str]], dict[str, bool]]:
    """Resolve signed aliases and derivative parents to primary root classes.

    A partial graph may prove a common root. Every source path must terminate
    at a primary node before a disjointness finding is possible.
    """
    nodes: dict[str, str] = {}
    for node in payload["nodes"]:
        if not isinstance(node, dict) or set(node) != _NODE_FIELDS:
            raise ValueError("malformed node")
        node_id, kind = node["node_id"], node["kind"]
        if not _nonempty(node_id) or kind not in {"primary", "derivative"} or node_id in nodes:
            raise ValueError("duplicate or invalid node")
        nodes[node_id] = kind

    parent = {node_id: node_id for node_id in nodes}

    def find(node_id: str) -> str:
        while parent[node_id] != node_id:
            node_id = parent[node_id]
        return node_id

    edges = payload["edges"]
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) != _EDGE_FIELDS:
            raise ValueError("malformed edge")
        kind, child, ancestor = edge["kind"], edge["from"], edge["to"]
        if (
            kind not in {"alias_of", "derived_from"}
            or not isinstance(child, str)
            or not isinstance(ancestor, str)
            or child not in nodes
            or ancestor not in nodes
            or child == ancestor
        ):
            raise ValueError("invalid edge")
        if kind == "alias_of":
            if nodes[child] != nodes[ancestor]:
                raise ValueError("alias kind conflict")
            left, right = find(child), find(ancestor)
            parent[max(left, right)] = min(left, right)

    classes = {node_id: find(node_id) for node_id in nodes}
    kinds = {classes[node_id]: kind for node_id, kind in nodes.items()}
    parents: dict[str, set[str]] = {canonical: set() for canonical in kinds}
    for edge in edges:
        if edge["kind"] != "derived_from":
            continue
        child, ancestor = classes[edge["from"]], classes[edge["to"]]
        if child == ancestor or kinds[child] != "derivative":
            raise ValueError("derivation cycle or primary child")
        parents[child].add(ancestor)

    # Validate cycles globally, including disconnected signed graph components.
    visiting: set[str] = set()
    done: set[str] = set()

    def acyclic(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError("derivation cycle")
        if node_id in done:
            return
        visiting.add(node_id)
        for ancestor in parents[node_id]:
            acyclic(ancestor)
        visiting.remove(node_id)
        done.add(node_id)

    for node_id in kinds:
        acyclic(node_id)

    cache: dict[str, tuple[set[str], bool]] = {}

    def roots(node_id: str) -> tuple[set[str], bool]:
        if node_id in cache:
            return cache[node_id]
        if kinds[node_id] == "primary":
            result = ({node_id}, True)
        elif not parents[node_id]:
            result = (set(), False)
        else:
            root_set: set[str] = set()
            complete = True
            for ancestor in parents[node_id]:
                ancestor_roots, ancestor_complete = roots(ancestor)
                root_set.update(ancestor_roots)
                complete = complete and ancestor_complete
            result = (root_set, complete)
        cache[node_id] = result
        return result

    by_match: dict[str, set[str]] = {}
    paths_complete: dict[str, bool] = {}
    for work in payload["works"]:
        root_set: set[str] = set()
        complete = bool(work["used_source_node_ids"])
        for source_id in work["used_source_node_ids"]:
            if source_id not in classes:
                raise ValueError("source node missing from signed graph")
            source_roots, source_complete = roots(classes[source_id])
            root_set.update(source_roots)
            complete = complete and source_complete
        by_match[work["match_id"]] = root_set
        paths_complete[work["match_id"]] = complete
    return by_match, paths_complete


def diagnose_citation_mde(
    bundle: dict,
    match_ids: tuple[str, str],
    identity_attestations: list[dict],
    lineage_attestations: list[dict],
    *,
    identity_trust_roots: Mapping[tuple[str, str], bytes],
    lineage_trust_roots: Mapping[tuple[str, str], bytes],
) -> dict:
    """Ask if signed, bound evidence decides the frozen two-root target.

    Both trust maps must come from host configuration. An empty production map
    returns UNRESOLVED, as does any missing, conflicting, or invalid record.
    """
    result = {
        "case_id": CASE_ID,
        "checker_source_sha256": CHECKER_SOURCE_SHA256,
        "checker_dependency_sha256": dict(CHECKER_DEPENDENCY_SHA256),
        "status": UNRESOLVED,
        "reason": "evidence_missing_or_invalid",
        "qualification_granted": False,
        "candidate_bundle_sha256": None,
        "selected_match_ids": [],
        "selected_candidate_identifiers": [],
        "canonical_work_ids": [],
        "primary_roots_by_match_id": {},
        "coverage_complete": False,
        "identity_assertion_authenticated": False,
        "lineage_assertion_authenticated": False,
        "identity_issuer_ids": [],
        "lineage_issuer_id": None,
        "identity_attestation_sha256": [],
        "lineage_attestation_sha256": None,
        "identity_metadata_record_sha256": [],
        "lineage_source_record_sha256": None,
        "host_trust_config_sha256": None,
        "host_trust_key_fingerprints": [],
        "used_enrolled_key_fingerprints": [],
        "limitations": list(_LIMITATIONS),
    }
    try:
        _bounded_canonical_bytes(
            {
                "candidate_bundle": bundle,
                "identity_attestations": identity_attestations,
                "lineage_attestations": lineage_attestations,
            }
        )
    except (ValueError, MemoryError, RecursionError):
        result["reason"] = "resource_limits_or_non_json_input"
        return result
    if (
        type(match_ids) is not tuple
        or len(match_ids) != 2
        or any(type(match_id) is not str for match_id in match_ids)
    ):
        result["reason"] = "frozen_visible_bundle_invalid"
        return result
    if any(not _nonempty(match_id) for match_id in match_ids):
        result["reason"] = "resource_limits_or_non_json_input"
        return result
    try:
        targets = _visible_bundle(bundle, match_ids)
        bundle_sha256 = _bounded_hash(bundle)
    except (
        InvalidEvidenceError,
        ValueError,
        TypeError,
        RecursionError,
        OverflowError,
        MemoryError,
    ):
        result["reason"] = "frozen_visible_bundle_invalid"
        return result
    result["candidate_bundle_sha256"] = bundle_sha256
    result["selected_match_ids"] = list(match_ids)
    result["selected_candidate_identifiers"] = list(targets)
    try:
        trust_fingerprint = _trust_config_fingerprint(
            identity_trust_roots, lineage_trust_roots
        )
    except (MemoryError, RecursionError):
        trust_fingerprint = None
    if trust_fingerprint is None:
        result["reason"] = "host_trust_config_invalid"
        return result
    result["host_trust_config_sha256"], result["host_trust_key_fingerprints"] = (
        trust_fingerprint
    )
    if not isinstance(identity_attestations, list) or len(identity_attestations) != 2:
        result["reason"] = "missing_or_conflicting_identity_attestations"
        return result
    if not isinstance(lineage_attestations, list) or len(lineage_attestations) != 1:
        result["reason"] = "missing_or_conflicting_lineage_attestations"
        return result

    identities: dict[str, tuple[dict, str, bytes]] = {}
    identity_envelopes_by_match: dict[str, dict] = {}
    for envelope in identity_attestations:
        verified = _verify_envelope(
            envelope, "identity", IDENTITY_VERSION, identity_trust_roots
        )
        if verified is None:
            result["reason"] = "identity_signature_or_schema_invalid"
            return result
        payload, issuer_id, key = verified
        match_id = payload.get("match_id")
        if not isinstance(match_id, str) or match_id not in match_ids or match_id in identities:
            result["reason"] = "identity_binding_or_conflict"
            return result
        if not _identity_payload_valid(
            payload, bundle_sha256, match_id, targets[match_ids.index(match_id)]
        ):
            result["reason"] = "identity_binding_or_conflict"
            return result
        identities[match_id] = payload, issuer_id, key
        identity_envelopes_by_match[match_id] = envelope
    if set(identities) != set(match_ids):
        result["reason"] = "identity_binding_or_conflict"
        return result
    result["identity_assertion_authenticated"] = True
    result["identity_issuer_ids"] = [identities[match_id][1] for match_id in match_ids]
    result["canonical_work_ids"] = [
        identities[match_id][0]["canonical_work_id"] for match_id in match_ids
    ]
    result["identity_attestation_sha256"] = [
        _bounded_hash(identity_envelopes_by_match[match_id]) for match_id in match_ids
    ]
    result["identity_metadata_record_sha256"] = [
        identities[match_id][0]["metadata_record_sha256"] for match_id in match_ids
    ]
    result["used_enrolled_key_fingerprints"] = [
        {
            **_key_fingerprint(
                "identity",
                identities[match_id][1],
                identity_envelopes_by_match[match_id]["key_id"],
                identities[match_id][2],
            ),
            "match_id": match_id,
        }
        for match_id in match_ids
    ]

    verified_lineage = _verify_envelope(
        lineage_attestations[0], "lineage", LINEAGE_VERSION, lineage_trust_roots
    )
    if verified_lineage is None:
        result["reason"] = "lineage_signature_or_schema_invalid"
        return result
    lineage, lineage_issuer_id, lineage_key = verified_lineage
    if any(
        lineage_issuer_id == identities[match_id][1]
        or lineage_key == identities[match_id][2]
        for match_id in match_ids
    ):
        result["reason"] = "lineage_signer_not_separate"
        return result
    if not _lineage_payload_valid(lineage, bundle_sha256, match_ids):
        result["reason"] = "lineage_binding_or_schema_invalid"
        return result
    works = {work["match_id"]: work for work in lineage["works"]}
    if any(
        works[match_id]["canonical_work_id"]
        != identities[match_id][0]["canonical_work_id"]
        for match_id in match_ids
    ):
        result["reason"] = "lineage_work_identity_conflict"
        return result
    try:
        by_match, paths_complete = _roots_from_graph(lineage)
    except (ValueError, RecursionError, TypeError, KeyError, MemoryError):
        result["reason"] = "lineage_graph_invalid"
        return result
    result["lineage_assertion_authenticated"] = True
    result["lineage_issuer_id"] = lineage_issuer_id
    result["lineage_attestation_sha256"] = _bounded_hash(lineage_attestations[0])
    result["lineage_source_record_sha256"] = lineage["source_record_sha256"]
    result["used_enrolled_key_fingerprints"].append(
        _key_fingerprint(
            "lineage", lineage_issuer_id, lineage_attestations[0]["key_id"], lineage_key
        )
    )
    result["primary_roots_by_match_id"] = {
        match_id: sorted(by_match[match_id]) for match_id in match_ids
    }
    complete = (
        lineage["alias_index_complete"]
        and all(works[match_id]["coverage_complete"] for match_id in match_ids)
        and all(paths_complete[match_id] for match_id in match_ids)
    )
    result["coverage_complete"] = complete
    if by_match[match_ids[0]] & by_match[match_ids[1]]:
        result["status"] = TARGET_FALSE
        result["reason"] = "shared_attested_primary_root"
    elif complete and all(by_match[match_id] for match_id in match_ids):
        if len(set(result["canonical_work_ids"])) == 2:
            result["status"] = TARGET_TRUE
            result["reason"] = "complete_attested_disjoint_primary_roots"
        else:
            result["reason"] = "same_canonical_work_without_shared_root"
    else:
        result["reason"] = "lineage_or_alias_coverage_incomplete"
    return result
