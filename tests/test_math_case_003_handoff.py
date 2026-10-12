"""The portable handoff must match the immutable case-003 source contract."""

from __future__ import annotations

import json
from pathlib import Path

from app.adapters.research_import.math_case_003 import (
    CHALLENGE_PATH,
    CONFIG_PATH,
    PDF_PATH,
    PINNED_FILES,
    SOLUTION_PATH,
    SOURCE_COMMIT,
    SOURCE_REPOSITORY,
    THEOREM_NAME,
)


def test_math_003_handoff_is_exactly_the_frozen_target():
    package = Path(__file__).resolve().parents[1] / "replay_packages" / "math-003-zeta-7-8-v1"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    target = manifest["target"]
    assert manifest["contract_version"] == "math-003.replay-handoff.v1"
    assert target["source_repository"] == SOURCE_REPOSITORY
    assert target["source_commit"] == SOURCE_COMMIT
    assert target["theorem_name"] == THEOREM_NAME
    assert target["paper_path"] == PDF_PATH
    assert target["challenge_path"] == CHALLENGE_PATH
    assert target["config_path"] == CONFIG_PATH
    assert target["solution_path"] == SOLUTION_PATH
    assert manifest["source_file_sha256"] == PINNED_FILES
    assert manifest["execution_contract"]["challenge_first"] is True
    assert manifest["execution_contract"]["precompiled_solution_allowed"] is False
    assert manifest["qualification_boundaries"]["machine_replay_success_is_paper_alignment"] is False
