"""The replay sandbox profile must not acquire ambient host authority."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapters.kernel_verification import math_project_sandbox
from app.core.errors import InvalidEvidenceError


def _fixture(tmp_path: Path, monkeypatch):
    project = tmp_path / "project"
    package = project / "lean" / ".lake" / "packages" / "example"
    (package / ".lake").mkdir(parents=True)
    (project / "lean" / "lake-manifest.json").write_text(
        json.dumps({"packages": [{"name": "example", "rev": "a" * 40}]}), encoding="utf-8"
    )
    toolchain = tmp_path / "toolchain" / "bin"
    toolchain.mkdir(parents=True)
    comparator = tmp_path / "comparator"
    comparator.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    for path in (
        toolchain / "lean", toolchain / "lake", comparator / "comparator",
        comparator / "lean4export", tools / "landrun",
    ):
        path.write_bytes(b"fixture")
    monkeypatch.setattr(math_project_sandbox, "_check_dependencies", lambda _root: (True, None))
    kwargs = {
        "lake_executable": toolchain / "lake",
        "lean_executable": toolchain / "lean",
        "comparator_root": comparator,
        "comparator_executable": comparator / "comparator",
        "landrun_executable": tools / "landrun",
        "lean4export_executable": comparator / "lean4export",
    }
    return project, package, kwargs


def test_bwrap_profile_hides_host_and_uses_ephemeral_build_mounts(tmp_path, monkeypatch):
    project, package, kwargs = _fixture(tmp_path, monkeypatch)
    args = math_project_sandbox.bubblewrap_math_project_args(project, **kwargs)
    assert args[-1] == "--"
    assert all(flag in args for flag in ("--unshare-net", "--unshare-pid", "--clearenv"))
    triples = tuple(zip(args, args[1:], args[2:], strict=False))
    pairs = tuple(zip(args, args[1:], strict=False))
    assert ("--ro-bind", "/", "/") not in triples
    assert ("--ro-bind", str(project), str(project)) in triples
    assert ("--tmpfs", str(project / "lean" / ".lake")) in pairs
    assert ("--ro-bind", str(package), str(package)) in triples
    assert ("--tmpfs", str(package / ".lake")) in pairs


def test_bwrap_profile_refuses_nonempty_package_build_mount(tmp_path, monkeypatch):
    project, package, kwargs = _fixture(tmp_path, monkeypatch)
    (package / ".lake" / "old.olean").write_bytes(b"stale")
    with pytest.raises(InvalidEvidenceError, match="not empty"):
        math_project_sandbox.bubblewrap_math_project_args(project, **kwargs)


def test_bwrap_profile_only_writes_disposable_dependency_copy(tmp_path, monkeypatch):
    project, package, kwargs = _fixture(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    copied = scratch / "example"
    (copied / ".git").mkdir(parents=True)
    (copied / ".lake").mkdir()
    monkeypatch.setattr(
        math_project_sandbox, "_git",
        lambda _root, *args: "a" * 40 if args[-2:] == ("rev-parse", "HEAD") else "",
    )
    args = math_project_sandbox.bubblewrap_math_project_args(
        project, dependency_scratch_root=scratch, **kwargs
    )
    triples = tuple(zip(args, args[1:], args[2:], strict=False))
    assert ("--bind", str(copied), str(package)) in triples
    assert ("--bind", str(project), str(project)) not in triples


def test_bwrap_profile_mounts_patched_closure_read_only(tmp_path, monkeypatch):
    project, package, kwargs = _fixture(tmp_path, monkeypatch)
    closure = tmp_path / ("a" * 64)
    copied = closure / "packages" / "example"
    copied.mkdir(parents=True)
    monkeypatch.setattr(
        "app.adapters.kernel_verification.math_project_closure.verify_patched_closure",
        lambda _project, _closure: {"closure_sha256": "a" * 64},
    )
    args = math_project_sandbox.bubblewrap_math_project_args(
        project, patched_closure_root=closure, **kwargs,
    )
    triples = tuple(zip(args, args[1:], args[2:], strict=False))
    assert ("--ro-bind", str(copied), str(package)) in triples
    assert ("--bind", str(copied), str(package)) not in triples


def test_build_work_root_requires_empty_directories(tmp_path, monkeypatch):
    project, package, kwargs = _fixture(tmp_path, monkeypatch)
    work = tmp_path / "work"
    (work / "project").mkdir(parents=True)
    (work / "packages" / "example").mkdir(parents=True)
    args = math_project_sandbox.bubblewrap_math_project_args(
        project, build_work_root=work, **kwargs,
    )
    triples = tuple(zip(args, args[1:], args[2:], strict=False))
    assert ("--bind", str(work / "project"), str(project / "lean" / ".lake")) in triples
    assert ("--bind", str(work / "packages" / "example"), str(package / ".lake")) in triples
    (work / "project" / "stale.olean").write_bytes(b"poison")
    with pytest.raises(InvalidEvidenceError, match="must be empty"):
        math_project_sandbox.bubblewrap_math_project_args(
            project, build_work_root=work, **kwargs,
        )
