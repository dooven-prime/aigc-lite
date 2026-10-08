"""Copy pinned Lake checkouts into a disposable per-replay dependency scratch.

The upstream Lakefile applies compatibility patches during configuration. It
must never receive write access to the original pinned dependency checkouts.
This script does not delete or reuse an existing scratch directory.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.kernel_verification.math_project import _check_dependencies  # noqa: E402
from scripts.prepare_math_case_003_dependencies import _validate_source  # noqa: E402


def prepare(project: Path, scratch: Path) -> None:
    project = project.resolve(strict=True)
    if scratch.exists() or scratch.is_symlink():
        raise RuntimeError("Scratch destination already exists; use a new path for each replay")
    scratch = scratch.absolute()
    if scratch.is_relative_to(project) or project.is_relative_to(scratch):
        raise RuntimeError("Scratch must be outside the pinned source checkout")
    packages = _validate_source(project)
    if not _check_dependencies(project / "lean")[0]:
        raise RuntimeError("Original Lake dependency closure is not clean and pinned")
    scratch.mkdir(parents=True)
    for item in packages:
        name = item["name"]
        source = project / "lean" / ".lake" / "packages" / name
        target = scratch / name
        if source.is_symlink():
            raise RuntimeError(f"Refusing symlink package: {name}")
        shutil.copytree(source, target, symlinks=True)
        result = subprocess.run(
            ("git", "-C", str(target), "rev-parse", "HEAD"),
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode or result.stdout.strip() != item["rev"]:
            raise RuntimeError(f"Scratch package revision mismatch: {name}")
        print(f"copied: {name}", flush=True)
    print(json.dumps({"scratch_root": str(scratch), "package_count": len(packages)}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", type=Path)
    parser.add_argument("scratch_root", type=Path)
    args = parser.parse_args()
    prepare(args.project_root, args.scratch_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
