"""Contracts for server-owned formal proof kernel execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

KERNEL_EXECUTION_CONTRACT_VERSION = "kernel.execution.v1"


class KernelBackendKind(StrEnum):
    LEAN4 = "lean4"
    COQ = "coq"


class KernelExecutionStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class KernelVerificationDraft:
    """One frozen, single-file proof submitted to a configured kernel backend."""

    backend: KernelBackendKind
    declaration_name: str
    source: str


@dataclass(frozen=True, slots=True)
class KernelExecutionResult:
    """Bounded observation returned by a kernel process adapter."""

    backend: KernelBackendKind
    verifier_id: str
    checker_name: str
    checker_version: str
    executable_hash: str
    toolchain_hash: str
    status: KernelExecutionStatus
    exit_code: int | None
    axioms: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    sorry_present: bool = False
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    duration_ms: int = 0
    command: tuple[str, ...] = ()
    isolation: dict[str, Any] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()
