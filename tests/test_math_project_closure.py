"""Pinned patch closure and build cache must fail closed on changed bytes."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess

import pytest

from app.adapters.kernel_verification import math_project_closure as closure
from app.core.errors import InvalidEvidenceError
from scripts import math_case_003_build_cache as cache


def _git(path, *args):
    return subprocess.run(
        ("git", "-C", str(path), *args), capture_output=True,
        text=True, check=True,
    ).stdout.strip()


def _fixture(tmp_path, monkeypatch):
    project = tmp_path / "source"
    package = project / "lean" / ".lake" / "packages" / "example"
    package.mkdir(parents=True)
    (package / ".lake").mkdir()
    (project / "lean" / "lake-manifest.json").write_text("{}", encoding="utf-8")
    _git(package, "init")
    _git(package, "remote", "add", "origin", "https://github.com/example/example.git")
    prefix = "".join(f"-- prefix {i}\n" for i in range(5))
    suffix = "".join(f"-- suffix {i}\n" for i in range(5))
    (package / "Example.lean").write_text(prefix + "theorem original : True := trivial\n" + suffix)
    _git(package, "add", "Example.lean")
    _git(package, "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-m", "base")
    revision = _git(package, "rev-parse", "HEAD")
    (package / "Example.lean").write_text(prefix + "theorem updated : True := trivial\n" + suffix)
    patch_dir = project / "lean" / "patches"
    patch_dir.mkdir()
    patch = patch_dir / "example-lean4341.patch"
    patch.write_text(_git(package, "diff", "HEAD") + "\n")
    _git(package, "checkout", "--", "Example.lean")
    item = {"name": "example", "rev": revision, "url": "https://github.com/example/example.git"}
    monkeypatch.setattr(closure, "_validate_project", lambda _project: None)
    monkeypatch.setattr(closure, "_patches", lambda _project: {"example": patch})
    monkeypatch.setattr(closure, "_package_manifest", lambda _project: [item])
    return project, package, item


@pytest.mark.skipif(os.name != "posix", reason="sealed dependency tree is Linux-only")
def test_patched_tree_has_address_and_rejects_changed_bytes(tmp_path, monkeypatch):
    project, package, _item = _fixture(tmp_path, monkeypatch)
    sealed = closure.prepare_patched_closure(project, tmp_path / "store")
    result = closure.verify_patched_closure(project, sealed)
    assert result["closure_sha256"] == sealed.name
    assert result["patch_count"] == 1
    assert "theorem original" in (package / "Example.lean").read_text()
    changed = sealed / "packages" / "example" / "Example.lean"
    changed.chmod(0o644)
    changed.write_text("theorem tampered : True := trivial\n")
    with pytest.raises(InvalidEvidenceError):
        closure.verify_patched_closure(project, sealed)


@pytest.mark.skipif(os.name != "posix", reason="sealed build cache is Linux-only")
def test_build_cache_binds_environment_and_rejects_tampering(tmp_path, monkeypatch):
    project, _package, item = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(cache, "_package_manifest", lambda _project: [item])
    monkeypatch.setattr(
        cache, "verify_patched_closure",
        lambda _project, _closure: {"closure_sha256": "a" * 64},
    )
    work = tmp_path / "work"
    cache.prepare_work(project, work)
    artifact = work / "packages" / "example" / "Example.olean"
    artifact.write_bytes(b"compiled-output")
    binary = tmp_path / "lean"
    binary.write_bytes(b"toolchain")
    store = tmp_path / "store"
    entry = cache.capture(project, tmp_path / "closure", work, store, {"lean": binary}, "b" * 64)
    assert cache.verify(project, tmp_path / "closure", entry, {"lean": binary}, "b" * 64)["qualification_use"] == "forbidden"
    with pytest.raises(RuntimeError, match="environment changed"):
        cache.verify(project, tmp_path / "closure", entry, {"lean": binary}, "c" * 64)
    moved_artifact = entry / "packages" / "example" / "Example.olean"
    moved_artifact.chmod(0o644)
    moved_artifact.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="bytes changed"):
        cache.verify(project, tmp_path / "closure", entry, {"lean": binary}, "b" * 64)


@pytest.mark.skipif(os.name != "posix", reason="sealed dependency tree is Linux-only")
def test_forged_address_cannot_hide_extra_edit_in_patched_file(tmp_path, monkeypatch):
    project, _package, _item = _fixture(tmp_path, monkeypatch)
    sealed = closure.prepare_patched_closure(project, tmp_path / "store")
    changed = sealed / "packages" / "example" / "Example.lean"
    changed.chmod(0o644)
    changed.write_text(changed.read_text() + "-- unauthorized extra line\n")
    manifest_path = sealed / "manifest.json"
    manifest_path.chmod(0o644)
    manifest = json.loads(manifest_path.read_bytes())
    manifest["packages"][0]["tree"] = closure._digest_tree(changed.parent)
    manifest_path.write_bytes(closure._canonical(manifest) + b"\n")
    forged = sealed.with_name(hashlib.sha256(closure._canonical(manifest)).hexdigest())
    sealed.rename(forged)
    with pytest.raises(InvalidEvidenceError, match="Patched bytes differ"):
        closure.verify_patched_closure(project, forged)
