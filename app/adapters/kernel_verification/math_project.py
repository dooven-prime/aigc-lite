"""Bounded, deployer-configured replay of the pinned case-003 Lean project.

The entire Lake invocation must be placed behind a deployer-owned sandbox
wrapper. Without one, this adapter refuses to execute untrusted project code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from ...adapters.research_import.math_case_003 import (
    CONFIG_PATH,
    PINNED_FILES,
    SOURCE_COMMIT,
)
from ...core.errors import InvalidEvidenceError

_SHA = re.compile(r"^[0-9a-f]{40}$")
_MATHLIB_REV = "d13f23b723b8a846827a245b89c10fc7d3f11612"


@dataclass(frozen=True, slots=True)
class ProjectReplayObservation:
    worktree_clean: bool
    dependencies_closed: bool
    mathlib_rev: str | None
    clean_build_exit_code: int | None
    kernel_exit_code: int | None
    comparator_exit_code: int | None
    lean_executable_hash: str
    lake_executable_hash: str
    lean_version: str
    lake_version: str
    kernel_stdout_hash: str
    kernel_stderr_hash: str
    comparator_stdout_hash: str
    comparator_stderr_hash: str
    executable_hash: str
    sandbox_hash: str
    command: tuple[str, ...]
    patch_closure_verified: bool = False
    patched_closure_sha256: str | None = None
    limitations: tuple[str, ...] = ()


class MathProjectComparatorBackend:
    """Use fixed commands and a required external sandbox; no client argv."""

    def __init__(
        self,
        project_root: Path,
        *,
        lake_executable: Path,
        lean_executable: Path,
        comparator_executable: Path,
        sandbox_executable: Path,
        sandbox_fixed_args: tuple[str, ...] = (),
        patched_closure_root: Path | None = None,
        timeout_seconds: float = 3600,
    ) -> None:
        if os.name != "posix":
            raise InvalidEvidenceError(
                "sandbox", "Untrusted Lean project replay requires a Linux sandbox"
            )
        self.project_root = project_root.resolve(strict=True)
        self.lake = lake_executable.resolve(strict=True)
        self.lean = lean_executable.resolve(strict=True)
        self.comparator = comparator_executable.resolve(strict=True)
        self.sandbox = sandbox_executable.resolve(strict=True)
        if not all(path.is_file() for path in (self.lake, self.lean, self.comparator, self.sandbox)):
            raise InvalidEvidenceError("verifier", "Verifier executables must be files")
        if self.lake.parent != self.lean.parent:
            raise InvalidEvidenceError("verifier", "Lean and Lake must come from one pinned toolchain")
        if not sandbox_fixed_args or sandbox_fixed_args[-1] != "--":
            raise InvalidEvidenceError(
                "sandbox", "A deployer-owned sandbox wrapper ending in -- is required"
            )
        self.sandbox_fixed_args = sandbox_fixed_args
        self.patched_closure_root = patched_closure_root
        self.timeout_seconds = timeout_seconds
        self._hashes = {
            "lake": _hash_file(self.lake),
            "lean": _hash_file(self.lean),
            "comparator": _hash_file(self.comparator),
            "sandbox": _hash_file(self.sandbox),
        }
        self._sandbox_policy_hash = hashlib.sha256(
            json.dumps(
                {
                    "executable_sha256": self._hashes["sandbox"],
                    "fixed_args": self.sandbox_fixed_args,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    async def verify(self) -> ProjectReplayObservation:
        root = self.project_root
        lean_root = root / "lean"
        if _git(root, "rev-parse", "HEAD") != SOURCE_COMMIT:
            raise InvalidEvidenceError("source", "Project checkout is not the pinned commit")
        if _git(root, "status", "--porcelain", "--untracked-files=all"):
            raise InvalidEvidenceError("source", "Project checkout is not clean")
        for path, expected in PINNED_FILES.items():
            target = (root / path).resolve(strict=True)
            if not target.is_relative_to(root) or _hash_file(target) != expected:
                raise InvalidEvidenceError("source", f"Pinned project file changed: {path}")
        dependency_state = _check_dependencies(lean_root)
        if not dependency_state[0]:
            raise InvalidEvidenceError("dependencies", "Pinned Lake dependencies are not closed")
        closure_identity = None
        if self.patched_closure_root is not None:
            from .math_project_closure import verify_patched_closure

            closure = self.patched_closure_root.resolve(strict=True)
            closure_identity = verify_patched_closure(root, closure)["closure_sha256"]
            for item in json.loads((lean_root / "lake-manifest.json").read_text())["packages"]:
                name = item["name"].strip("«»")
                expected = ("--ro-bind", str(closure / "packages" / name),
                            str(lean_root / ".lake" / "packages" / name))
                if not any(self.sandbox_fixed_args[i:i + 3] == expected
                           for i in range(len(self.sandbox_fixed_args) - 2)):
                    raise InvalidEvidenceError("sandbox", "Patched closure is not mounted read-only")
        if any(lean_root.rglob("*.olean")):
            raise InvalidEvidenceError(
                "dependencies", "Pre-existing compiled Lean artifacts are not allowed"
            )
        if any(
            _hash_file(path) != self._hashes[name]
            for name, path in (
                ("lake", self.lake),
                ("lean", self.lean),
                ("comparator", self.comparator),
                ("sandbox", self.sandbox),
            )
        ):
            raise InvalidEvidenceError("verifier", "Verifier executable changed after registration")

        # Sandbox syntax is an operator contract. The wrapper must confine the
        # entire Lake process, not merely Comparator's solution subprocess.
        prefix = (str(self.sandbox), *self.sandbox_fixed_args)
        lean_probe = await _run(prefix + (str(self.lean), "--version"), lean_root, 30)
        lake_probe = await _run(prefix + (str(self.lake), "--version"), lean_root, 30)
        lean_version = lean_probe[1].decode("utf-8", errors="replace").strip()
        lake_version = lake_probe[1].decode("utf-8", errors="replace").strip()
        if (
            lean_probe[0] != 0 or lake_probe[0] != 0
            or "4.34.1" not in lean_version or "4.34.1" not in lake_version
        ):
            raise InvalidEvidenceError("toolchain", "Pinned Lean/Lake 4.34.1 is required")
        # Comparator must build/export the challenge before it ever touches the
        # untrusted solution. Prebuilding the solution here would invalidate
        # that ordering and could poison the challenge's build environment.
        comparator_command = (
            str(self.lake),
            "env",
            str(self.comparator),
            CONFIG_PATH.removeprefix("lean/"),
        )
        comparator = await _run(prefix + comparator_command, lean_root, self.timeout_seconds)
        # A successful Comparator run entails its challenge-first builds,
        # statement comparison, axiom check, and built-in kernel replay. A
        # failure does not reveal which of those stages passed, so keep the
        # individual build/kernel statuses undetermined rather than guessing.
        comparator_passed = comparator[0] == 0
        clean_after = not _git(root, "status", "--porcelain", "--untracked-files=all")
        clean_after = clean_after and all(
            _hash_file((root / path).resolve(strict=True)) == expected
            for path, expected in PINNED_FILES.items()
        )
        dependencies_after = _check_dependencies(lean_root)
        if self.patched_closure_root is not None:
            from .math_project_closure import verify_patched_closure

            verify_patched_closure(root, self.patched_closure_root)
        if any(
            _hash_file(path) != self._hashes[name]
            for name, path in (
                ("lake", self.lake), ("lean", self.lean),
                ("comparator", self.comparator), ("sandbox", self.sandbox),
            )
        ):
            raise InvalidEvidenceError("verifier", "Verifier executable changed during replay")
        limitations = [
            "Build and kernel success are entailed by Comparator success; no separate "
            "solution prebuild or second kernel process was run.",
            "A configured sandbox wrapper is treated as an operator-owned trust boundary; "
            "its binary and fixed arguments are hashed, but its actual isolation policy "
            "requires deployment audit.",
            "Git commit bytes and signatures were not independently authenticated.",
        ]
        if comparator[0] is None:
            limitations.append("Comparator exceeded its wall-clock limit and was terminated.")
        return ProjectReplayObservation(
            worktree_clean=clean_after,
            dependencies_closed=dependencies_after[0],
            mathlib_rev=dependency_state[1],
            clean_build_exit_code=0 if comparator_passed else None,
            kernel_exit_code=0 if comparator_passed else None,
            comparator_exit_code=comparator[0],
            lean_executable_hash=self._hashes["lean"],
            lake_executable_hash=self._hashes["lake"],
            lean_version=lean_version,
            lake_version=lake_version,
            kernel_stdout_hash=hashlib.sha256(b"").hexdigest(),
            kernel_stderr_hash=hashlib.sha256(b"").hexdigest(),
            comparator_stdout_hash=hashlib.sha256(comparator[1]).hexdigest(),
            comparator_stderr_hash=hashlib.sha256(comparator[2]).hexdigest(),
            executable_hash=self._hashes["comparator"],
            sandbox_hash=self._sandbox_policy_hash,
            command=("lake", "env", "comparator", CONFIG_PATH.removeprefix("lean/")),
            patch_closure_verified=closure_identity is not None,
            patched_closure_sha256=closure_identity,
            limitations=tuple(limitations),
        )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(root), *args),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise InvalidEvidenceError("source", "Pinned Git checkout cannot be inspected")
    return result.stdout.strip()


def _check_dependencies(lean_root: Path) -> tuple[bool, str | None]:
    manifest = json.loads((lean_root / "lake-manifest.json").read_text(encoding="utf-8"))
    packages_dir = manifest.get("packagesDir")
    packages = manifest.get("packages")
    if packages_dir != ".lake/packages" or not isinstance(packages, list) or not packages:
        return False, None
    lake_dir = lean_root / ".lake"
    package_base = lake_dir / "packages"
    if lake_dir.is_symlink() or package_base.is_symlink() or not package_base.is_dir():
        return False, None
    mathlib_rev = None
    seen = set()
    for package in packages:
        if not isinstance(package, dict) or package.get("type") != "git":
            return False, None
        name, rev = package.get("name"), package.get("rev")
        if not isinstance(name, str) or not isinstance(rev, str) or not _SHA.fullmatch(rev):
            return False, None
        normalized_name = name.strip("«»")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", normalized_name) or normalized_name in seen:
            return False, None
        seen.add(normalized_name)
        package_root = package_base / normalized_name
        if (
            package_root.is_symlink()
            or not package_root.is_dir()
            or _git(package_root, "rev-parse", "HEAD") != rev
        ):
            return False, None
        if _git(package_root, "status", "--porcelain", "--untracked-files=all"):
            return False, None
        if normalized_name == "mathlib":
            mathlib_rev = rev
    return mathlib_rev == _MATHLIB_REV, mathlib_rev


async def _run(
    command: tuple[str, ...], cwd: Path, timeout: float
) -> tuple[int | None, bytes, bytes]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"HOME", "USERPROFILE", "PATH", "SystemRoot", "WINDIR", "LANG", "LC_ALL"}
    }
    environment.update({"NO_COLOR": "1", "CLICOLOR": "0"})
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=environment,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_task = asyncio.create_task(_read_bounded(process.stdout))
    stderr_task = asyncio.create_task(_read_bounded(process.stderr))
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout)
    except TimeoutError:
        timed_out = True
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
    except asyncio.CancelledError:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise
    stdout, stdout_truncated = await stdout_task
    stderr, stderr_truncated = await stderr_task
    if stdout_truncated or stderr_truncated:
        raise InvalidEvidenceError("verifier", "Comparator output exceeded the bounded limit")
    return None if timed_out else process.returncode, stdout, stderr


async def _read_bounded(stream: asyncio.StreamReader | None) -> tuple[bytes, bool]:
    if stream is None:
        return b"", False
    # Lake may print one progress line per source module across Mathlib and the
    # pinned project. Keep a finite ceiling without treating normal build logs
    # as a verifier infrastructure failure.
    limit = 16_000_000
    content = bytearray()
    truncated = False
    while chunk := await stream.read(64 * 1024):
        available = limit - len(content)
        if available > 0:
            content.extend(chunk[:available])
        truncated = truncated or len(chunk) > available
    return bytes(content), truncated
