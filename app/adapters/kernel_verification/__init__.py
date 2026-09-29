"""Configured formal proof kernel process adapters."""

from .process import (
    CoqKernelVerifierBackend,
    KernelVerifierRegistry,
    LeanKernelVerifierBackend,
)

__all__ = [
    "CoqKernelVerifierBackend",
    "KernelVerifierRegistry",
    "LeanKernelVerifierBackend",
]
