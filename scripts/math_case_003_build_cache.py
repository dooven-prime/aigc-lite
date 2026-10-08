"""Seal and audit case-003 compiler output without granting proof authority.

Cache entries are content-addressed and bound to the pinned project, patched
dependency closure, verifier executables, and sandbox policy. This first
version captures cold-build output for inspection; qualified replay never
imports or trusts a cache entry as a kernel result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.kernel_verification.math_project import _hash_file  # noqa: E402
from app.adapters.kernel_verification.math_project_closure import (  # noqa: E402
    _canonical,
    _package_manifest,
    verify_patched_closure,
)
from app.adapters.research_import.math_case_003 import PINNED_FILES, SOURCE_COMMIT  # noqa: E402


def _tree(root: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    count = size = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in dirs:
            if (base / name).is_symlink():
                raise RuntimeError("Build cache contains a symlink directory")
        dirs.sort()
        for name in sorted(files):
            path = base / name
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("Build cache contains a symlink or special file")
            relative = path.relative_to(root).as_posix()
            mode = stat.S_IMODE(path.stat().st_mode)
            file_size = path.stat().st_size
            digest.update(_canonical([relative, bool(mode & 0o111), file_size, _hash_file(path)]))
            digest.update(b"\n")
            count += 1
            size += file_size
    return {"sha256": digest.hexdigest(), "file_count": count, "byte_count": size}


def _environment(project: Path, closure: Path, binaries: dict[str, Path], policy_sha256: str) -> dict[str, object]:
    verified = verify_patched_closure(project, closure)
    if not len(policy_sha256) == 64 or any(ch not in "0123456789abcdef" for ch in policy_sha256):
        raise RuntimeError("Expected a SHA-256 sandbox policy digest")
    return {
        "source_commit": SOURCE_COMMIT,
        "source_files_sha256": hashlib.sha256(_canonical(PINNED_FILES)).hexdigest(),
        "patched_closure_sha256": verified["closure_sha256"],
        "sandbox_policy_sha256": policy_sha256,
        "binaries": {name: _hash_file(path.resolve(strict=True)) for name, path in sorted(binaries.items())},
    }


def prepare_work(project: Path, target: Path) -> None:
    project = project.resolve(strict=True)
    target = target.absolute()
    if (target.exists() or target.is_symlink() or target.is_relative_to(project)
            or project.is_relative_to(target)):
        raise RuntimeError("Build work target must be a new external directory")
    names = [item["name"] for item in _package_manifest(project)]
    (target / "project").mkdir(parents=True)
    for name in names:
        (target / "project" / "packages" / name).mkdir(parents=True, exist_ok=True)
        (target / "packages" / name).mkdir(parents=True)


def _outputs(project: Path, root: Path) -> dict[str, object]:
    names = [item["name"] for item in _package_manifest(project)]
    if (root / "project").is_symlink() or (root / "packages").is_symlink():
        raise RuntimeError("Build work paths cannot be symlinks")
    if any((root / "packages" / name).is_symlink() or
           not (root / "packages" / name).is_dir() for name in names):
        raise RuntimeError("Build work package path is not a directory")
    if {entry.name for entry in (root / "packages").iterdir()} != set(names):
        raise RuntimeError("Build work package set differs from pinned Lake manifest")
    return {
        "project": _tree(root / "project"),
        "packages": {name: _tree(root / "packages" / name) for name in names},
    }


def capture(
    project: Path, closure: Path, work: Path, store: Path,
    binaries: dict[str, Path], policy_sha256: str,
) -> Path:
    if os.name != "posix":
        raise RuntimeError("Build cache sealing requires POSIX")
    project = project.resolve(strict=True)
    work = work.resolve(strict=True)
    store = store.absolute()
    if (work.is_relative_to(project) or project.is_relative_to(work)
            or store.is_relative_to(work) or store.is_relative_to(project)):
        raise RuntimeError("Build work, source, and cache store must be separate")
    if store.is_symlink() or work.is_symlink() or (work / "manifest.json").exists():
        raise RuntimeError("Unsafe or previously sealed build work")
    manifest = {
        "contract_version": "math-project.build-cache.v1",
        "environment": _environment(project, closure, binaries, policy_sha256),
        "outputs": _outputs(project, work),
        "qualification_use": "forbidden",
    }
    identity = hashlib.sha256(_canonical(manifest)).hexdigest()
    store.mkdir(parents=True, exist_ok=True)
    destination = store / identity
    if destination.exists() or destination.is_symlink():
        raise RuntimeError("Cache address exists; verify it instead of replacing it")
    (work / "manifest.json").write_bytes(_canonical(manifest) + b"\n")
    for path in sorted(work.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_file() or path.is_dir():
            path.chmod(path.stat().st_mode & ~0o222)
    work.rename(destination)
    return destination


def verify(
    project: Path, closure: Path, entry: Path,
    binaries: dict[str, Path], policy_sha256: str,
) -> dict[str, object]:
    project = project.resolve(strict=True)
    entry = entry.resolve(strict=True)
    raw = (entry / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    if raw != _canonical(manifest) + b"\n" or hashlib.sha256(_canonical(manifest)).hexdigest() != entry.name:
        raise RuntimeError("Build cache address or manifest differs")
    if manifest.get("contract_version") != "math-project.build-cache.v1" or manifest.get("qualification_use") != "forbidden":
        raise RuntimeError("Unsupported build cache contract")
    if manifest.get("environment") != _environment(project, closure, binaries, policy_sha256):
        raise RuntimeError("Build cache environment changed")
    if manifest.get("outputs") != _outputs(project, entry):
        raise RuntimeError("Build cache bytes changed")
    if {path.name for path in entry.iterdir()} != {"manifest.json", "project", "packages"}:
        raise RuntimeError("Unexpected build cache entry")
    return {"cache_sha256": entry.name, "file_count": sum(
        item["file_count"] for item in [manifest["outputs"]["project"], *manifest["outputs"]["packages"].values()]
    ), "qualification_use": "forbidden"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "capture", "verify"))
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--entry", type=Path)
    parser.add_argument("--closure", type=Path)
    parser.add_argument("--lake", type=Path)
    parser.add_argument("--lean", type=Path)
    parser.add_argument("--comparator", type=Path)
    parser.add_argument("--sandbox-policy-sha256")
    args = parser.parse_args()
    if args.action == "prepare":
        if not args.work:
            parser.error("prepare requires --work")
        prepare_work(args.project, args.work)
        print(json.dumps({"build_work_root": str(args.work.absolute())}))
        return 0
    required = ("closure", "lake", "lean", "comparator", "sandbox_policy_sha256")
    if any(getattr(args, name) is None for name in required):
        parser.error("capture/verify require closure, lake, lean, comparator and sandbox policy hash")
    binaries = {name: getattr(args, name) for name in ("lake", "lean", "comparator")}
    if args.action == "capture":
        if not args.work or not args.store:
            parser.error("capture requires --work and --store")
        result = capture(args.project, args.closure, args.work, args.store, binaries, args.sandbox_policy_sha256)
        print(json.dumps({"cache_entry": str(result)}))
    else:
        if not args.entry:
            parser.error("verify requires --entry")
        print(json.dumps(verify(args.project, args.closure, args.entry, binaries, args.sandbox_policy_sha256)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
