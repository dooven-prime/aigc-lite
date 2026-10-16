"""Structure checks for candidate-only, per-record source inventories.

These tests validate declared identities and limitations, not upstream truth.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

MANIFEST_DIR = Path(__file__).resolve().parents[1] / "records" / "manifests"
EXPECTED = {
    "lemma-8-6-v1.json": "../LEMMA_8_6.md",
    "pressure-flux-10-19-v1.json": "../PRESSURE_FLUX_10_19.md",
    "trace-square-control-v1.json": "../POSITIVE_CONTROL_TRACE_SQUARE.md",
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@pytest.mark.parametrize(("name", "record_path"), EXPECTED.items())
def test_per_record_manifest_has_scoped_sources(
    name: str, record_path: str,
) -> None:
    manifest = json.loads((MANIFEST_DIR / name).read_text(encoding="utf-8"))
    assert manifest["contract"] == "external.math.alignment.record-source-manifest.v1"
    assert manifest["record_path"] == record_path
    assert (MANIFEST_DIR / record_path).resolve().is_file()
    assert manifest["authority_effect"] == "none"
    assert manifest["not_closed"]

    seen: set[str] = set()
    for source in manifest["sources"]:
        assert source["id"] not in seen
        seen.add(source["id"])
        digest = source["sha256"]
        size = source["bytes"]
        if digest is None:
            assert size is None
            assert source["materialization"] != "retrieved_and_hashed_at_audit_not_bundled"
        else:
            assert _SHA256.fullmatch(digest)
            assert type(size) is int and size > 0
            assert source["materialization"] == "retrieved_and_hashed_at_audit_not_bundled"


def test_manifests_do_not_share_a_false_complete_source_closure() -> None:
    lemma = json.loads((MANIFEST_DIR / "lemma-8-6-v1.json").read_text(encoding="utf-8"))
    pressure = json.loads(
        (MANIFEST_DIR / "pressure-flux-10-19-v1.json").read_text(encoding="utf-8")
    )
    control = json.loads(
        (MANIFEST_DIR / "trace-square-control-v1.json").read_text(encoding="utf-8")
    )
    assert lemma["audit_mode"] == "bounded_source_detector_plus_human_dependency_trace"
    assert any(source["sha256"] is None for source in lemma["sources"])
    assert pressure["audit_mode"] == "human_primary_source_comparison"
    assert len([source for source in pressure["sources"] if source["sha256"]]) >= 6
    assert control["audit_mode"] == "human_curated_statement_fields_plus_pure_comparator_regression"
    assert any(
        source["id"] == "independent_lean_source_file" and source["sha256"] is None
        for source in control["sources"]
    )
