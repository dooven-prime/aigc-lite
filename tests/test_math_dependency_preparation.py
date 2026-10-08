"""Manual dependency preparation rejects a changed source or unapproved URL."""

from __future__ import annotations

import json

import pytest

from scripts import prepare_math_case_003_dependencies as preparation
from scripts import prepare_math_case_003_scratch as scratch_preparation
from scripts import prepare_math_case_003_source as source_preparation


def _source(tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "lean").mkdir(parents=True)
    packages = [
        {
            "type": "git",
            "name": f"package{i}",
            "rev": "a" * 40,
            "url": f"https://github.com/example/package{i}.git",
        }
        for i in range(42)
    ]
    manifest = root / "lean" / "lake-manifest.json"
    manifest.write_text(json.dumps({"packagesDir": ".lake/packages", "packages": packages}))
    monkeypatch.setattr(preparation, "PINNED_FILES", {})
    monkeypatch.setattr(
        preparation,
        "_git",
        lambda *args, **_kwargs: preparation.SOURCE_COMMIT
        if "rev-parse" in args
        else "",
    )
    return root, manifest, packages


def test_dependency_preparation_rejects_non_github_source(tmp_path, monkeypatch):
    root, manifest, packages = _source(tmp_path, monkeypatch)
    assert len(preparation._validate_source(root)) == 42
    packages[0]["url"] = "https://evil.invalid/steal/package.git"
    manifest.write_text(json.dumps({"packagesDir": ".lake/packages", "packages": packages}))
    with pytest.raises(RuntimeError, match="Invalid Lake package identity"):
        preparation._validate_source(root)


def test_dependency_preparation_rejects_duplicate_package(tmp_path, monkeypatch):
    root, manifest, packages = _source(tmp_path, monkeypatch)
    packages[1]["name"] = packages[0]["name"]
    manifest.write_text(json.dumps({"packagesDir": ".lake/packages", "packages": packages}))
    with pytest.raises(RuntimeError, match="Invalid Lake package identity"):
        preparation._validate_source(root)


def test_source_preparation_reuses_only_fixed_origin(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    monkeypatch.setattr(source_preparation, "_git", lambda *args: "https://other.invalid/math.git")
    with pytest.raises(RuntimeError, match="fixed public repository"):
        source_preparation.prepare(root)


def test_source_preparation_refuses_symlink(tmp_path):
    link = tmp_path / "linked-source"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(RuntimeError, match="must not be a symlink"):
        source_preparation.prepare(link)


def test_dependency_scratch_refuses_reuse(tmp_path):
    project = tmp_path / "source"
    project.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(RuntimeError, match="already exists"):
        scratch_preparation.prepare(project, scratch)
