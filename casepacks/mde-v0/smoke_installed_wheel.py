"""Smoke installed core and MDE wheels without importing checkout source.

Run with the clean wheel virtual environment's interpreter from a directory
outside the repository. The script itself may live in the checkout: its
directory is not either Python package's source directory.
"""

from __future__ import annotations

import hashlib
import re
import sys
from importlib import metadata
from importlib.resources import files
from pathlib import Path

from aigc_lite_casepacks.mde_v0 import bio, citation

import app


def _installed_path(module: object) -> None:
    origin = Path(module.__file__).resolve()
    assert origin.is_relative_to(Path(sys.prefix).resolve()), origin


def main() -> None:
    assert metadata.version("aigc-lite") == "0.7.0"
    assert metadata.version("aigc-lite-mde-casepack") == "0.1.0"
    requires = metadata.requires("aigc-lite-mde-casepack") or []
    assert any(
        re.fullmatch(r"aigc-lite\s*==\s*0\.7\.0", requirement, re.IGNORECASE)
        for requirement in requires
    ), requires
    for module in (app, bio, citation):
        _installed_path(module)

    contract = files("aigc_lite_casepacks.mde_v0").joinpath(
        "bio_lineage_contract_v0.json"
    ).read_bytes()
    assert hashlib.sha256(contract).hexdigest() == bio.CHECKER_DEPENDENCY_SHA256[
        "bio_mde_contract"
    ]

    bio_report = bio.diagnose_bio_mde(b"{}", b"{}", [], [], [])
    assert bio_report["status"] == "UNRESOLVED"
    assert bio_report["qualification_granted"] is False
    citation_report = citation.diagnose_citation_mde(
        {}, ("match-1", "match-2"), [], [],
        identity_trust_roots={}, lineage_trust_roots={},
    )
    assert citation_report["status"] == "UNRESOLVED"
    assert citation_report["qualification_granted"] is False
    print("Installed core/MDE casepack boundary verified")


if __name__ == "__main__":
    main()
