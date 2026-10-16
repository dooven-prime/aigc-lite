"""Candidate-only audit of one frozen manuscript/formal-source alignment question.

This package deliberately has no dependency on the aigc-lite application or
authority services.  Its result is a source-bound research diagnosis, not a
verification, qualification, or admission receipt.
"""

from .audit import InputIdentityError, audit_sources

__all__ = ["InputIdentityError", "audit_sources"]
