"""Fail-closed checks for the standalone, candidate-only Lean delivery."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.verify_fibre_criterion_case import (
    BOUND_FILES,
    CASE_ID,
    DECLARATIONS,
    CaseReplayError,
    check_package,
    parse_axiom_audit,
)


def _candidate(tmp_path: Path) -> tuple[Path, str]:
    contents = {
        ".gitattributes": b"* text eol=lf\n",
        "lean-toolchain": b"leanprover/lean4:v4.33.0\n",
        "FibreCriterion.lean": b"import Init\n",
        "AxiomAudit.lean": b"import FibreCriterion\n",
    }
    for name, data in contents.items():
        (tmp_path / name).write_bytes(data)
    lock = {
        "schema": "rime.formal-case-lock.v1",
        "case_id": CASE_ID,
        "status": "CANDIDATE_NOT_RELEASED",
        "toolchain": {
            "lean_version": "4.33.0",
            "lean_commit": "d8b18978322de05a8f3dba51ef03cf5461676c17",
            "imports": ["Init"],
        },
        "bound_files": [
            {"path": name, "size": len(contents[name]), "sha256": hashlib.sha256(contents[name]).hexdigest()}
            for name in sorted(BOUND_FILES)
        ],
        "allowed_standard_axioms": ["Classical.choice", "Quot.sound", "propext"],
        "expected_theorem_axioms": [
            {"name": f"AgainstSilentPromotion.{name}", "axioms": []}
            for name in DECLARATIONS
        ],
    }
    lock_bytes = json.dumps(lock).encode()
    (tmp_path / "case-lock.json").write_bytes(lock_bytes)
    return tmp_path, hashlib.sha256(lock_bytes).hexdigest()


def test_package_requires_out_of_band_lock_digest(tmp_path: Path) -> None:
    case, digest = _candidate(tmp_path)
    lock, files = check_package(case, digest)
    assert lock["case_id"] == CASE_ID
    assert files["FibreCriterion.lean"] == b"import Init\n"
    with pytest.raises(CaseReplayError, match="out-of-band digest"):
        check_package(case, "0" * 64)


def test_package_rejects_changed_source_and_extra_lean_module(tmp_path: Path) -> None:
    case, digest = _candidate(tmp_path)
    (case / "FibreCriterion.lean").write_bytes(b"import Init\n-- changed\n")
    with pytest.raises(CaseReplayError, match="bound file changed"):
        check_package(case, digest)
    (case / "FibreCriterion.lean").write_bytes(b"import Init\n")
    (case / "Unexpected.lean").write_bytes(b"import Init\n")
    with pytest.raises(CaseReplayError, match="unexpected Lean source"):
        check_package(case, digest)


def test_package_rejects_symlinked_bound_input(tmp_path: Path) -> None:
    case, digest = _candidate(tmp_path)
    source = case / "AxiomAudit.lean"
    target = case / "audit-copy.txt"
    source.rename(target)
    try:
        source.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(CaseReplayError, match="linked"):
        check_package(case, digest)


def test_axiom_audit_requires_all_exact_sets() -> None:
    expected = [
        {"name": f"AgainstSilentPromotion.{name}", "axioms": []}
        for name in DECLARATIONS
    ]
    lines = [
        f"'{item['name']}' does not depend on any axioms" for item in expected
    ]
    assert len(parse_axiom_audit("\n".join(lines), expected)) == 7
    with pytest.raises(CaseReplayError, match="every frozen declaration"):
        parse_axiom_audit("\n".join(lines[:-1]), expected)
    with pytest.raises(CaseReplayError, match="axiom set differs"):
        parse_axiom_audit(
            "\n".join(lines[:-1] + [
                f"'{expected[-1]['name']}' depends on axioms: [sorryAx]"
            ]),
            expected,
        )
    with pytest.raises(CaseReplayError, match="duplicate axiom report"):
        parse_axiom_audit("\n".join(lines + [lines[0]]), expected)
