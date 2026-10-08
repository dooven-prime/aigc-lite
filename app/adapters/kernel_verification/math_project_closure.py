"""Content-addressed, read-only compatibility-patched Lake dependency trees.

The closure is a preparation artifact, not a mathematical verification. A
replay rehashes it and mounts it read-only before invoking untrusted Lake code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from ...adapters.research_import.math_case_003 import PINNED_FILES, SOURCE_COMMIT
from ...core.errors import InvalidEvidenceError
from .math_project import _check_dependencies, _git, _hash_file

_NAME = re.compile(r"[A-Za-z0-9_.-]+\Z")
_PATCH_SUFFIX = "-lean4341.patch"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest_tree(root: Path) -> dict[str, object]:
    """Hash source bytes, names, and executable bits; exclude Git and build state."""

    digest = hashlib.sha256()
    count = size = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        linked_dirs = sorted(name for name in dirs if (base / name).is_symlink())
        for name in linked_dirs:
            path = base / name
            digest.update(_canonical([path.relative_to(root).as_posix(), "symlink", os.readlink(path)]))
            digest.update(b"\n")
            count += 1
        dirs[:] = sorted(name for name in dirs if name not in {".git", ".lake"} and name not in linked_dirs)
        for name in sorted(files):
            path = base / name
            if path.is_symlink():
                digest.update(_canonical([path.relative_to(root).as_posix(), "symlink", os.readlink(path)]))
                digest.update(b"\n")
                count += 1
                continue
            if not path.is_file():
                raise InvalidEvidenceError("dependencies", f"Unsafe source entry: {path}")
            relative = path.relative_to(root).as_posix()
            mode = stat.S_IMODE(path.stat().st_mode)
            file_size = path.stat().st_size
            digest.update(_canonical([relative, bool(mode & 0o111), file_size, _hash_file(path)]))
            digest.update(b"\n")
            count += 1
            size += file_size
    return {"sha256": digest.hexdigest(), "file_count": count, "byte_count": size}


def _patches(project: Path) -> dict[str, Path]:
    patches = {}
    for relative, expected in PINNED_FILES.items():
        if not relative.startswith("lean/patches/") or not relative.endswith(_PATCH_SUFFIX):
            continue
        path = project / relative
        if path.is_symlink() or _hash_file(path) != expected:
            raise InvalidEvidenceError("source", f"Pinned patch changed: {relative}")
        name = path.name.removesuffix(_PATCH_SUFFIX)
        if not _NAME.fullmatch(name) or name in patches:
            raise InvalidEvidenceError("source", "Invalid or duplicate compatibility patch name")
        patches[name] = path
    if len(patches) != 23:
        raise InvalidEvidenceError("source", "Expected 23 pinned compatibility patches")
    return patches


def _git_apply(package: Path, *args: str) -> None:
    result = subprocess.run(
        ("git", "-C", str(package), "apply", *args),
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode:
        raise InvalidEvidenceError("dependencies", f"Compatibility patch failed: {package.name}")


def _package_manifest(project: Path) -> list[dict[str, str]]:
    manifest = json.loads((project / "lean" / "lake-manifest.json").read_text(encoding="utf-8"))
    packages = manifest.get("packages")
    if manifest.get("packagesDir") != ".lake/packages" or not isinstance(packages, list) or len(packages) != 42:
        raise InvalidEvidenceError("dependencies", "Expected fixed 42-package Lake manifest")
    result = []
    seen = set()
    for item in packages:
        name = item["name"].strip("«»")
        if not _NAME.fullmatch(name) or name in seen or item.get("type") != "git":
            raise InvalidEvidenceError("dependencies", "Invalid package manifest identity")
        seen.add(name)
        result.append({"name": name, "rev": item["rev"], "url": item["url"]})
    return result


def _validate_project(project: Path) -> None:
    if _git(project, "rev-parse", "HEAD") != SOURCE_COMMIT or _git(project, "status", "--porcelain", "--untracked-files=all"):
        raise InvalidEvidenceError("source", "Project checkout is not clean at the pinned commit")
    for relative, expected in PINNED_FILES.items():
        path = (project / relative).resolve(strict=True)
        if not path.is_relative_to(project) or _hash_file(path) != expected:
            raise InvalidEvidenceError("source", f"Pinned source changed: {relative}")
    if not _check_dependencies(project / "lean")[0]:
        raise InvalidEvidenceError("dependencies", "Original Lake dependencies are not clean and pinned")


def _expected_patch_paths(package: Path, patch: Path) -> set[str]:
    result = subprocess.run(
        ("git", "-C", str(package), "apply", "--numstat", str(patch)),
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode:
        raise InvalidEvidenceError("dependencies", "Cannot inspect compatibility patch paths")
    return {line.split("\t", 2)[-1] for line in result.stdout.splitlines()}


def _check_package(package: Path, item: dict[str, str], patch: Path | None) -> dict[str, object]:
    if package.is_symlink() or not package.is_dir() or _git(package, "rev-parse", "HEAD") != item["rev"]:
        raise InvalidEvidenceError("dependencies", f"Dependency revision mismatch: {item['name']}")
    if _git(package, "remote", "get-url", "origin") != item["url"]:
        raise InvalidEvidenceError("dependencies", f"Dependency origin mismatch: {item['name']}")
    changed = _git(package, "status", "--porcelain", "--untracked-files=all")
    if patch is None:
        if changed:
            raise InvalidEvidenceError("dependencies", f"Unpatched dependency is dirty: {item['name']}")
    else:
        _git_apply(package, "--reverse", "--check", str(patch))
        expected = _expected_patch_paths(package, patch)
        actual = set(_git(package, "diff", "--name-only", "HEAD").splitlines())
        actual.update(_git(package, "ls-files", "--others", "--exclude-standard").splitlines())
        if not actual or not actual.issubset(expected):
            raise InvalidEvidenceError("dependencies", f"Unexpected patched paths: {item['name']}")
    return {
        **item,
        "patch_sha256": _hash_file(patch) if patch else None,
        "tree": _digest_tree(package),
    }


def prepare_patched_closure(project: Path, store: Path) -> Path:
    """Build once in a unique staging directory, then atomically seal by digest."""

    if os.name != "posix":
        raise InvalidEvidenceError("dependencies", "Patched closure preparation requires POSIX")
    project = project.resolve(strict=True)
    store = store.parent.resolve(strict=True) / store.name
    if store.is_symlink() or store.is_relative_to(project) or project.is_relative_to(store):
        raise InvalidEvidenceError("dependencies", "Closure store must be external and non-symlinked")
    _validate_project(project)
    patches = _patches(project)
    items = _package_manifest(project)
    if set(patches) - {item["name"] for item in items}:
        raise InvalidEvidenceError("dependencies", "Patch does not name a pinned package")
    store.mkdir(parents=True, exist_ok=True)
    staging = store / f".staging-{uuid4().hex}"
    staging.mkdir()
    (staging / "packages").mkdir()
    records = []
    for item in items:
        name = item["name"]
        source = project / "lean" / ".lake" / "packages" / name
        target = staging / "packages" / name
        _digest_tree(source)  # Hash tracked symlink text, never dereference it.
        shutil.copytree(source, target, symlinks=True)
        patch = patches.get(name)
        if patch:
            _git_apply(target, "--check", str(patch))
            _git_apply(target, str(patch))
        records.append({**_check_package(target, item, patch), "source_tree": _digest_tree(source)})
    manifest = {
        "contract_version": "math-project.patched-dependencies.v1",
        "source_commit": SOURCE_COMMIT,
        "lake_manifest_sha256": _hash_file(project / "lean" / "lake-manifest.json"),
        "packages": records,
    }
    identity = hashlib.sha256(_canonical(manifest)).hexdigest()
    destination = store / identity
    if destination.exists() or destination.is_symlink():
        raise InvalidEvidenceError("dependencies", "Closure digest already exists; verify it instead")
    (staging / "manifest.json").write_bytes(_canonical(manifest) + b"\n")
    for path in sorted(staging.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_file():
            path.chmod(path.stat().st_mode & ~0o222)
        elif path.is_dir():
            path.chmod(path.stat().st_mode & ~0o222)
    staging.rename(destination)
    return destination


def verify_patched_closure(project: Path, closure: Path) -> dict[str, object]:
    """Rehash all package bytes and verify patches, pins, and address before replay."""

    project = project.resolve(strict=True)
    if closure.is_symlink():
        raise InvalidEvidenceError("dependencies", "Closure path must not be a symlink")
    closure = closure.resolve(strict=True)
    if closure.name in {"", ".", ".."} or not re.fullmatch(r"[0-9a-f]{64}", closure.name):
        raise InvalidEvidenceError("dependencies", "Closure path is not content-addressed")
    _validate_project(project)
    patches = _patches(project)
    raw = (closure / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    if raw != _canonical(manifest) + b"\n" or hashlib.sha256(_canonical(manifest)).hexdigest() != closure.name:
        raise InvalidEvidenceError("dependencies", "Closure manifest address mismatch")
    if manifest.get("contract_version") != "math-project.patched-dependencies.v1" or manifest.get("source_commit") != SOURCE_COMMIT:
        raise InvalidEvidenceError("dependencies", "Closure contract or source mismatch")
    if manifest.get("lake_manifest_sha256") != _hash_file(project / "lean" / "lake-manifest.json"):
        raise InvalidEvidenceError("dependencies", "Closure Lake manifest mismatch")
    records = manifest.get("packages")
    items = _package_manifest(project)
    if not isinstance(records, list) or len(records) != len(items):
        raise InvalidEvidenceError("dependencies", "Closure package count mismatch")
    packages_root = closure / "packages"
    if packages_root.is_symlink() or {path.name for path in packages_root.iterdir()} != {item["name"] for item in items}:
        raise InvalidEvidenceError("dependencies", "Unexpected closure package path")
    for item, record in zip(items, records, strict=True):
        patch = patches.get(item["name"])
        source = project / "lean" / ".lake" / "packages" / item["name"]
        current = {**_check_package(packages_root / item["name"], item, patch),
                   "source_tree": _digest_tree(source)}
        if current != record:
            raise InvalidEvidenceError("dependencies", f"Closure content changed: {item['name']}")
        if patch is not None:
            # A reversible patch check alone permits extra edits in the same
            # file. Independently regenerate the expected bytes from the
            # clean pinned source, then compare the complete tree digest.
            with tempfile.TemporaryDirectory(prefix="aigc-lite-patch-audit-") as temporary:
                expected = Path(temporary) / item["name"]
                shutil.copytree(source, expected, symlinks=True)
                _git_apply(expected, "--check", str(patch))
                _git_apply(expected, str(patch))
                if _digest_tree(expected) != current["tree"]:
                    raise InvalidEvidenceError("dependencies", f"Patched bytes differ from pinned patch: {item['name']}")
        elif current["tree"] != current["source_tree"]:
            raise InvalidEvidenceError("dependencies", f"Unpatched bytes differ from source: {item['name']}")
        mountpoint = packages_root / item["name"] / ".lake"
        if mountpoint.is_symlink() or not mountpoint.is_dir() or any(mountpoint.iterdir()):
            raise InvalidEvidenceError("dependencies", "Unsafe package build mountpoint")
    if set(closure.iterdir()) != {closure / "manifest.json", packages_root}:
        raise InvalidEvidenceError("dependencies", "Unexpected closure entries")
    return {"closure_sha256": closure.name, "package_count": len(records), "patch_count": len(patches)}
