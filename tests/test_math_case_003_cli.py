"""The public replay command must retain the qualified read-only closure path."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app import cli, database
from app.adapters.kernel_verification import math_project, math_project_sandbox
from app.adapters.research_import.math_case_003 import SOURCE_COMMIT, THEOREM_NAME
from app.core.errors import InvalidEvidenceError
from app.profiles.math_project import PROJECT_RECEIPT_VERSION
from app.services import math_project as math_project_service


def _args(*extra: str):
    return cli._parser().parse_args(
        [
            "math-case-003", "replay", "--workspace", "isolated-test",
            "--claim-id", "claim", "--snapshot-id", "snapshot",
            "--project-root", "/source", "--lake", "/tools/lake",
            "--lean", "/tools/lean", "--comparator", "/comparator/bin",
            "--sandbox-profile", "bwrap-v1", "--comparator-root", "/comparator",
            "--landrun", "/tools/landrun", "--lean4export", "/comparator/export",
            *extra,
        ]
    )


def test_replay_cli_passes_read_only_closure_and_fresh_build_work(monkeypatch, capsys):
    captured = {}

    def fake_bwrap(_project, **kwargs):
        captured["policy"] = kwargs
        return ("--",)

    class FakeBackend:
        def __init__(self, _project, **kwargs):
            captured["backend"] = kwargs

    class FakeService:
        def __init__(self, _provider):
            pass

        async def verify(self, _context, _claim, _snapshot, *, backend):
            assert isinstance(backend, FakeBackend)
            return {
                "run": {"id": "run", "status": "failed"},
                "verification_attempt": {"id": "attempt"},
                "receipt_artifact": {"id": "receipt"},
            }

    monkeypatch.setattr(database, "get_repository", lambda: object())
    monkeypatch.setattr(math_project_sandbox, "bubblewrap_math_project_args", fake_bwrap)
    monkeypatch.setattr(math_project, "MathProjectComparatorBackend", FakeBackend)
    monkeypatch.setattr(math_project_service, "MathProjectSliceService", FakeService)

    cli._math_case_003(
        _args("--patched-closure-root", "/closure", "--build-work-root", "/fresh-work")
    )
    assert captured["policy"]["patched_closure_root"] == Path("/closure")
    assert captured["policy"]["build_work_root"] == Path("/fresh-work")
    assert captured["policy"]["dependency_scratch_root"] is None
    assert captured["backend"]["patched_closure_root"] == Path("/closure")
    assert json.loads(capsys.readouterr().out)["qualification_granted"] is False


@pytest.mark.parametrize(
    "extra",
    [(), ("--patched-closure-root", "/closure", "--dependency-scratch-root", "/scratch")],
)
def test_replay_cli_rejects_missing_or_ambiguous_dependency_mode(monkeypatch, extra):
    monkeypatch.setattr(database, "get_repository", lambda: object())
    with pytest.raises(SystemExit, match="exactly one"):
        cli._math_case_003(_args(*extra))


def test_review_candidate_cli_never_evaluates_qualification(monkeypatch, tmp_path, capsys):
    note = tmp_path / "review.md"
    note.write_bytes(b"candidate-only review")

    class FakeService:
        def __init__(self, _provider):
            pass

        def store_rtheta_candidate_review(self, _context, content):
            assert content == b"candidate-only review"
            return {"artifact": {"id": "candidate", "content_hash": "a" * 64}}

    monkeypatch.setattr(database, "get_repository", lambda: object())
    monkeypatch.setattr(math_project_service, "MathProjectSliceService", FakeService)
    args = cli._parser().parse_args(
        ["math-case-003", "review-candidate", "--workspace", "isolated-test",
         "--review-file", str(note)]
    )
    cli._math_case_003(args)
    result = json.loads(capsys.readouterr().out)
    assert result["candidate_review_artifact_id"] == "candidate"
    assert result["qualification_granted"] is False
    assert result["current_use_binding"] is None


def test_receipt_cli_exports_only_byte_closed_fixed_case(monkeypatch, capsys):
    content = json.dumps(
        {
            "contract_version": PROJECT_RECEIPT_VERSION,
            "source_commit": SOURCE_COMMIT,
            "theorem_name": THEOREM_NAME,
        },
        sort_keys=True,
    )
    artifact = {
        "id": "receipt", "content_text": content,
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "metadata": {"role": "project_verification_receipt"},
    }

    class FakeRepository:
        def get_artifact(self, _workspace, _artifact_id):
            return artifact

    class FakeService:
        def __init__(self, _provider):
            pass

    monkeypatch.setattr(database, "get_repository", FakeRepository)
    monkeypatch.setattr(math_project_service, "MathProjectSliceService", FakeService)
    args = cli._parser().parse_args(
        ["math-case-003", "receipt", "--workspace", "isolated-test", "--receipt-id", "receipt"]
    )
    cli._math_case_003(args)
    assert json.loads(capsys.readouterr().out)["content_sha256"] == artifact["content_hash"]
    artifact["content_hash"] = "0" * 64
    with pytest.raises(InvalidEvidenceError, match="byte-closed"):
        cli._math_case_003(args)
