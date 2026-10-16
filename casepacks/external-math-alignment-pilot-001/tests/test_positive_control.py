"""Known-answer, manually curated control for method/statement separation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aigc_lite_casepacks.external_math_alignment_v0.control import (  # noqa: E402
    compare_curated_pair,
)

TRACE_SQUARE_FIELDS = {
    "object": "square real matrix A",
    "domain": "finite n by n, n natural",
    "hypothesis": "A is symmetric",
    "conclusion": "trace(A squared) is nonnegative",
}


def test_different_valid_route_does_not_create_statement_gap() -> None:
    result = compare_curated_pair(
        TRACE_SQUARE_FIELDS,
        dict(TRACE_SQUARE_FIELDS),
        manuscript_method="orthogonal diagonalization and eigenvalue squares",
        formal_method="entrywise expansion and symmetry to squares",
    )
    assert result["local_statement_alignment"] == "ALIGNED_UNDER_CHECKED_SCOPE"
    assert result["mismatched_fields"] == []
    assert result["proof_method_alignment"] == "DIFFERENT_ROUTE"
    assert result["full_statement_identity"] == "UNRESOLVED"
    assert result["kernel_verification"] == "NOT_RUN"
    assert result["authority_effect"] == "NONE"


def test_changed_hypothesis_is_a_checked_field_gap() -> None:
    formal = dict(TRACE_SQUARE_FIELDS)
    formal["hypothesis"] = "A is positive semidefinite"
    result = compare_curated_pair(
        TRACE_SQUARE_FIELDS,
        formal,
        manuscript_method="orthogonal diagonalization",
        formal_method="orthogonal diagonalization",
    )
    assert result["local_statement_alignment"] == "CHECKED_FIELD_GAP"
    assert result["mismatched_fields"] == ["hypothesis"]
    assert result["proof_method_alignment"] == "SAME_ROUTE"


def test_missing_statement_field_abstains_even_when_methods_match() -> None:
    formal = dict(TRACE_SQUARE_FIELDS)
    del formal["hypothesis"]
    result = compare_curated_pair(
        TRACE_SQUARE_FIELDS,
        formal,
        manuscript_method="entrywise expansion",
        formal_method="entrywise expansion",
    )
    assert result["local_statement_alignment"] == "UNRESOLVED"
    assert result["missing_fields"] == ["hypothesis"]
    assert result["authority_effect"] == "NONE"
