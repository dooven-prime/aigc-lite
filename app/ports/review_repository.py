"""Persistence port for review profiles' runs and advisory findings."""

from __future__ import annotations

from typing import Any, Protocol


class ReviewRepository(Protocol):
    def create_review_run(
        self,
        tenant_id: str,
        values: dict[str, Any],
        findings: list[dict[str, Any]],
    ) -> dict: ...

    def get_review_run(self, tenant_id: str, review_run_id: str) -> dict | None: ...

    def list_review_runs(
        self,
        tenant_id: str,
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
        profile_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]: ...

    def get_review_finding(self, tenant_id: str, finding_id: str) -> dict | None: ...

    def list_review_findings(
        self,
        tenant_id: str,
        *,
        review_run_id: str | None = None,
        subject_id: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]: ...
