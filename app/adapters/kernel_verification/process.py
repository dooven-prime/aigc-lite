"""Bounded subprocess adapters for Lean 4 and Coq proof kernels.

The executable and every fixed argument are server configuration.  A request
can provide proof text and a declaration name, but never a command, path,
environment variable, or verifier identity.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.errors import KernelVerifierNotConfiguredError
from ...core.kernel_verification import (
    KernelBackendKind,
    KernelExecutionResult,
    KernelExecutionStatus,
    KernelVerificationDraft,
)
from ...core.qualification import canonical_hash
from ...ports.kernel_verification import KernelVerifierBackend

_DECLARATION = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*$")
_LEAN_AXIOMS = re.compile(r"^'(?P<name>[^']+)' depends on axioms: \[(?P<axioms>.*)]$")
_LEAN_CLOSED = re.compile(r"^'(?P<name>[^']+)' does not depend on any axioms$")
_COQ_AXIOM_LINE = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_'.]*)\s*:")


@dataclass(frozen=True, slots=True)
class _ProcessObservation:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    output_truncated: bool = False
    start_error: bool = False


class _ProcessKernelVerifierBackend:
    kind: KernelBackendKind
    verifier_id: str
    checker_name: str
    extension: str
    version_args: tuple[str, ...] = ("--version",)

    def __init__(
        self,
        executable: str,
        *,
        timeout_seconds: float = 30,
        max_output_bytes: int = 100_000,
        memory_mb: int = 512,
        fixed_args: tuple[str, ...] = (),
    ) -> None:
        resolved = _resolve_executable(executable)
        self._command_prefix = (str(resolved), *fixed_args)
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes
        self._memory_mb = memory_mb
        self._executable_hash = _file_hash(resolved)
        self._launcher_assets = tuple(
            (path, _file_hash(path))
            for value in fixed_args
            if (path := Path(value)).is_absolute() and path.is_file()
        )

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.kind.value,
            "verifier_id": self.verifier_id,
            "checker": self.checker_name,
            "configured": True,
            "executable_hash": self._executable_hash,
            "timeout_seconds": self._timeout_seconds,
            "max_output_bytes": self._max_output_bytes,
            "request_controls_command": False,
            "isolation": self._isolation(),
        }

    async def verify(self, draft: KernelVerificationDraft) -> KernelExecutionResult:
        if draft.backend is not self.kind:
            raise ValueError("Kernel backend does not match the request")
        if not _DECLARATION.fullmatch(draft.declaration_name):
            raise ValueError("Invalid declaration name")

        started = time.monotonic()
        dependencies = self._dependencies(draft.source)
        if not self._executable_matches_startup_hash():
            return self._result(
                status=KernelExecutionStatus.ERROR,
                version="unavailable",
                observation=_ProcessObservation(None, "", "", start_error=True),
                axioms=(),
                dependencies=dependencies,
                sorry_present=False,
                duration_ms=_elapsed_ms(started),
                command=(self.checker_name,),
                limitations=("configured executable changed after backend registration",),
            )
        with tempfile.TemporaryDirectory(prefix=f"aigc-lite-{self.kind.value}-") as directory:
            workdir = Path(directory)
            version_observation = await self._run(
                (*self._command_prefix, *self.version_args), workdir, timeout_seconds=5
            )
            version = _first_nonempty_line(
                version_observation.stdout, version_observation.stderr
            )
            if (
                version_observation.timed_out
                or version_observation.start_error
                or version_observation.exit_code != 0
                or not version
            ):
                return self._result(
                    status=KernelExecutionStatus.ERROR,
                    version=version or "unavailable",
                    observation=version_observation,
                    axioms=(),
                    dependencies=dependencies,
                    sorry_present=False,
                    duration_ms=_elapsed_ms(started),
                    command=(self.checker_name, *self.version_args),
                    limitations=("checker version probe failed",),
                )

            source_path = workdir / f"Main.{self.extension}"
            instrumented = self._instrument(draft.source, draft.declaration_name)
            source_path.write_text(instrumented, encoding="utf-8", newline="\n")
            args = self._verification_args(source_path.name)
            observation = await self._run(
                (*self._command_prefix, *args),
                workdir,
                timeout_seconds=self._timeout_seconds,
            )
            axioms, closure_observed, sorry_present = self._parse_closure(
                observation.stdout, observation.stderr, draft.declaration_name
            )
            sorry_present = sorry_present or self._source_has_placeholder(draft.source)
            if observation.timed_out:
                status = KernelExecutionStatus.TIMEOUT
            elif observation.start_error:
                status = KernelExecutionStatus.ERROR
            elif (
                observation.exit_code == 0
                and closure_observed
                and not axioms
                and not sorry_present
            ):
                status = KernelExecutionStatus.PASSED
            else:
                status = KernelExecutionStatus.FAILED
            limitations = list(self._limitations())
            if not closure_observed:
                limitations.append("axiom closure marker was not observed")
            if not self._executable_matches_startup_hash():
                status = KernelExecutionStatus.ERROR
                limitations.append("configured executable changed during verification")
            return self._result(
                status=status,
                version=version,
                observation=observation,
                axioms=axioms,
                dependencies=dependencies,
                sorry_present=sorry_present,
                duration_ms=_elapsed_ms(started),
                command=(self.checker_name, *args),
                limitations=tuple(limitations),
            )

    def _result(
        self,
        *,
        status: KernelExecutionStatus,
        version: str,
        observation: _ProcessObservation,
        axioms: tuple[str, ...],
        dependencies: tuple[str, ...],
        sorry_present: bool,
        duration_ms: int,
        command: tuple[str, ...],
        limitations: tuple[str, ...],
    ) -> KernelExecutionResult:
        toolchain_hash = canonical_hash(
            {
                "backend": self.kind.value,
                "checker_version": version,
                "executable_hash": self._executable_hash,
                "launcher_hashes": [digest for _path, digest in self._launcher_assets],
            }
        )
        return KernelExecutionResult(
            backend=self.kind,
            verifier_id=self.verifier_id,
            checker_name=self.checker_name,
            checker_version=version,
            executable_hash=self._executable_hash,
            toolchain_hash=toolchain_hash,
            status=status,
            exit_code=observation.exit_code,
            axioms=axioms,
            dependencies=dependencies,
            sorry_present=sorry_present,
            stdout=observation.stdout,
            stderr=observation.stderr,
            output_truncated=observation.output_truncated,
            duration_ms=duration_ms,
            command=command,
            isolation=self._isolation(),
            limitations=limitations,
        )

    async def _run(
        self,
        argv: tuple[str, ...],
        workdir: Path,
        *,
        timeout_seconds: float,
    ) -> _ProcessObservation:
        environment = _minimal_environment(workdir, Path(self._command_prefix[0]).parent)
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=workdir,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **kwargs,
            )
        except OSError:
            return _ProcessObservation(None, "", "", start_error=True)

        stdout_task = asyncio.create_task(
            _read_bounded(process.stdout, self._max_output_bytes)
        )
        stderr_task = asyncio.create_task(
            _read_bounded(process.stderr, self._max_output_bytes)
        )
        try:
            await asyncio.wait_for(process.wait(), timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()
            stdout, stdout_truncated = await stdout_task
            stderr, stderr_truncated = await stderr_task
            return _ProcessObservation(
                process.returncode,
                stdout,
                stderr,
                timed_out=True,
                output_truncated=stdout_truncated or stderr_truncated,
            )
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise
        stdout, stdout_truncated = await stdout_task
        stderr, stderr_truncated = await stderr_task
        return _ProcessObservation(
            process.returncode,
            stdout,
            stderr,
            output_truncated=stdout_truncated or stderr_truncated,
        )

    def _instrument(self, source: str, declaration_name: str) -> str:
        raise NotImplementedError

    def _verification_args(self, source_name: str) -> tuple[str, ...]:
        raise NotImplementedError

    def _parse_closure(
        self, stdout: str, stderr: str, declaration_name: str
    ) -> tuple[tuple[str, ...], bool, bool]:
        raise NotImplementedError

    def _source_has_placeholder(self, source: str) -> bool:
        raise NotImplementedError

    def _dependencies(self, source: str) -> tuple[str, ...]:
        raise NotImplementedError

    def _limitations(self) -> tuple[str, ...]:
        return (
            "temporary working directory is not an operating-system security sandbox",
            "network and host filesystem access are not isolated by this backend",
            "installed standard-library bytes are represented by toolchain identity, not bundled",
        )

    def _executable_matches_startup_hash(self) -> bool:
        try:
            return (
                _file_hash(Path(self._command_prefix[0])) == self._executable_hash
                and all(_file_hash(path) == digest for path, digest in self._launcher_assets)
            )
        except OSError:
            return False

    @staticmethod
    def _isolation() -> dict[str, bool | str]:
        return {
            "mode": "bounded_process",
            "shell": False,
            "temporary_workdir": True,
            "request_controls_command": False,
            "network_isolated": False,
            "host_filesystem_isolated": False,
        }


class LeanKernelVerifierBackend(_ProcessKernelVerifierBackend):
    kind = KernelBackendKind.LEAN4
    verifier_id = "lean4-kernel"
    checker_name = "lean"
    extension = "lean"

    def _instrument(self, source: str, declaration_name: str) -> str:
        return f"{source.rstrip()}\n#print axioms {declaration_name}\n"

    def _verification_args(self, source_name: str) -> tuple[str, ...]:
        return (
            "--trust=0",
            "--json",
            "--threads=1",
            f"--memory={self._memory_mb}",
            source_name,
        )

    def _parse_closure(
        self, stdout: str, stderr: str, declaration_name: str
    ) -> tuple[tuple[str, ...], bool, bool]:
        axioms: tuple[str, ...] = ()
        closure_observed = False
        sorry_present = False
        for line in (stdout + "\n" + stderr).splitlines():
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            data = str(message.get("data") or "").strip()
            if message.get("kind") == "hasSorry" or "uses `sorry`" in data:
                sorry_present = True
            closed = _LEAN_CLOSED.fullmatch(data)
            if closed and closed.group("name") == declaration_name:
                closure_observed = True
                axioms = ()
                continue
            opened = _LEAN_AXIOMS.fullmatch(data)
            if opened and opened.group("name") == declaration_name:
                closure_observed = True
                values = tuple(
                    sorted(
                        item.strip()
                        for item in opened.group("axioms").split(",")
                        if item.strip()
                    )
                )
                axioms = values
                sorry_present = sorry_present or "sorryAx" in values
        return axioms, closure_observed, sorry_present

    def _source_has_placeholder(self, source: str) -> bool:
        code = _strip_lean_comments_and_strings(source)
        return re.search(r"\b(?:sorry|admit)\b", code) is not None

    def _dependencies(self, source: str) -> tuple[str, ...]:
        code = _strip_lean_comments_and_strings(source)
        values = []
        for match in re.finditer(r"(?m)^\s*import\s+([^\r\n]+)$", code):
            values.extend(match.group(1).split())
        return tuple(sorted(set(values)))


class CoqKernelVerifierBackend(_ProcessKernelVerifierBackend):
    kind = KernelBackendKind.COQ
    verifier_id = "coq-kernel"
    checker_name = "coqc"
    extension = "v"

    def _instrument(self, source: str, declaration_name: str) -> str:
        return f"{source.rstrip()}\nPrint Assumptions {declaration_name}.\n"

    def _verification_args(self, source_name: str) -> tuple[str, ...]:
        return ("-q", source_name)

    def _parse_closure(
        self, stdout: str, stderr: str, _declaration_name: str
    ) -> tuple[tuple[str, ...], bool, bool]:
        output = stdout + "\n" + stderr
        closed = "Closed under the global context" in output
        marker = re.search(r"(?m)^Axioms:\s*$", output)
        axioms: list[str] = []
        if marker:
            for line in output[marker.end() :].splitlines():
                match = _COQ_AXIOM_LINE.match(line)
                if match:
                    axioms.append(match.group("name"))
                elif line.strip() and axioms:
                    break
            if not axioms:
                axioms.append("unparsed_axiom")
        sorry_present = self._source_has_placeholder(output)
        return tuple(sorted(set(axioms))), closed or marker is not None, sorry_present

    def _source_has_placeholder(self, source: str) -> bool:
        code = _strip_coq_comments_and_strings(source)
        return re.search(r"\b(?:Admitted|admit)\b", code) is not None

    def _dependencies(self, source: str) -> tuple[str, ...]:
        code = _strip_coq_comments_and_strings(source)
        values = []
        pattern = re.compile(
            r"(?m)^\s*(?:From\s+(?P<prefix>[A-Za-z0-9_.']+)\s+)?"
            r"Require\s+(?:Import|Export)\s+(?P<modules>[A-Za-z0-9_'. \t]+)\.\s*$"
        )
        for match in pattern.finditer(code):
            prefix = match.group("prefix")
            for module in match.group("modules").split():
                values.append(f"{prefix}.{module}" if prefix else module)
        return tuple(sorted(set(values)))


class KernelVerifierRegistry:
    """Server-owned registry; missing or invalid executables remain unavailable."""

    def __init__(self, backends: list[KernelVerifierBackend] | None = None) -> None:
        self._backends = {
            backend.kind: backend for backend in (backends or [])  # type: ignore[attr-defined]
        }

    @classmethod
    def from_config(
        cls,
        *,
        lean_executable: str,
        coq_executable: str,
        timeout_seconds: float,
        max_output_bytes: int,
        memory_mb: int,
    ) -> KernelVerifierRegistry:
        backends: list[KernelVerifierBackend] = []
        if lean_executable.strip():
            try:
                backends.append(
                    LeanKernelVerifierBackend(
                        lean_executable,
                        timeout_seconds=timeout_seconds,
                        max_output_bytes=max_output_bytes,
                        memory_mb=memory_mb,
                    )
                )
            except FileNotFoundError:
                pass
        if coq_executable.strip():
            try:
                backends.append(
                    CoqKernelVerifierBackend(
                        coq_executable,
                        timeout_seconds=timeout_seconds,
                        max_output_bytes=max_output_bytes,
                        memory_mb=memory_mb,
                    )
                )
            except FileNotFoundError:
                pass
        return cls(backends)

    def get(self, kind: KernelBackendKind) -> KernelVerifierBackend:
        backend = self._backends.get(kind)
        if backend is None:
            raise KernelVerifierNotConfiguredError(kind.value)
        return backend

    def list_backends(self) -> list[dict[str, Any]]:
        result = []
        for kind in KernelBackendKind:
            backend = self._backends.get(kind)
            result.append(
                backend.describe()
                if backend is not None
                else {
                    "backend": kind.value,
                    "configured": False,
                    "request_controls_command": False,
                }
            )
        return result


def _resolve_executable(value: str) -> Path:
    candidate = Path(value).expanduser()
    resolved_value = str(candidate.resolve()) if candidate.is_file() else shutil.which(value)
    if not resolved_value:
        raise FileNotFoundError(value)
    resolved = Path(resolved_value).resolve(strict=True)
    if not resolved.is_file():
        raise FileNotFoundError(value)
    return resolved


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _minimal_environment(workdir: Path, executable_dir: Path) -> dict[str, str]:
    allowed = (
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "USERPROFILE",
        "HOME",
        "LANG",
        "LC_ALL",
    )
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    environment.update(
        {
            "PATH": str(executable_dir),
            "TEMP": str(workdir),
            "TMP": str(workdir),
            "TMPDIR": str(workdir),
            "NO_COLOR": "1",
            "CLICOLOR": "0",
            "LEAN_PATH": "",
            "COQPATH": "",
        }
    )
    return environment


async def _read_bounded(
    stream: asyncio.StreamReader | None, maximum: int
) -> tuple[str, bool]:
    if stream is None:
        return "", False
    value = bytearray()
    truncated = False
    while chunk := await stream.read(64 * 1024):
        remaining = maximum - len(value)
        if remaining > 0:
            value.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return value.decode("utf-8", errors="replace"), truncated


def _first_nonempty_line(*values: str) -> str:
    for value in values:
        for line in value.splitlines():
            if line.strip():
                return line.strip()[:500]
    return ""


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _strip_lean_comments_and_strings(source: str) -> str:
    return _strip_language_noise(source, line_comment="--", block_open="/-", block_close="-/")


def _strip_coq_comments_and_strings(source: str) -> str:
    return _strip_language_noise(source, line_comment=None, block_open="(*", block_close="*)")


def _strip_language_noise(
    source: str,
    *,
    line_comment: str | None,
    block_open: str,
    block_close: str,
) -> str:
    result: list[str] = []
    index = 0
    block_depth = 0
    in_string = False
    while index < len(source):
        if block_depth:
            if source.startswith(block_open, index):
                block_depth += 1
                index += len(block_open)
            elif source.startswith(block_close, index):
                block_depth -= 1
                index += len(block_close)
            else:
                index += 1
            continue
        if in_string:
            if source[index] == "\\":
                index += 2
            elif source[index] == '"':
                in_string = False
                index += 1
            else:
                index += 1
            continue
        if line_comment and source.startswith(line_comment, index):
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline
            continue
        if source.startswith(block_open, index):
            block_depth = 1
            index += len(block_open)
            continue
        if source[index] == '"':
            in_string = True
            index += 1
            continue
        result.append(source[index])
        index += 1
    return "".join(result)
