"""Offline, bounded local-order comparison for the development-only pilot.

Only the pinned PDF's equation (8.19) and one pinned Lean declaration are read.
No external review text is supplied to the detector.  Even an observed offset
gap does not establish full statement identity, proof validity, or Clay scope.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

import pypdf

CONTRACT = "external.math.alignment.pilot-001.audit.v0"
SOURCE_LOCK_CONTRACT = "external.math.alignment.source-lock.v0"
CASE_ID = "external-math-alignment-pilot-001-lemma-8-6"
LOCK_FILE = "source-lock-v0.json"
PYPDF_VERSION = "6.7.0"
PDF_PAGE_INDEX = 94
FORMAL_COMMIT = "f9e8bc5b38b6e212696e8a30e3e91517af887bbd"
FORMAL_PATH = "NavierStokes/SmoothFamilyTorusInverse.lean"
MAX_LOCK_BYTES = 64 * 1024
MAX_PACKAGED_CONTRACT_BYTES = 64 * 1024
MAX_CHECKER_SOURCE_BYTES = 512 * 1024
MAX_PDF_BYTES = 16 * 1024 * 1024
MAX_LEAN_BYTES = 2 * 1024 * 1024
MAX_PAGE_TEXT_CHARS = 100_000
MAX_DECLARATION_HEADER_CHARS = 20_000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DECLARATION = "norm_derivativeWord_inverse_le"
_DECLARATION_START = re.compile(
    rf"(?m)^[ \t]*theorem[ \t]+{_DECLARATION}\b"
)
_JET_OFFSET = re.compile(r"\bxJet\s*\(\s*w\.length\s*\+\s*(\d{1,2})\s*\)")
_MANUSCRIPT_EQUATION = re.compile(
    r"∥N\s*−\s*1F∥\s*Cmy\s*≤\s*C\s*m∥F∥\s*Cm\+(\d{1,2})y\s*\.\s*\(8\.19\)"
)


class InputIdentityError(ValueError):
    """The operator's lock or a purported source does not match frozen bytes."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputIdentityError(f"Duplicate source-lock key: {key}")
        result[key] = value
    return result


def _read_bounded(path: Path, maximum: int, label: str) -> bytes:
    try:
        with path.open("rb") as handle:
            data = handle.read(maximum + 1)
    except OSError as exc:
        raise InputIdentityError(f"Cannot read {label}") from exc
    if len(data) > maximum:
        raise InputIdentityError(f"{label} exceeds the input-size ceiling")
    return data


def _packaged_bytes(name: str, maximum: int) -> bytes:
    try:
        data = resources.files(__package__).joinpath(name).read_bytes()
    except OSError as exc:
        raise InputIdentityError(f"Installed package lacks {name}") from exc
    if not data or len(data) > maximum:
        raise InputIdentityError(f"Installed package {name} exceeds the size ceiling")
    return data


def _validate_source_entry(entry: Any, name: str, maximum: int) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise InputIdentityError(f"Missing {name} source-lock entry")
    digest = entry.get("sha256")
    size = entry.get("bytes")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise InputIdentityError(f"Invalid {name} SHA-256 in source lock")
    if type(size) is not int or size < 1 or size > maximum:
        raise InputIdentityError(f"Invalid {name} byte count in source lock")
    return entry


def _load_lock(lock_bytes: bytes, expected_lock_sha256: str) -> dict[str, Any]:
    if not isinstance(expected_lock_sha256, str) or _SHA256.fullmatch(
        expected_lock_sha256
    ) is None:
        raise InputIdentityError("An external SHA-256 for the source lock is required")
    if not lock_bytes or len(lock_bytes) > MAX_LOCK_BYTES:
        raise InputIdentityError("Source lock exceeds the input-size ceiling")
    if _sha256(lock_bytes) != expected_lock_sha256:
        raise InputIdentityError("Source lock does not match the operator-supplied hash")
    try:
        lock = json.loads(lock_bytes, object_pairs_hook=_no_duplicate_keys)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InputIdentityError("Malformed source lock") from exc
    if not isinstance(lock, dict):
        raise InputIdentityError("Source lock must be an object")
    if lock.get("contract") != SOURCE_LOCK_CONTRACT:
        raise InputIdentityError("Unexpected source-lock contract identity")
    if lock.get("case_id") != CASE_ID:
        raise InputIdentityError("Unexpected source-lock case identity")
    if lock.get("development_only") is not True:
        raise InputIdentityError("Pilot source lock must remain development-only")
    sources = lock.get("sources")
    if not isinstance(sources, dict):
        raise InputIdentityError("Missing source-lock sources")
    pdf = _validate_source_entry(sources.get("manuscript_pdf"), "manuscript PDF", MAX_PDF_BYTES)
    formal = _validate_source_entry(sources.get("formal_source"), "formal source", MAX_LEAN_BYTES)
    if pdf.get("pdf_page_index") != PDF_PAGE_INDEX:
        raise InputIdentityError("Unexpected physical PDF page index")
    if pdf.get("label") != "(8.19)":
        raise InputIdentityError("This pilot is restricted to equation (8.19)")
    if formal.get("declaration") != _DECLARATION:
        raise InputIdentityError("Unexpected formal declaration")
    if formal.get("git_commit") != FORMAL_COMMIT or formal.get("path") != FORMAL_PATH:
        raise InputIdentityError("Unexpected formal-source commit or path")
    return lock


