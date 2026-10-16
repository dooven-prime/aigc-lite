"""Development-only, synthetic tests for the source-bound local-order detector."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aigc_lite_casepacks.external_math_alignment_v0.__main__ import main  # noqa: E402
from aigc_lite_casepacks.external_math_alignment_v0.audit import (  # noqa: E402
    InputIdentityError,
    _lean_offsets,
    _paper_offset,
    audit_sources,
)

PDF_TEXT = (
    "Lemma 8.6.Let N=v t ·∂ y on T2, with vt as in (6.2), and let F be a "
    "smooth function of zero Haar mean. Then Nφ=F has a unique smooth "
    "zero-mean solution φ=N −1F. This solution is given by the following "
    "Fourier series and satisfies the displayed norm bound for every "
    "integer m≥0:\n"
    "N−1F= ∑\nk∈Z2\\{0}\n"
    "∥N −1F∥Cmy ≤C m∥F∥ Cm+4y\n. (8.19)"
)
LEAN_TEXT = """\
theorem norm_derivativeWord_inverse_le (w : List Bool) {C : ℝ}
    (hfirst : ∀ x, ‖SmoothFourierData.xJet (w.length + 5) f x‖ ≤ C)
    (hsecond : ∀ x,
      ‖SmoothFourierData.xJet (w.length + 5) (swap f) x‖ ≤ C) :
    ‖derivativeWord w f‖ ≤ C := by
  trivial
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inputs(tmp_path: Path, *, lean: str = LEAN_TEXT) -> tuple[Path, Path, bytes, str]:
    pdf_bytes = b"synthetic PDF bytes used with a patched text extractor"
    lean_bytes = lean.encode("utf-8")
    pdf_path = tmp_path / "manuscript.pdf"
    lean_path = tmp_path / "proof.lean"
    pdf_path.write_bytes(pdf_bytes)
    lean_path.write_bytes(lean_bytes)
    lock = {
        "contract": "external.math.alignment.source-lock.v0",
        "case_id": "external-math-alignment-pilot-001-lemma-8-6",
        "development_only": True,
        "sources": {
            "manuscript_pdf": {
                "sha256": _sha(pdf_bytes), "bytes": len(pdf_bytes),
                "pdf_page_index": 94, "label": "(8.19)",
            },
            "formal_source": {
                "sha256": _sha(lean_bytes), "bytes": len(lean_bytes),
                "git_commit": "f9e8bc5b38b6e212696e8a30e3e91517af887bbd",
                "path": "NavierStokes/SmoothFamilyTorusInverse.lean",
                "declaration": "norm_derivativeWord_inverse_le",
            },
            "external_review": {"content_claim": "KNOWN ANSWER MUST NOT BE READ"},
        },
    }
    lock_bytes = json.dumps(lock, sort_keys=True).encode("utf-8")
    return pdf_path, lean_path, lock_bytes, _sha(lock_bytes)


def test_local_gap_is_candidate_only_and_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aigc_lite_casepacks.external_math_alignment_v0.audit._extract_pdf_page",
        lambda _data, _index: PDF_TEXT,
    )
    pdf, lean, lock, expected = _inputs(tmp_path)
    first = audit_sources(pdf, lean, expected_lock_sha256=expected, lock_bytes=lock)
    second = audit_sources(pdf, lean, expected_lock_sha256=expected, lock_bytes=lock)
    assert first == second
    assert first["artifact_kind"] == "CANDIDATE_DIAGNOSIS"
    assert first["diagnosis"]["local_requirement_alignment"] == "ALIGNMENT_GAP"
    assert first["diagnosis"]["full_statement_alignment"] == "UNRESOLVED"
    assert first["diagnosis"]["kernel_verification"] == "NOT_RUN"
    assert first["diagnosis"]["qualification"] == "NOT_REQUESTED"
    assert first["diagnosis"]["current_use"] == "NOT_REQUESTED"
    assert first["authority_effect"] == "NONE"
    assert first["observations"]["manuscript"]["input_derivative_offset"] == 4
    formal = first["observations"]["formal"]
    assert formal["hfirst_input_derivative_offset"] == 5
    assert formal["hsecond_input_derivative_offset"] == 5
    assert first["input_identity"]["operator_expected_source_lock_sha256"] == expected
    package_root = Path(__file__).resolve().parents[1] / "src/aigc_lite_casepacks/external_math_alignment_v0"
    assert first["input_identity"]["checker_source_sha256"] == _sha(
        (package_root / "audit.py").read_bytes()
    )
    assert first["input_identity"]["packaged_contract_sha256"] == _sha(
        (package_root / "contract-v0.json").read_bytes()
    )
    assert "KNOWN ANSWER MUST NOT BE READ" not in json.dumps(first)


