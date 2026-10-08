"""An explicit Bubblewrap policy for the pinned math-project replay.

This constructs operator-side arguments; it does not certify the host kernel,
Bubblewrap implementation, or Comparator. The replay backend hashes the
executable and every fixed argument into its receipt.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ...core.errors import InvalidEvidenceError
from .math_project import _check_dependencies, _git

_NAME = re.compile(r"[A-Za-z0-9_.-]+\Z")


def bubblewrap_math_project_args(
    project_root: Path,
    *,
    lake_executable: Path,
    lean_executable: Path,
    comparator_root: Path,
    comparator_executable: Path,
    landrun_executable: Path,
    lean4export_executable: Path,
    dependency_scratch_root: Path | None = None,
    patched_closure_root: Path | None = None,
    build_work_root: Path | None = None,
) -> tuple[str, ...]:
    """Mount pinned inputs; prefer audited read-only patched dependencies."""

    root = project_root.resolve(strict=True)
    lean_root = root / "lean"
    lake = lake_executable.resolve(strict=True)
    lean = lean_executable.resolve(strict=True)
    comparator_home = comparator_root.resolve(strict=True)
    comparator = comparator_executable.resolve(strict=True)
    exporter = lean4export_executable.resolve(strict=True)
    landrun = landrun_executable.resolve(strict=True)
    if lake.parent != lean.parent or not comparator.is_relative_to(comparator_home):
        raise InvalidEvidenceError("sandbox", "Verifier tool paths do not share their pinned roots")
    if not exporter.is_relative_to(comparator_home):
        raise InvalidEvidenceError("sandbox", "lean4export is outside the Comparator checkout")
    if not _check_dependencies(lean_root)[0]:
        raise InvalidEvidenceError("dependencies", "Pinned Lake dependencies are not closed")
    manifest = json.loads((lean_root / "lake-manifest.json").read_text(encoding="utf-8"))
    packages = manifest["packages"]
    if dependency_scratch_root is not None and patched_closure_root is not None:
        raise InvalidEvidenceError("sandbox", "Scratch and patched closure are mutually exclusive")
    scratch = dependency_scratch_root.resolve(strict=True) if dependency_scratch_root else None
    closure = patched_closure_root.resolve(strict=True) if patched_closure_root else None
    build_work = build_work_root.resolve(strict=True) if build_work_root else None
    if build_work is not None and (
        build_work_root.is_symlink() or build_work.is_relative_to(root)
        or root.is_relative_to(build_work)
        or (closure is not None and build_work.is_relative_to(closure))
    ):
        raise InvalidEvidenceError("sandbox", "Build work root must be separate")
    if closure is not None:
        from .math_project_closure import verify_patched_closure

        verify_patched_closure(root, closure)
    if scratch is not None and (
        dependency_scratch_root.is_symlink()
        or scratch.is_relative_to(root)
        or root.is_relative_to(scratch)
    ):
        raise InvalidEvidenceError("sandbox", "Dependency scratch must be external and non-symlinked")
    package_roots: list[Path] = []
    scratch_roots: list[Path] = []
    closure_roots: list[Path] = []
    for item in packages:
        name = item["name"].strip("«»")
        if not _NAME.fullmatch(name):
            raise InvalidEvidenceError("sandbox", "Unsafe Lake package name")
        package = lean_root / ".lake" / "packages" / name
        build_mount = package / ".lake"
        if build_mount.is_symlink() or not build_mount.is_dir() or any(build_mount.iterdir()):
            raise InvalidEvidenceError("sandbox", f"Package build mountpoint is not empty: {name}")
        package_roots.append(package)
        if closure is not None:
            closure_roots.append(closure / "packages" / name)
        if scratch is not None:
            copied = scratch / name
            if (
                copied.is_symlink()
                or not copied.is_dir()
                or not (copied / ".git").is_dir()
                or _git(copied, "rev-parse", "HEAD") != item["rev"]
                or _git(copied, "status", "--porcelain", "--untracked-files=all")
            ):
                raise InvalidEvidenceError("sandbox", f"Scratch package is not clean and pinned: {name}")
            copied_mount = copied / ".lake"
            if copied_mount.is_symlink() or not copied_mount.is_dir() or any(copied_mount.iterdir()):
                raise InvalidEvidenceError("sandbox", f"Scratch build mountpoint is not empty: {name}")
            scratch_roots.append(copied)
    if scratch is not None and {path.name for path in scratch.iterdir()} != {
        path.name for path in scratch_roots
    }:
        raise InvalidEvidenceError("sandbox", "Scratch contains unexpected dependency paths")

    toolchain = lean.parent.parent
    args = [
        "--unshare-user", "--unshare-pid", "--unshare-net", "--unshare-ipc",
        "--unshare-uts", "--disable-userns", "--die-with-parent", "--new-session",
        "--clearenv",
    ]
    for system_path in ("/usr", "/lib", "/lib64", "/bin", "/etc/alternatives"):
        args.extend(("--ro-bind", system_path, system_path))
    for trusted_path in (toolchain, comparator_home, landrun.parent, root):
        args.extend(("--ro-bind", str(trusted_path), str(trusted_path)))
    lake_dir = lean_root / ".lake"
    packages_dir = lake_dir / "packages"
    args.extend(("--tmpfs", str(lake_dir), "--dir", str(packages_dir)))
    if build_work is not None:
        project_build = build_work / "project"
        if project_build.is_symlink() or not project_build.is_dir():
            raise InvalidEvidenceError("sandbox", "Project build work directory must be empty")
        entries = list(project_build.iterdir())
        if entries and (
            len(entries) != 1 or entries[0].name != "packages"
            or entries[0].is_symlink() or not entries[0].is_dir()
            or {path.name for path in entries[0].iterdir()} != {path.name for path in package_roots}
            or any(path.is_symlink() or not path.is_dir() or any(path.iterdir())
                   for path in entries[0].iterdir())
        ):
            raise InvalidEvidenceError("sandbox", "Project build work directory must be empty")
        args[-4:] = ["--bind", str(project_build), str(lake_dir), "--dir", str(packages_dir)]
    for index, package in enumerate(package_roots):
        if closure is not None:
            args.extend(("--ro-bind", str(closure_roots[index]), str(package)))
        elif scratch is None:
            args.extend(("--ro-bind", str(package), str(package)))
        else:
            args.extend(("--bind", str(scratch_roots[index]), str(package)))
        if build_work is None:
            args.extend(("--tmpfs", str(package / ".lake")))
        else:
            package_build = build_work / "packages" / package.name
            if (package_build.is_symlink() or not package_build.is_dir()
                    or any(package_build.iterdir())):
                raise InvalidEvidenceError("sandbox", f"Package build work directory must be empty: {package.name}")
            args.extend(("--bind", str(package_build), str(package / ".lake")))
    args.extend((
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--setenv", "PATH", f"{lean.parent}:{landrun.parent}:/usr/bin:/bin",
        "--setenv", "HOME", "/tmp",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "COMPARATOR_LANDRUN", str(landrun),
        "--setenv", "COMPARATOR_LEAN4EXPORT", str(exporter),
        "--chdir", str(lean_root),
        "--",
    ))
    return tuple(args)
