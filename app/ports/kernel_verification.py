"""Provider boundary for formal proof kernel backends."""

from __future__ import annotations

from typing import Protocol

from ..core.kernel_verification import (
    KernelBackendKind,
    KernelExecutionResult,
    KernelVerificationDraft,
)


class KernelVerifierBackend(Protocol):
    kind: KernelBackendKind
    verifier_id: str

    def describe(self) -> dict: ...

    async def verify(self, draft: KernelVerificationDraft) -> KernelExecutionResult: ...


class KernelVerifierSource(Protocol):
    """Resolve only server-registered kernel backends."""

    def get(self, kind: KernelBackendKind) -> KernelVerifierBackend: ...

    def list_backends(self) -> list[dict]: ...
