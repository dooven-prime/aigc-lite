"""Canonical certificate bytes bind the conditional report, not authority."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files

import pytest
from aigc_lite_casepacks.mde_v0.certificate_common import (
    CERTIFICATE_CONTRACT_SHA256,
    CERTIFICATE_VERSION,
    encode_certificate,
    finalize_certificate,
    parse_certificate,
    verify_certificate_hash,
)


def _body() -> dict:
    return {
        "certificate_version": CERTIFICATE_VERSION,
        "certificate_kind": "bio.parent-source-refutation.v0",
        "case_id": "synthetic.mde.test",
        "decision": {"target_value": False, "compatible_model_nonempty": True},
        "candidate_only": True,
        "qualification_granted": False,
        "current_use_bound": False,
        "authorization_granted": False,
    }


def test_canonical_certificate_identity_is_stable_and_round_trips() -> None:
    first = finalize_certificate(_body())
    second = finalize_certificate(dict(reversed(list(_body().items()))))
    assert first == second
    assert verify_certificate_hash(first)
    raw = encode_certificate(first)
    assert raw == json.dumps(
        first, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    assert parse_certificate(raw) == first
    assert first["certificate_contract_sha256"] == CERTIFICATE_CONTRACT_SHA256


def test_certificate_contract_bytes_are_packaged_and_match_hash_rule() -> None:
    raw = files("aigc_lite_casepacks.mde_v0").joinpath(
        "certificate_contract_v0.json"
    ).read_bytes()
    contract = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == CERTIFICATE_CONTRACT_SHA256
    assert contract["certificate_version"] == CERTIFICATE_VERSION
    assert contract["hash"]["domain_separator"] == (
        "aigc-lite.mde.disambiguation-certificate.v0\n"
    )
    assert contract["authority_effect"] == "candidate_diagnostic_only"


def test_certificate_identity_rejects_mutation_and_noncanonical_json() -> None:
    certificate = finalize_certificate(_body())
    changed = {**certificate, "qualification_granted": True}
    assert not verify_certificate_hash(changed)
    with pytest.raises(ValueError, match="identity"):
        encode_certificate(changed)
    with pytest.raises(ValueError, match="noncanonical"):
        parse_certificate(json.dumps(certificate, indent=2).encode("utf-8"))
    with pytest.raises(ValueError, match="Duplicate"):
        parse_certificate(encode_certificate(certificate).replace(
            b'"case_id":', b'"case_id":"duplicate","case_id":', 1
        ))
    with pytest.raises(ValueError, match="candidate-only"):
        finalize_certificate({**_body(), "qualification_granted": True})


def test_certificate_identity_rejects_wrong_version_non_json_and_resource_pressure() -> None:
    with pytest.raises(ValueError, match="version"):
        finalize_certificate({**_body(), "certificate_version": "future"})
    with pytest.raises(ValueError, match="canonical JSON"):
        finalize_certificate({**_body(), "not_json": object()})
    with pytest.raises(ValueError, match="candidate-only"):
        finalize_certificate({**_body(), "certificate_kind": []})
    assert not verify_certificate_hash({
        **finalize_certificate(_body()), "certificate_kind": []
    })
    with pytest.raises(ValueError, match="too large"):
        parse_certificate(b"{" + b" " * 1_048_576 + b"}")