@pytest.mark.parametrize(
    "paper",
    [
        PDF_TEXT.replace("(8.19)", "(8.20)"),
        PDF_TEXT + "\n" + PDF_TEXT,
        PDF_TEXT.replace("Lemma 8.6.", "Lemma 8.7."),
        PDF_TEXT.replace("Cm+4y", "Cm+?y"),
    ],
)
def test_absent_or_ambiguous_pdf_anchor_is_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, paper: str,
) -> None:
    monkeypatch.setattr(
        "aigc_lite_casepacks.external_math_alignment_v0.audit._extract_pdf_page",
        lambda _data, _index: paper,
    )
    pdf, lean, lock, expected = _inputs(tmp_path)
    result = audit_sources(pdf, lean, expected_lock_sha256=expected, lock_bytes=lock)
    assert result["diagnosis"]["local_requirement_alignment"] == "UNRESOLVED"
    assert "paper_equation_anchor_absent_or_ambiguous" in result["diagnosis"]["reasons"]


def test_formal_hypotheses_must_both_be_uniquely_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aigc_lite_casepacks.external_math_alignment_v0.audit._extract_pdf_page",
        lambda _data, _index: PDF_TEXT,
    )
    lean_text = LEAN_TEXT.replace(
        "(hsecond : ∀ x,\n      ‖SmoothFourierData.xJet (w.length + 5)",
        "(hsecond : ∀ x,\n      ‖SmoothFourierData.xJet (w.length + 4)",
    )
    pdf, lean, lock, expected = _inputs(tmp_path, lean=lean_text)
    result = audit_sources(pdf, lean, expected_lock_sha256=expected, lock_bytes=lock)
    assert result["diagnosis"]["local_requirement_alignment"] == "UNRESOLVED"
    assert "formal_hypothesis_offsets_disagree" in result["diagnosis"]["reasons"]
    assert _lean_offsets(LEAN_TEXT + "\n" + LEAN_TEXT) is None


def test_equal_offsets_do_not_become_full_statement_alignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aigc_lite_casepacks.external_math_alignment_v0.audit._extract_pdf_page",
        lambda _data, _index: PDF_TEXT,
    )
    pdf, lean, lock, expected = _inputs(
        tmp_path, lean=LEAN_TEXT.replace("w.length + 5", "w.length + 4"),
    )
    result = audit_sources(pdf, lean, expected_lock_sha256=expected, lock_bytes=lock)
    assert result["diagnosis"]["local_requirement_alignment"] == "NO_LOCAL_OFFSET_GAP_DETECTED"
    assert result["diagnosis"]["full_statement_alignment"] == "UNRESOLVED"
    assert result["authority_effect"] == "NONE"


def test_source_lock_and_source_bytes_must_match_external_expected_hash(tmp_path: Path) -> None:
    pdf, lean, lock, expected = _inputs(tmp_path)
    with pytest.raises(InputIdentityError, match="operator-supplied"):
        audit_sources(pdf, lean, expected_lock_sha256="0" * 64, lock_bytes=lock)
    with pytest.raises(InputIdentityError, match="required"):
        audit_sources(pdf, lean, expected_lock_sha256="", lock_bytes=lock)
    pdf.write_bytes(pdf.read_bytes() + b"x")
    with pytest.raises(InputIdentityError, match="Manuscript PDF"):
        audit_sources(pdf, lean, expected_lock_sha256=expected, lock_bytes=lock)
    pdf.write_bytes(b"synthetic PDF bytes used with a patched text extractor")
    lean.write_bytes(lean.read_bytes() + b"x")
    with pytest.raises(InputIdentityError, match="Formal source"):
        audit_sources(pdf, lean, expected_lock_sha256=expected, lock_bytes=lock)


def test_runtime_drift_and_malformed_lock_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf, lean, lock, expected = _inputs(tmp_path)
    monkeypatch.setattr("pypdf.__version__", "6.20.0")
    with pytest.raises(InputIdentityError, match="pypdf runtime version"):
        audit_sources(pdf, lean, expected_lock_sha256=expected, lock_bytes=lock)
    monkeypatch.setattr("pypdf.__version__", "6.7.0")
    duplicate = b'{"case_id":"a","case_id":"b"}'
    with pytest.raises(InputIdentityError, match="Malformed source lock"):
        audit_sources(pdf, lean, expected_lock_sha256=_sha(duplicate), lock_bytes=duplicate)


def test_parser_only_accepts_the_frozen_local_shape() -> None:
    assert _paper_offset(PDF_TEXT) is not None
    assert _paper_offset(PDF_TEXT.replace("∥N −1F∥", "∥G∥")) is None
    assert _lean_offsets(LEAN_TEXT) is not None
    assert _lean_offsets(LEAN_TEXT.replace("(hfirst :", "(hother :")) is None


def test_cli_report_is_ascii_safe_on_gbk_console(monkeypatch: pytest.MonkeyPatch) -> None:
    output = io.BytesIO()
    console = io.TextIOWrapper(output, encoding="gbk", write_through=True)
    monkeypatch.setattr("sys.stdout", console)
    monkeypatch.setattr(
        "aigc_lite_casepacks.external_math_alignment_v0.__main__.audit_sources",
        lambda *_args, **_kwargs: {"quote": "∥N −1F∥"},
    )
    result = main([
        "--manuscript-pdf", "unused.pdf",
        "--formal-source", "unused.lean",
        "--expected-lock-sha256", "0" * 64,
    ])
    assert result == 0
    assert json.loads(output.getvalue().decode("ascii")) == {"quote": "∥N −1F∥"}
