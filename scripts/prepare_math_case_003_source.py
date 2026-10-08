"""Create or validate the fixed, sparse OpenAI math case-003 checkout.

This only acquires candidate source bytes. A pinned hash and Git commit do not
authenticate an unsigned upstream release or qualify its mathematical claim.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.research_import.math_case_003 import (  # noqa: E402
    PINNED_FILES,
    SOURCE_COMMIT,
)
from scripts.prepare_math_case_003_dependencies import _validate_source  # noqa: E402

SOURCE_URL = "https://github.com/openai/math.git"
SPARSE_PATHS = tuple(f"/{path}" for path in PINNED_FILES) + (
    "/lean/OAI/NumberTheory/DirichletL/",
    "/lean/patches/",
)


def _git(*args: str, timeout: int = 300) -> str:
    result = subprocess.run(
        ("git", *args), capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(f"Git source preparation failed: {result.stderr.strip()[:500]}")
    return result.stdout.strip()


def prepare(target: Path) -> None:
    if target.is_symlink():
        raise RuntimeError("Source checkout must not be a symlink")
    if not target.exists():
        # Sparse before checkout: the full Lean tree contains over 100k paths.
        _git(
            "clone", "--filter=blob:none", "--sparse", "--depth", "1",
            "--no-checkout", SOURCE_URL, str(target), timeout=600,
        )
        _git("-C", str(target), "sparse-checkout", "set", "--no-cone", *SPARSE_PATHS)
        _git("-C", str(target), "fetch", "--depth", "1", "origin", SOURCE_COMMIT, timeout=600)
        _git("-C", str(target), "checkout", "--detach", SOURCE_COMMIT, timeout=600)
    elif not target.is_dir():
        raise RuntimeError("Source target is not a directory")
    if _git("-C", str(target), "remote", "get-url", "origin") != SOURCE_URL:
        raise RuntimeError("Source origin is not the fixed public repository")
    if _git("-C", str(target), "rev-parse", "HEAD") != SOURCE_COMMIT:
        raise RuntimeError("Source checkout is not the pinned commit")
    if _git("-C", str(target), "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("Source checkout is dirty")
    _git("-C", str(target), "sparse-checkout", "set", "--no-cone", *SPARSE_PATHS)
    _validate_source(target)
    _git("-C", str(target), "fsck", "--full", "--no-progress", timeout=600)
    print(f"validated candidate source: {target} @ {SOURCE_COMMIT}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    prepare(args.target.absolute())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