def _verify_source(data: bytes, entry: dict[str, Any], label: str) -> None:
    if len(data) != entry["bytes"] or _sha256(data) != entry["sha256"]:
        raise InputIdentityError(f"{label} does not match frozen size and SHA-256")


def _extract_pdf_page(pdf_bytes: bytes, page_index: int) -> str | None:
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes), strict=True)
        if reader.is_encrypted or page_index >= len(reader.pages):
            return None
        text = reader.pages[page_index].extract_text()
    except Exception:  # pypdf may expose format-specific errors.
        return None
    if not isinstance(text, str) or len(text) > MAX_PAGE_TEXT_CHARS:
        return None
    return text


def _paper_offset(page_text: str) -> dict[str, Any] | None:
    # The physical page and exact source bytes are already fixed.  Constrain the
    # match to one equation and its preceding Lemma 8.6, not arbitrary m+N text.
    labels = list(re.finditer(r"\(8\.19\)", page_text))
    lemmas = list(re.finditer(r"Lemma\s+8\.6\.", page_text))
    matches = list(_MANUSCRIPT_EQUATION.finditer(page_text))
    if len(labels) != 1 or len(lemmas) != 1 or len(matches) != 1:
        return None
    match = matches[0]
    if not (lemmas[0].start() < match.start() < labels[0].start() + 1):
        return None
    if match.start() - lemmas[0].end() > 1_500:
        return None
    return {
        "input_derivative_offset": int(match.group(1)),
        "anchor": {"start": match.start(), "end": match.end(), "quote": match.group(0)},
    }


def _lean_offsets(source_text: str) -> dict[str, Any] | None:
    declarations = list(_DECLARATION_START.finditer(source_text))
    if len(declarations) != 1:
        return None
    start = declarations[0].start()
    end = source_text.find(":= by", start, start + MAX_DECLARATION_HEADER_CHARS)
    if end < 0:
        return None
    header = source_text[start:end]
    first = list(re.finditer(r"\(hfirst\s*:", header))
    second = list(re.finditer(r"\(hsecond\s*:", header))
    if len(first) != 1 or len(second) != 1 or first[0].start() >= second[0].start():
        return None
    values = []
    anchors = []
    for part in (header[first[0].start() : second[0].start()], header[second[0].start() :]):
        matches = list(_JET_OFFSET.finditer(part))
        if len(matches) != 1:
            return None
        values.append(int(matches[0].group(1)))
        absolute = start + (first[0].start() if len(values) == 1 else second[0].start()) + matches[0].start()
        anchors.append({
            "line": source_text.count("\n", 0, absolute) + 1,
            "quote": matches[0].group(0),
        })
    return {
        "hfirst_input_derivative_offset": values[0],
        "hsecond_input_derivative_offset": values[1],
        "declaration_start_line": source_text.count("\n", 0, start) + 1,
        "anchors": {"hfirst": anchors[0], "hsecond": anchors[1]},
    }


