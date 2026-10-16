"""Synthetic counterfactuals for the bounded Lemma 8.6 detector.

These fixtures are not OpenAI manuscript or Lean source bytes.  Each has a
distinct, explicitly synthetic source-lock identity and a matching external
hash; none is admissible as a real-paper blind evaluation result.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aigc_lite_casepacks.external_math_alignment_v0 import audit  # noqa: E402

PAPER = (
    "SYNTHETIC FIXTURE. Lemma 8.6. For every integer m≥0, the bound is "
    "∥N −1F∥Cmy ≤C m∥F∥ Cm+4y\n. (8.19)"
)
LEAN = """\
-- SYNTHETIC FIXTURE; this file is not a kernel-checked proof.
theorem norm_derivativeWord_inverse_le (w : List Bool) {C : ℝ}
    (hfirst : ∀ x, ‖SmoothFourierData.xJet (w.length + 5) f x‖ ≤ C)
    (hsecond : ∀ x,
      ‖SmoothFourierData.xJet (w.length + 5) (swap f) x‖ ≤ C) :
    ‖derivativeWord w f‖ ≤ C := by
  trivial
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _audit_synthetic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fixture_id: str,
    paper: str = PAPER,
    lean: str = LEAN,
) -> dict:
    """Exercise production comparison with a test-only, non-Git identity."""

    case_id = f"synthetic:alignment-mutation:{fixture_id}"
    formal_origin = f"synthetic-not-a-git-commit:{fixture_id}"
    formal_path = f"synthetic-fixtures/{fixture_id}.lean"
    monkeypatch.setattr(audit, "CASE_ID", case_id)
    monkeypatch.setattr(audit, "FORMAL_COMMIT", formal_origin)
    monkeypatch.setattr(audit, "FORMAL_PATH", formal_path)
    monkeypatch.setattr(audit, "PDF_PAGE_INDEX", 0)
    monkeypatch.setattr(audit, "_extract_pdf_page", lambda _bytes, _index: paper)

    # This is a byte-identity fixture, not a PDF rendering or Lean compilation.
    pdf_bytes = f"synthetic-pdf:{fixture_id}".encode("ascii")
    lean_bytes = lean.encode("utf-8")
    pdf_path = tmp_path / "synthetic.pdf"
    lean_path = tmp_path / "synthetic.lean"
    pdf_path.write_bytes(pdf_bytes)
    lean_path.write_bytes(lean_bytes)
    lock = {
        "contract": audit.SOURCE_LOCK_CONTRACT,
        "case_id": case_id,
        "development_only": True,
        "sources": {
            "manuscript_pdf": {
                "sha256": _sha(pdf_bytes),
                "bytes": len(pdf_bytes),
                "pdf_page_index": 0,
                "label": "(8.19)",
                "origin": f"synthetic-not-a-paper:{fixture_id}",
            },
            "formal_source": {
                "sha256": _sha(lean_bytes),
                "bytes": len(lean_bytes),
                "git_commit": formal_origin,
                "path": formal_path,
                "declaration": "norm_derivativeWord_inverse_le",
            },
        },
    }
    lock_bytes = json.dumps(lock, sort_keys=True).encode("utf-8")
    result = audit.audit_sources(
        pdf_path,
        lean_path,
        expected_lock_sha256=_sha(lock_bytes),
        lock_bytes=lock_bytes,
    )
    assert result["case_id"] == case_id
    assert result["input_identity"]["formal_git_commit_claim"] == formal_origin
    assert result["input_identity"]["formal_path_claim"] == formal_path
    assert result["input_identity"]["manuscript_pdf_sha256"] == _sha(pdf_bytes)
    assert result["input_identity"]["formal_source_sha256"] == _sha(lean_bytes)
    assert result["diagnosis"]["full_statement_alignment"] == "UNRESOLVED"
    assert result["diagnosis"]["kernel_verification"] == "NOT_RUN"
    assert result["diagnosis"]["qualification"] == "NOT_REQUESTED"
    assert result["diagnosis"]["current_use"] == "NOT_REQUESTED"
    assert result["authority_effect"] == "NONE"
    return result


def test_both_hypotheses_reduced_to_paper_offset_do_not_prove_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _audit_synthetic(
        tmp_path,
        monkeypatch,
        fixture_id="both-offsets-reduced",
        lean=LEAN.replace("w.length + 5", "w.length + 4"),
    )
    assert result["diagnosis"]["local_requirement_alignment"] == (
        "NO_LOCAL_OFFSET_GAP_DETECTED"
    )
    assert "offset_comparison_does_not_establish_statement_identity" in (
        result["diagnosis"]["reasons"]
    )


@pytest.mark.parametrize("hypothesis", ["hfirst", "hsecond"])
def test_changing_only_one_hypothesis_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hypothesis: str,
) -> None:
    original = (
        "(hfirst : ∀ x, ‖SmoothFourierData.xJet (w.length + 5)"
        if hypothesis == "hfirst"
        else "(hsecond : ∀ x,\n      ‖SmoothFourierData.xJet (w.length + 5)"
    )
    assert original in LEAN
    changed = LEAN.replace(original, original.replace("+ 5", "+ 4"))
    result = _audit_synthetic(
        tmp_path,
        monkeypatch,
        fixture_id=f"one-offset-reduced-{hypothesis}",
        lean=changed,
    )
    assert result["diagnosis"]["local_requirement_alignment"] == "UNRESOLVED"
    assert "formal_hypothesis_offsets_disagree" in result["diagnosis"]["reasons"]


def test_removed_declaration_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _audit_synthetic(
        tmp_path,
        monkeypatch,
        fixture_id="declaration-removed",
        lean=LEAN.replace(
            "theorem norm_derivativeWord_inverse_le",
            "theorem unrelated_synthetic_claim",
        ),
    )
    assert result["diagnosis"]["local_requirement_alignment"] == "UNRESOLVED"
    assert "formal_declaration_anchor_absent_or_ambiguous" in (
        result["diagnosis"]["reasons"]
    )


@pytest.mark.parametrize(
    ("fixture_id", "paper", "lean"),
    [
        (
            "same-offset-different-object",
            PAPER,
            LEAN.replace("‖derivativeWord w f‖ ≤ C", "‖derivativeWord w unrelated‖ ≤ C")
            .replace("w.length + 5", "w.length + 4"),
        ),
        (
            "same-offset-different-quantifier",
            PAPER.replace("For every integer m≥0", "For one chosen integer m≥0"),
            LEAN.replace("w.length + 5", "w.length + 4"),
        ),
    ],
)
def test_same_offset_cannot_promote_changed_object_or_quantifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fixture_id: str,
    paper: str,
    lean: str,
) -> None:
    result = _audit_synthetic(
        tmp_path, monkeypatch, fixture_id=fixture_id, paper=paper, lean=lean,
    )
    assert result["diagnosis"]["local_requirement_alignment"] == (
        "NO_LOCAL_OFFSET_GAP_DETECTED"
    )
    assert result["diagnosis"]["full_statement_alignment"] == "UNRESOLVED"
