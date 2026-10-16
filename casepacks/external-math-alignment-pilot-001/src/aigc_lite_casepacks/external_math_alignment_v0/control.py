"""Candidate-only comparison of *human-curated* statement fields.

This is a regression for one failure mode: a different proof method must not
be treated as a statement mismatch.  It neither extracts semantic fields from
PDF/Lean nor certifies that the supplied field mapping is correct.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_FIELDS = ("object", "domain", "hypothesis", "conclusion")


def compare_curated_pair(
    manuscript: Mapping[str, Any],
    formal: Mapping[str, Any],
    *,
    manuscript_method: str | None,
    formal_method: str | None,
) -> dict[str, Any]:
    """Compare one explicitly checked scope without making an authority decision."""

    missing = [
        field
        for field in _FIELDS
        if not isinstance(manuscript.get(field), str)
        or not manuscript[field].strip()
        or not isinstance(formal.get(field), str)
        or not formal[field].strip()
    ]
    if missing:
        local_alignment = "UNRESOLVED"
        mismatched: list[str] = []
    else:
        mismatched = [
            field for field in _FIELDS if manuscript[field] != formal[field]
        ]
        local_alignment = (
            "CHECKED_FIELD_GAP" if mismatched else "ALIGNED_UNDER_CHECKED_SCOPE"
        )

    if not isinstance(manuscript_method, str) or not manuscript_method.strip():
        method_alignment = "UNRESOLVED"
    elif not isinstance(formal_method, str) or not formal_method.strip():
        method_alignment = "UNRESOLVED"
    else:
        method_alignment = (
            "SAME_ROUTE"
            if manuscript_method == formal_method
            else "DIFFERENT_ROUTE"
        )

    return {
        "artifact_kind": "CANDIDATE_DIAGNOSIS",
        "local_statement_alignment": local_alignment,
        "mismatched_fields": mismatched,
        "missing_fields": missing,
        "proof_method_alignment": method_alignment,
        "full_statement_identity": "UNRESOLVED",
        "kernel_verification": "NOT_RUN",
        "authority_effect": "NONE",
        "limitations": [
            "Field values and their semantic mapping are human-curated inputs, not extracted or certified by this function.",
            "Equal checked fields do not establish whole-statement equivalence.",
            "Proof-method difference is reported separately and never creates a statement gap.",
        ],
    }
