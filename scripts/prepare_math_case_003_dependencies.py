"""Fetch pinned Lake sources without executing the upstream Lake project.

This is a preparation step, not a verifier. It never builds packages, deletes
an existing checkout, or grants qualification. Run it outside the replay
sandbox; the later replay must recheck every revision and a clean worktree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.research_import.math_case_003 import (  # noqa: E402
    MANIFEST_PATH,
    PINNED_FILES,
    SOURCE_COMMIT,
)

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_NAME = re.compile(r"[A-Za-z0-9_.-]+\Z")
_GITHUB_PATH = re.compile(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?\Z")


def _git(*args: str, timeout: int = 180) -> str:
    result = subprocess.run(
        ("git", *args),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()[:500]}")
    return result.stdout.strip()


def _validate_source(project: Path) -> list[dict[str, str]]:
    if _git("-C", str(project), "rev-parse", "HEAD") != SOURCE_COMMIT:
        raise RuntimeError("Source checkout is not the pinned commit")
    if _git("-C", str(project), "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("Source checkout is not clean")
    for relative, expected in PINNED_FILES.items():
        target = (project / relative).resolve(strict=True)
        if not target.is_relative_to(project) or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Pinned source bytes changed: {relative}")

    manifest = json.loads((project / MANIFEST_PATH).read_text(encoding="utf-8"))
    if manifest.get("packagesDir") != ".lake/packages":
        raise RuntimeError("Unexpected Lake packages directory")
    packages = manifest.get("packages")
    if not isinstance(packages, list) or len(packages) != 42:
        raise RuntimeError("Expected exactly 42 pinned Lake packages")
    validated = []
    seen = set()
    for package in packages:
        if not isinstance(package, dict) or package.get("type") != "git":
            raise RuntimeError("Non-Git Lake package is outside this preparation policy")
        name, rev, url = package.get("name"), package.get("rev"), package.get("url")
        if not all(isinstance(value, str) for value in (name, rev, url)):
            raise RuntimeError("Incomplete Lake package identity")
        name = name.strip("«»")
        parsed = urlsplit(url)
        if (
            not _NAME.fullmatch(name)
            or name in seen
            or not _SHA.fullmatch(rev)
            or parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or not _GITHUB_PATH.fullmatch(parsed.path)
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(f"Invalid Lake package identity: {name}")
        seen.add(name)
        validated.append({"name": name, "rev": rev, "url": url})
    return validated


def _fetch(package: dict[str, str], packages_dir: Path, timeout: int) -> str:
    name, rev, url = package["name"], package["rev"], package["url"]
    target = packages_dir / name
    if target.is_symlink():
        raise RuntimeError(f"Package checkout must not be a symlink: {target}")
    if target.exists():
        if not (target / ".git").is_dir():
            raise RuntimeError(f"Existing path is not a Git checkout: {target}")
        if _git("-C", str(target), "rev-parse", "HEAD") != rev:
            raise RuntimeError(f"Existing checkout has the wrong revision: {target}")
        if _git("-C", str(target), "status", "--porcelain", "--untracked-files=all"):
            raise RuntimeError(f"Existing checkout is dirty: {target}")
        _prepare_empty_build_mount(target)
        return "already-present"

    _git("clone", "--filter=blob:none", "--depth", "1", "--no-checkout", url, str(target), timeout=timeout)
    _git("-C", str(target), "fetch", "--depth", "1", "origin", rev, timeout=timeout)
    _git("-C", str(target), "checkout", "--detach", rev, timeout=timeout)
    if _git("-C", str(target), "rev-parse", "HEAD") != rev:
        raise RuntimeError(f"Fetched checkout has the wrong revision: {target}")
    if _git("-C", str(target), "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError(f"Fetched checkout is dirty: {target}")
    _prepare_empty_build_mount(target)
    return "fetched"


def _prepare_empty_build_mount(target: Path) -> None:
    """A per-package tmpfs will cover this empty mountpoint during replay."""

    mountpoint = target / ".lake"
    if mountpoint.is_symlink() or (mountpoint.exists() and not mountpoint.is_dir()):
        raise RuntimeError(f"Unsafe package build mountpoint: {mountpoint}")
    mountpoint.mkdir(exist_ok=True)
    if any(mountpoint.iterdir()):
        raise RuntimeError(f"Package build mountpoint is not empty: {mountpoint}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--package", action="append", default=[], help="repeat to limit the fetch")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--max-total-seconds", type=int, default=900)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    project = args.project_root.resolve(strict=True)
    packages = _validate_source(project)
    if args.package:
        selected = set(args.package)
        known = {item["name"] for item in packages}
        if selected - known:
            raise RuntimeError(f"Unknown packages: {sorted(selected - known)}")
        packages = [item for item in packages if item["name"] in selected]
    packages_dir = project / "lean" / ".lake" / "packages"
    if args.dry_run:
        print(json.dumps(packages, indent=2))
        return 0
    packages_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    for package in packages:
        if time.monotonic() - started >= args.max_total_seconds:
            raise RuntimeError("Dependency preparation exceeded the total time budget")
        state = _fetch(package, packages_dir, args.timeout_seconds)
        print(f"{state}: {package['name']} @ {package['rev']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
