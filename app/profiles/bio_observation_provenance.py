"""Narrow authentication of *records*, not of experimental reality or quality.

Trust roots are supplied by the host, never by an evidence envelope. A valid
signature establishes who signed these frozen bytes; it does not establish
sample identity, complete audit trails, biological independence, or QC.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..core.errors import InvalidEvidenceError
from ..core.qualification import (
    CriterionReceipt,
    EvidenceAxisState,
    EvidenceClosure,
    QualificationCriterion,
    QualificationDecision,
    QualificationProfile,
    QualificationVerdict,
    ValidationModality,
    canonical_hash,
)
from .bio_in_vitro import validate_witness

PROFILE_ID = "bio.observation.provenance.v0"
CLAIM_KIND = "in_vitro_record_provenance"
VERIFIER_PRINCIPAL = "system:verifier:bio-record-provenance"
SCOPE = "Enrolled-key authentication and exact cross-record binding only."
BOUNDARIES = [
    "Signatures authenticate record issuers and bytes, not whether the experiment occurred.",
    "Instrument enrollment, sample identity, full audit trails, QC, and biological independence are not checked.",
    "No causal, clinical, safety, efficacy, or regulatory conclusion follows.",
]
CHECKER_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
PROFILE = QualificationProfile(
    profile_id=PROFILE_ID,
    version=1,
    claim_kind=CLAIM_KIND,
    policy_version="bio.observation.provenance.policy.v0",
    description=(
        "A frozen in-vitro dataset is exactly bound to an instrument export and a "
        "sample-mapping record signed by two host-enrolled roles. This authenticates "
        "record origin and binding, not experimental truth or validity. "
        f"Checker source sha256:{CHECKER_SOURCE_SHA256}."
    ),
    criteria=(
        QualificationCriterion(
            "statement_identity", "The narrow record-binding statement is frozen."
        ),
        QualificationCriterion("source_integrity", "All source bytes and references are intact."),
        QualificationCriterion(
            "instrument_record", "An enrolled instrument issuer signed exact measurements."
        ),
        QualificationCriterion(
            "sample_chain", "A separate enrolled custodian signed the sample mapping."
        ),
        QualificationCriterion(
            "server_replay", "A server-owned attempt binds the checked records."
        ),
    ),
    accepted_modalities=(ValidationModality.EXACT_REPLAY,),
)


@dataclass(frozen=True, slots=True)
class TrustedRecordKey:
    role: str
    issuer_id: str
    key_id: str
    public_key: bytes

    def __post_init__(self) -> None:
        if self.role not in {"instrument", "custodian"} or len(self.public_key) != 32:
            raise ValueError("Invalid host-owned provenance key")
        if not self.issuer_id or not self.key_id:
            raise ValueError("Provenance issuer and key ID are required")


def statement(witness_hash: str) -> str:
    return (
        "The measurement pairs and study metadata in submitted in-vitro witness "
        f"sha256:{witness_hash} match an enrolled instrument export and a "
        "separately signed custodian sample mapping."
    )


def signed_bytes(envelope: dict) -> bytes:
    """Domain-separated bytes shared by external signers and this verifier."""
    return b"aigc-lite.bio-observation-provenance.v0\n" + json.dumps(
        {key: envelope[key] for key in ("contract_version", "issuer_id", "key_id", "payload")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _intact(node: dict) -> bool:
    content = node["payload"].get("content_text")
    return bool(
        isinstance(content, str)
        and hashlib.sha256(content.encode("utf-8")).hexdigest() == node["content_hash"]
        and node["payload"].get("content_hash") == node["content_hash"]
    )


def _verified(envelope: dict, role: str, keys: dict[tuple[str, str, str], bytes]) -> bool:
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"contract_version", "issuer_id", "key_id", "payload", "signature"}
        or envelope.get("contract_version") != f"bio.{role}-record.v0"
    ):
        return False
    if not isinstance(envelope.get("issuer_id"), str) or not isinstance(
        envelope.get("key_id"), str
    ):
        return False
    key = keys.get((role, envelope["issuer_id"], envelope["key_id"]))
    if key is None or not isinstance(envelope.get("payload"), dict):
        return False
    try:
        signature_text = envelope["signature"]
        if not isinstance(signature_text, str) or len(signature_text) > 100:
            return False
        signature = base64.b64decode(
            signature_text.encode("ascii") + b"=" * (-len(signature_text) % 4),
            altchars=b"-_",
            validate=True,
        )
        if len(signature) != 64:
            return False
        Ed25519PublicKey.from_public_bytes(key).verify(signature, signed_bytes(envelope))
    except (InvalidSignature, ValueError, KeyError, TypeError, UnicodeEncodeError, binascii.Error):
        return False
    return True


class BioObservationProvenanceVerifier:
    def __init__(self, trusted_keys: tuple[TrustedRecordKey, ...] = ()) -> None:
        self._keys: dict[tuple[str, str, str], bytes] = {}
        for item in trusted_keys:
            identity = (item.role, item.issuer_id, item.key_id)
            if identity in self._keys:
                raise ValueError("Duplicate host-owned provenance key")
            if item.public_key in self._keys.values():
                raise ValueError("Instrument and custodian cannot share a signing key")
            self._keys[identity] = item.public_key
        keyring_hash = canonical_hash(
            [
                {
                    "role": role,
                    "issuer_id": issuer,
                    "key_id": key_id,
                    "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
                }
                for (role, issuer, key_id), public_key in sorted(self._keys.items())
            ]
        )
        self.profile = replace(
            PROFILE, description=f"{PROFILE.description} Host keyring sha256:{keyring_hash}."
        )

    def evaluate(self, closure: EvidenceClosure) -> QualificationDecision:
        claim = next(node for node in closure.nodes if node["node_type"] == "claim_revision")
        payload = claim["payload"]
        if payload.get("claim_type") != CLAIM_KIND:
            return QualificationDecision(
                QualificationVerdict.NOT_APPLICABLE, (), ("claim_kind_mismatch",)
            )
        artifacts = {
            node["node_id"]: node for node in closure.nodes if node["node_type"] == "artifact"
        }
        sources = [node for node in closure.nodes if node["node_type"] == "source"]
        by_role: dict[str, list[dict]] = {key: [] for key in ("witness", "instrument", "custodian")}
        for node in artifacts.values():
            role = (node["payload"].get("metadata") or {}).get("role")
            if role == "bio_in_vitro_witness":
                by_role["witness"].append(node)
            elif role == "bio_provenance_instrument":
                by_role["instrument"].append(node)
            elif role == "bio_provenance_custodian":
                by_role["custodian"].append(node)
        selected = {
            role: values[0] if len(values) == 1 and _intact(values[0]) else None
            for role, values in by_role.items()
        }
        witness = instrument = custodian = None
        try:
            if selected["witness"]:
                witness = validate_witness(
                    json.loads(selected["witness"]["payload"]["content_text"])
                )
            if selected["instrument"]:
                instrument = json.loads(selected["instrument"]["payload"]["content_text"])
            if selected["custodian"]:
                custodian = json.loads(selected["custodian"]["payload"]["content_text"])
        except (InvalidEvidenceError, ValueError, TypeError, KeyError):
            pass
        source_ok = bool(
            witness is not None
            and all(node is not None for node in selected.values())
            and len(sources) == 3
            and all(
                sum(
                    source["payload"].get("source_key") == role
                    and source["payload"].get("locator") == f"artifact:{node['node_id']}"
                    and source["payload"].get("status") == "frozen"
                    and source["content_hash"] == node["content_hash"]
                    for source in sources
                )
                == 1
                for role, node in selected.items()
                if node is not None
            )
        )
        witness_hash = selected["witness"]["content_hash"] if selected["witness"] else None
        identity_ok = bool(
            source_ok
            and payload.get("semantic_hash") == closure.claim_semantic_hash
            and "statement_drift" not in closure.limitations
            and payload.get("statement") == statement(witness_hash)
            and payload.get("scope") == SCOPE
            and payload.get("negative_boundaries") == BOUNDARIES
            and not payload.get("dependency_claim_ids")
        )
        instrument_ok = bool(
            identity_ok
            and _verified(instrument, "instrument", self._keys)
            and instrument["payload"].get("study") == witness["study"]
            and instrument["payload"].get("pairs") == witness["pairs"]
            and set(instrument["payload"]) == {"study", "pairs"}
        )
        samples = (
            custodian.get("payload", {}).get("samples") if isinstance(custodian, dict) else None
        )
        custodian_ok = bool(
            instrument_ok
            and _verified(custodian, "custodian", self._keys)
            and custodian["issuer_id"] != instrument["issuer_id"]
            and custodian["payload"].get("study_id") == witness["study"]["study_id"]
            and custodian["payload"].get("instrument_sha256")
            == selected["instrument"]["content_hash"]
            and set(custodian["payload"]) == {"study_id", "instrument_sha256", "samples"}
            and isinstance(samples, list)
            and len(samples) == len(witness["pairs"])
            and all(
                isinstance(item, dict)
                and set(item) == {"pair_id", "sample_id", "collected_at", "assayed_at"}
                and isinstance(item["sample_id"], str)
                and bool(item["sample_id"].strip())
                and isinstance(item["pair_id"], str)
                and isinstance(item["collected_at"], str)
                and bool(item["collected_at"])
                and isinstance(item["assayed_at"], str)
                and bool(item["assayed_at"])
                for item in samples
            )
            and sorted(item["pair_id"] for item in samples)
            == sorted(item["pair_id"] for item in witness["pairs"])
        )
        attempt_ok = False
        if custodian_ok:
            artifact_ids = {item["node_id"] for item in selected.values()}
            expected_digest = canonical_hash(
                {
                    "claim_semantic_hash": closure.claim_semantic_hash,
                    "sources": {role: item["content_hash"] for role, item in selected.items()},
                }
            )
            attempt_ok = any(
                item["payload"].get("validation_modality") == ValidationModality.EXACT_REPLAY.value
                and item["payload"].get("outcome") == "passed"
                and not item["payload"].get("invalidated_by")
                and item["payload"].get("input_digest") == expected_digest
                and item["payload"].get("output_digest")
                == canonical_hash({"record_binding": "passed"})
                and set(item["payload"].get("artifact_ids") or []) == artifact_ids
                and len(item["payload"].get("artifact_ids") or []) == 3
                and (item["payload"].get("verifier_lineage") or {}).get("runtime_derived") is True
                and (item["payload"].get("verifier_lineage") or {}).get("principal_id")
                == VERIFIER_PRINCIPAL
                and not (item["payload"].get("verifier_lineage") or {}).get("model_route")
                for item in closure.nodes
                if item["node_type"] == "verification_attempt"
            )
        values = {
            "statement_identity": identity_ok,
            "source_integrity": source_ok,
            "instrument_record": instrument_ok,
            "sample_chain": custodian_ok,
            "server_replay": attempt_ok,
        }
        criteria = tuple(
            CriterionReceipt(
                code,
                EvidenceAxisState.SATISFIED if ok else EvidenceAxisState.UNDETERMINED,
                PROFILE.criteria[index].description,
            )
            for index, (code, ok) in enumerate(values.items())
        )
        admitted = all(values.values())
        return QualificationDecision(
            QualificationVerdict.ADMITTED if admitted else QualificationVerdict.UNRESOLVED,
            criteria,
            () if admitted else tuple(code for code, ok in values.items() if not ok),
            {
                "statement_identity": EvidenceAxisState.SATISFIED
                if identity_ok
                else EvidenceAxisState.UNDETERMINED,
                "artifact_integrity": EvidenceAxisState.SATISFIED
                if source_ok
                else EvidenceAxisState.UNDETERMINED,
                "local_correctness": EvidenceAxisState.SATISFIED
                if attempt_ok
                else EvidenceAxisState.UNDETERMINED,
                "external_reality": EvidenceAxisState.UNDETERMINED,
                "independent_validation": EvidenceAxisState.UNDETERMINED,
            },
        )
