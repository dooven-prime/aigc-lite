"""Case-003 qualification must not confuse kernel compilation with identity."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.adapters.kernel_verification import math_project as math_project_backend
from app.adapters.kernel_verification.math_project import (
    MathProjectComparatorBackend,
    ProjectReplayObservation,
    _run,
)
from app.adapters.kernel_verification.process import LeanKernelVerifierBackend
from app.adapters.research_import import math_case_003
from app.adapters.research_import.math_case_003 import PinnedSourceFile
from app.core.contracts import RequestContext
from app.core.errors import InvalidEvidenceError
from app.core.kernel_verification import (
    KernelBackendKind,
    KernelExecutionStatus,
    KernelVerificationDraft,
)
from app.core.qualification import ValidationModality, canonical_hash
from app.core.research import VerificationAttemptDraft, VerificationKind, VerificationOutcome
from app.profiles.math_project import PROFILE_ID
from app.repository import SQLiteRepository
from app.services.math_project import MathProjectSliceService
from app.services.qualification import QualificationService
from app.services.research_registry import ResearchRegistryService


def _fixture_sources(monkeypatch) -> tuple[PinnedSourceFile, ...]:
    payloads = {
        path: (b"%PDF-1.4\nfixture" if path == math_case_003.PDF_PATH else path.encode())
        for path in math_case_003.PINNED_FILES
    }
    monkeypatch.setattr(
        math_case_003,
        "PINNED_FILES",
        {path: hashlib.sha256(content).hexdigest() for path, content in payloads.items()},
    )
    return tuple(
        PinnedSourceFile(path, hashlib.sha256(content).hexdigest(), content)
        for path, content in payloads.items()
    )


def _observation(
    *, kernel_exit: int, comparator_exit: int, patch_closure_verified: bool = True
) -> ProjectReplayObservation:
    return ProjectReplayObservation(
        worktree_clean=True,
        dependencies_closed=True,
        mathlib_rev="d13f23b723b8a846827a245b89c10fc7d3f11612",
        clean_build_exit_code=0,
        kernel_exit_code=kernel_exit,
        comparator_exit_code=comparator_exit,
        lean_executable_hash="1" * 64,
        lake_executable_hash="2" * 64,
        lean_version="Lean 4.34.1",
        lake_version="Lake (Lean version 4.34.1)",
        kernel_stdout_hash="a" * 64,
        kernel_stderr_hash="b" * 64,
        comparator_stdout_hash="c" * 64,
        comparator_stderr_hash="d" * 64,
        executable_hash="e" * 64,
        sandbox_hash="f" * 64,
        command=("lake", "env", "comparator", "ComparatorChallenges/QuasiRiemannHypothesis.json"),
        patch_closure_verified=patch_closure_verified,
    )


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process-group cancellation requires POSIX")
async def test_project_process_timeout_is_inconclusive(tmp_path):
    status, _stdout, _stderr = await _run(
        (sys.executable, "-c", "import time; time.sleep(5)"), tmp_path, 0.05
    )
    assert status is None


class _Backend:
    def __init__(self, observation: ProjectReplayObservation) -> None:
        self.observation = observation

    async def verify(self) -> ProjectReplayObservation:
        return self.observation


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="project replay requires Linux")
@pytest.mark.parametrize("comparator_exit", [0, 1, None])
async def test_replay_never_prebuilds_untrusted_solution(
    tmp_path, monkeypatch, comparator_exit: int | None
) -> None:
    project = tmp_path / "source"
    (project / "lean").mkdir(parents=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("lake", "lean", "comparator", "sandbox"):
        (tools / name).write_bytes(name.encode())
    monkeypatch.setattr(math_project_backend, "PINNED_FILES", {})
    monkeypatch.setattr(
        math_project_backend,
        "_git",
        lambda _root, *args: math_project_backend.SOURCE_COMMIT
        if args == ("rev-parse", "HEAD")
        else "",
    )
    monkeypatch.setattr(
        math_project_backend,
        "_check_dependencies",
        lambda _root: (True, "d13f23b723b8a846827a245b89c10fc7d3f11612"),
    )
    calls: list[tuple[str, ...]] = []

    async def fake_run(command: tuple[str, ...], _cwd: Path, _timeout: float):
        calls.append(command)
        if command[-1] == "--version":
            return 0, b"Lean/Lake 4.34.1", b""
        return comparator_exit, b"Comparator result", b""

    monkeypatch.setattr(math_project_backend, "_run", fake_run)
    backend = MathProjectComparatorBackend(
        project,
        lake_executable=tools / "lake",
        lean_executable=tools / "lean",
        comparator_executable=tools / "comparator",
        sandbox_executable=tools / "sandbox",
        sandbox_fixed_args=("--",),
    )
    altered_policy = MathProjectComparatorBackend(
        project,
        lake_executable=tools / "lake",
        lean_executable=tools / "lean",
        comparator_executable=tools / "comparator",
        sandbox_executable=tools / "sandbox",
        sandbox_fixed_args=("--ro-bind", "/", "/", "--"),
    )
    assert backend._sandbox_policy_hash != altered_policy._sandbox_policy_hash
    result = await backend.verify()
    assert len(calls) == 3  # two version probes, then Comparator alone
    assert calls[-1][-4:-1] == (str(tools / "lake"), "env", str(tools / "comparator"))
    assert calls[-1][-1] == "ComparatorChallenges/QuasiRiemannHypothesis.json"
    assert result.comparator_exit_code == comparator_exit
    assert result.sandbox_hash == backend._sandbox_policy_hash
    assert result.kernel_exit_code == (0 if comparator_exit == 0 else None)
    assert result.clean_build_exit_code == (0 if comparator_exit == 0 else None)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="project replay requires Linux")
async def test_patched_closure_requires_read_only_mount_before_replay(tmp_path, monkeypatch):
    project = tmp_path / "project"
    lean_root = project / "lean"
    lean_root.mkdir(parents=True)
    (lean_root / "lake-manifest.json").write_text(
        json.dumps({"packages": [{"name": "example", "rev": "a" * 40}]}),
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("lake", "lean", "comparator", "sandbox"):
        (tools / name).write_bytes(name.encode())
    closure = tmp_path / ("a" * 64)
    (closure / "packages" / "example").mkdir(parents=True)
    monkeypatch.setattr(math_project_backend, "PINNED_FILES", {})
    monkeypatch.setattr(
        math_project_backend, "_git",
        lambda _root, *args: math_project_backend.SOURCE_COMMIT
        if args == ("rev-parse", "HEAD") else "",
    )
    monkeypatch.setattr(math_project_backend, "_check_dependencies", lambda _root: (True, "mathlib"))
    monkeypatch.setattr(
        "app.adapters.kernel_verification.math_project_closure.verify_patched_closure",
        lambda _project, _closure: {"closure_sha256": "a" * 64},
    )

    async def fake_run(command, _cwd, _timeout):
        return (0, b"Lean/Lake 4.34.1", b"") if command[-1] == "--version" else (0, b"", b"")

    monkeypatch.setattr(math_project_backend, "_run", fake_run)
    backend = MathProjectComparatorBackend(
        project,
        lake_executable=tools / "lake", lean_executable=tools / "lean",
        comparator_executable=tools / "comparator", sandbox_executable=tools / "sandbox",
        sandbox_fixed_args=("--ro-bind", str(closure / "packages" / "example"),
                            str(lean_root / ".lake" / "packages" / "example"), "--"),
        patched_closure_root=closure,
    )
    observation = await backend.verify()
    assert observation.patch_closure_verified is True
    assert observation.patched_closure_sha256 == "a" * 64

    unmounted = MathProjectComparatorBackend(
        project,
        lake_executable=tools / "lake", lean_executable=tools / "lean",
        comparator_executable=tools / "comparator", sandbox_executable=tools / "sandbox",
        sandbox_fixed_args=("--",), patched_closure_root=closure,
    )
    with pytest.raises(InvalidEvidenceError, match="not mounted read-only"):
        await unmounted.verify()


@pytest.mark.asyncio
async def test_compiled_weaker_statement_cannot_be_qualified(tmp_path, monkeypatch) -> None:
    """A real Lean compile may pass while the pinned Comparator check fails."""

    executable = os.environ.get("AIGC_LITE_TEST_LEAN_EXE") or shutil.which("lean")
    if (
        not executable
        or subprocess.run((executable, "--version"), capture_output=True, check=False).returncode
    ):
        pytest.skip("A working Lean toolchain is not installed")
    lean = LeanKernelVerifierBackend(executable)
    compiled = await lean.verify(
        KernelVerificationDraft(
            backend=KernelBackendKind.LEAN4,
            declaration_name="weak_zeta",
            source=(
                "def riemannZeta (s : Nat) : Nat := s\n"
                "theorem weak_zeta (s : Nat) (assume_nonzero : s ≠ 0) : "
                "riemannZeta s ≠ 0 := by\n"
                "  exact assume_nonzero\n"
            ),
        )
    )
    assert compiled.status is KernelExecutionStatus.PASSED
    await _assert_weak_statement_blocked(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_failed_statement_check_blocks_kernel_success_even_without_lean(
    tmp_path, monkeypatch
) -> None:
    await _assert_weak_statement_blocked(tmp_path, monkeypatch)


async def _assert_weak_statement_blocked(tmp_path, monkeypatch) -> None:
    repository = SQLiteRepository(Path(tmp_path) / "math-slice.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    context = RequestContext("slice-negative", "workspace-a", "admin-a")
    service = MathProjectSliceService(provider)
    snapshot = service.store_snapshot(context, _fixture_sources(monkeypatch))
    claim = service.freeze_claim(context, snapshot["snapshot"]["id"])["claim"]
    before = QualificationService(provider).evaluate(context, claim["id"], PROFILE_ID)
    assert before["evaluation"]["verdict"] == "UNRESOLVED"
    assert next(
        item for item in before["evaluation"]["criteria"]
        if item["code"] == "source_snapshot"
    )["state"] == "satisfied"
    evidence = await service.verify(
        context,
        claim["id"],
        snapshot["snapshot"]["id"],
        backend=_Backend(_observation(kernel_exit=0, comparator_exit=1)),
    )
    gate = QualificationService(provider).evaluate(context, claim["id"], PROFILE_ID)
    legacy_gate = QualificationService(provider).evaluate(context, claim["id"], "math.formal.v1")

    assert evidence["verification_attempt"]["outcome"] == "failed"
    assert evidence["qualification_granted"] is False
    assert gate["evaluation"]["verdict"] == "BLOCKED"
    assert "comparator_rejected" in gate["evaluation"]["blockers"]
    assert gate["evaluation"]["evidence_vector"]["statement_identity"] == "undetermined"
    assert gate["qualification_receipt"] is None
    assert gate["current_use_binding"] is None
    assert legacy_gate["evaluation"]["verdict"] == "NOT_APPLICABLE"
    assert legacy_gate["qualification_receipt"] is None
    assert repository.list_current_use_bindings(context.workspace_id, PROFILE_ID) == []


def test_snapshot_tampering_is_rejected(tmp_path, monkeypatch) -> None:
    repository = SQLiteRepository(Path(tmp_path) / "math-snapshot.db")
    repository.init()
    context = RequestContext("slice-snapshot", "workspace-a", "admin-a")
    service = MathProjectSliceService(lambda: repository)
    snapshot = service.store_snapshot(context, _fixture_sources(monkeypatch))
    artifacts = {item["id"]: item for item in snapshot["source_artifacts"]}
    assert math_case_003.validate_snapshot_files(snapshot["manifest"]["files"], artifacts)
    first = snapshot["manifest"]["files"][0]["artifact_ids"][0]
    artifacts[first] = {**artifacts[first], "content_text": "AAAA"}
    assert not math_case_003.validate_snapshot_files(snapshot["manifest"]["files"], artifacts)


@pytest.mark.asyncio
@pytest.mark.parametrize("patch_closure_verified", [False, True])
async def test_simulated_complete_evidence_still_requires_knowledge_admission(
    tmp_path, monkeypatch, patch_closure_verified: bool
) -> None:
    """Positive gate wiring test; the Comparator observation is a test double."""

    repository = SQLiteRepository(Path(tmp_path) / "math-positive.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    context = RequestContext("slice-positive", "workspace-a", "human-reviewer")
    service = MathProjectSliceService(provider)
    snapshot = service.store_snapshot(context, _fixture_sources(monkeypatch))
    claim = service.freeze_claim(context, snapshot["snapshot"]["id"])["claim"]
    await service.verify(
        context,
        claim["id"],
        snapshot["snapshot"]["id"],
        backend=_Backend(_observation(
            kernel_exit=0, comparator_exit=0,
            patch_closure_verified=patch_closure_verified,
        )),
    )
    ResearchRegistryService(provider).record_verification_attempt(
        context,
        claim["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.REVIEW,
            outcome=VerificationOutcome.PASSED,
            validation_modality=ValidationModality.EXPERT_REVIEW,
            method="Human paper-to-challenge statement review fixture.",
            scope="Theorem 1.1 zeta subclaim only.",
            input_digest=canonical_hash({
                "claim": claim["semantic_hash"],
                "snapshot": snapshot["snapshot"]["content_hash"],
            }),
            output_digest=snapshot["snapshot"]["content_hash"],
            artifact_ids=(snapshot["snapshot"]["id"],),
        ),
    )
    result = QualificationService(provider).evaluate(context, claim["id"], PROFILE_ID)
    assert result["evaluation"]["verdict"] == (
        "ADMITTED" if patch_closure_verified else "UNRESOLVED"
    )
    assert (result["qualification_receipt"] is not None) is patch_closure_verified
    if patch_closure_verified:
        assert result["qualification_receipt"]["independence_summary"]["qualified"] is False
    assert result["current_use_binding"] is None
    assert repository.list_current_use_bindings(context.workspace_id, PROFILE_ID) == []