def audit_sources(
    manuscript_pdf: Path,
    formal_source: Path,
    *,
    expected_lock_sha256: str,
    lock_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Return a deterministic, candidate-only report from two frozen sources.

    ``expected_lock_sha256`` MUST come from outside the package being audited.
    Providing a self-declared hash from the package does not establish source
    identity.  The optional ``lock_bytes`` is for isolated tests; the CLI only
    ever reads the installed, packaged lock.
    """

    if pypdf.__version__ != PYPDF_VERSION:
        raise InputIdentityError("pypdf runtime version does not match the frozen extractor")
    if lock_bytes is None:
        lock_bytes = _packaged_bytes(LOCK_FILE, MAX_LOCK_BYTES)
    checker_bytes = _packaged_bytes("audit.py", MAX_CHECKER_SOURCE_BYTES)
    contract_bytes = _packaged_bytes("contract-v0.json", MAX_PACKAGED_CONTRACT_BYTES)
    lock = _load_lock(lock_bytes, expected_lock_sha256)
    pdf_entry = lock["sources"]["manuscript_pdf"]
    formal_entry = lock["sources"]["formal_source"]
    pdf_bytes = _read_bounded(Path(manuscript_pdf), MAX_PDF_BYTES, "manuscript PDF")
    lean_bytes = _read_bounded(Path(formal_source), MAX_LEAN_BYTES, "formal source")
    _verify_source(pdf_bytes, pdf_entry, "Manuscript PDF")
    _verify_source(lean_bytes, formal_entry, "Formal source")

    page_text = _extract_pdf_page(pdf_bytes, pdf_entry["pdf_page_index"])
    paper = _paper_offset(page_text) if page_text is not None else None
    try:
        lean_text = lean_bytes.decode("utf-8", errors="strict")
    except UnicodeError:
        lean_text = ""
    formal = _lean_offsets(lean_text) if lean_text else None

    local_alignment = "UNRESOLVED"
    reasons: list[str] = []
    if paper is None:
        reasons.append("paper_equation_anchor_absent_or_ambiguous")
    if formal is None:
        reasons.append("formal_declaration_anchor_absent_or_ambiguous")
    if paper is not None and formal is not None:
        formal_offsets = {
            formal["hfirst_input_derivative_offset"],
            formal["hsecond_input_derivative_offset"],
        }
        if len(formal_offsets) != 1:
            reasons.append("formal_hypothesis_offsets_disagree")
        elif next(iter(formal_offsets)) > paper["input_derivative_offset"]:
            local_alignment = "ALIGNMENT_GAP"
            reasons.append("formal_hypotheses_require_higher_input_derivative_order")
        else:
            local_alignment = "NO_LOCAL_OFFSET_GAP_DETECTED"
            reasons.append("offset_comparison_does_not_establish_statement_identity")

    return {
        "contract": CONTRACT,
        "case_id": lock["case_id"],
        "development_only": True,
        "artifact_kind": "CANDIDATE_DIAGNOSIS",
        "input_identity": {
            "operator_expected_source_lock_sha256": expected_lock_sha256,
            "manuscript_pdf_sha256": pdf_entry["sha256"],
            "formal_source_sha256": formal_entry["sha256"],
            "formal_git_commit_claim": formal_entry["git_commit"],
            "formal_path_claim": formal_entry["path"],
            "pypdf_version": pypdf.__version__,
            "checker_source_sha256": _sha256(checker_bytes),
            "packaged_contract_sha256": _sha256(contract_bytes),
        },
        "observations": {
            "manuscript": None if paper is None else {
                **paper,
                "pdf_page_index": pdf_entry["pdf_page_index"],
                "page_text_sha256": _sha256(page_text.encode("utf-8")),
            },
            "formal": formal,
        },
        "diagnosis": {
            "local_requirement_alignment": local_alignment,
            "reasons": reasons,
            "full_statement_alignment": "UNRESOLVED",
            "proof_method_alignment": "NOT_EVALUATED",
            "kernel_verification": "NOT_RUN",
            "clay_target_alignment": "UNRESOLVED",
            "qualification": "NOT_REQUESTED",
            "current_use": "NOT_REQUESTED",
        },
        "limitations": [
            "Lean declaration gives pointwise derivativeWord bounds under hfirst/hsecond xJet hypotheses; it is not literally a C_y^m norm estimate.",
            "Comparing m with w.length is a local-order diagnostic, not a checked semantic equivalence.",
            "Only the frozen PDF equation and Lean declaration are parsed; external review is not fed to this detector.",
            "Source SHA-256 verifies supplied bytes, not Git tree authenticity, source truth, or mathematical correctness.",
            "Reported checker and contract digests identify installed package bytes; they are not independently authenticated signatures.",
            "No Lean kernel, full manuscript-to-formal comparison, or Clay-target comparison was run.",
        ],
        "authority_effect": "NONE",
    }
