"""Deterministic review execution and tenant-scoped finding queries."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any, Protocol
from uuid import uuid4

from ..core.contracts import RequestContext
from ..core.errors import ResourceNotFoundError
from ..core.qualification import canonical_hash
from ..core.review import (
    REVIEWER_LINEAGE_VERSION,
    ReviewFindingDraft,
    ReviewFindingOrigin,
    ReviewFindingStatus,
    ReviewProfile,
)
from ..database import get_repository
from ..profiles.execution_integrity import ExecutionIntegrityReviewer
from ..repository import Repository

RepositoryProvider = Callable[[], Repository]


class Reviewer(Protocol):
    profile: ReviewProfile

    def evaluate(self, snapshot: dict[str, Any]) -> tuple[ReviewFindingDraft, ...]: ...


class ReviewProfileRegistry:
    """Immutable-by-profile-id registry of host-owned review evaluators."""

    def __init__(self, reviewers: list[Reviewer] | None = None) -> None:
        self._reviewers: dict[str, Reviewer] = {}
        for reviewer in reviewers or []:
            self.register(reviewer)

    def register(self, reviewer: Reviewer) -> None:
        profile_id = reviewer.profile.profile_id
        if profile_id in self._reviewers:
            raise ValueError(f"Review profile already registered: {profile_id}")
        self._reviewers[profile_id] = reviewer

    def get(self, profile_id: str) -> Reviewer:
        try:
            return self._reviewers[profile_id]
        except KeyError as exc:
            raise ResourceNotFoundError("review_profile", profile_id) from exc

    def list_profiles(self) -> list[dict[str, Any]]:
        return [
            reviewer.profile.as_dict()
            | {"profile_hash": reviewer.profile.content_hash}
            for _, reviewer in sorted(self._reviewers.items())
        ]


def create_default_review_registry() -> ReviewProfileRegistry:
    return ReviewProfileRegistry([ExecutionIntegrityReviewer()])


class ReviewService:
    """Run advisory checks without changing qualification or authority state."""

    def __init__(
        self,
        repository_provider: RepositoryProvider = get_repository,
        registry: ReviewProfileRegistry | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._registry = registry or create_default_review_registry()

    def list_profiles(self) -> list[dict[str, Any]]:
        return self._registry.list_profiles()

    def review_execution_run(
        self,
        context: RequestContext,
        run_id: str,
        profile_id: str,
    ) -> dict:
        repository = self._repository_provider()
        run = repository.get_run(context.workspace_id, run_id)
        if run is None:
            raise ResourceNotFoundError("agent_run", run_id)
        reviewer = self._registry.get(profile_id)
        if reviewer.profile.subject_type != "agent_run":
            raise ResourceNotFoundError("review_profile", profile_id)

        snapshot = self._execution_snapshot(run)
        subject_digest = canonical_hash(snapshot)
        drafts = reviewer.evaluate(snapshot)
        review_run_id = str(uuid4())
        counts = Counter(draft.severity.value for draft in drafts)
        review_values = {
            "id": review_run_id,
            "profile_id": reviewer.profile.profile_id,
            "profile_version": reviewer.profile.version,
            "profile_hash": reviewer.profile.content_hash,
            "subject_type": reviewer.profile.subject_type,
            "subject_id": run_id,
            "subject_digest": subject_digest,
            "input_snapshot": snapshot,
            "reviewer_origin": ReviewFindingOrigin.DETERMINISTIC_RULE.value,
            "reviewer_lineage": {
                "contract_version": REVIEWER_LINEAGE_VERSION,
                "implementation": "aigc-lite",
                "profile_hash": reviewer.profile.content_hash,
                "model_route": None,
                "runtime_derived": True,
            },
            "status": "completed",
            "finding_count": len(drafts),
            "severity_counts": dict(sorted(counts.items())),
            "requested_by": context.principal_id,
        }
        findings = [
            self._finding_values(
                draft,
                subject_digest=subject_digest,
                profile_hash=reviewer.profile.content_hash,
                ordinal=index,
            )
            for index, draft in enumerate(drafts, start=1)
        ]
        return repository.create_review_run(
            context.workspace_id, review_values, findings
        )

    def get_review_run(self, context: RequestContext, review_run_id: str) -> dict:
        value = self._repository_provider().get_review_run(
            context.workspace_id, review_run_id
        )
        if value is None:
            raise ResourceNotFoundError("review_run", review_run_id)
        return value

    def list_review_runs(
        self,
        context: RequestContext,
        *,
        subject_id: str | None = None,
        profile_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return self._repository_provider().list_review_runs(
            context.workspace_id,
            subject_type="agent_run" if subject_id else None,
            subject_id=subject_id,
            profile_id=profile_id,
            limit=limit,
        )

    def get_finding(self, context: RequestContext, finding_id: str) -> dict:
        value = self._repository_provider().get_review_finding(
            context.workspace_id, finding_id
        )
        if value is None:
            raise ResourceNotFoundError("review_finding", finding_id)
        return value

    def list_findings(
        self,
        context: RequestContext,
        *,
        review_run_id: str | None = None,
        subject_id: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        return self._repository_provider().list_review_findings(
            context.workspace_id,
            review_run_id=review_run_id,
            subject_id=subject_id,
            severity=severity,
            status=status,
            limit=limit,
        )

    @staticmethod
    def _execution_snapshot(run: dict[str, Any]) -> dict[str, Any]:
        """Freeze only fields required by rules, never duplicate transcript text."""

        steps = [
            {
                "id": step["id"],
                "sequence": int(step["sequence"]),
                "kind": step["kind"],
                "name": step["name"],
                "status": step["status"],
                "has_input": bool(step.get("input_content")),
                "has_output": bool(step.get("output_content")),
                "input_digest": canonical_hash(step.get("input_content") or ""),
                "output_digest": canonical_hash(step.get("output_content") or ""),
                "metadata": step.get("metadata") or {},
                "created_at": step.get("created_at"),
            }
            for step in run.get("steps") or []
        ]
        artifacts = [
            {
                "id": item["id"],
                "step_id": item.get("step_id"),
                "content_hash": item.get("content_hash"),
                "kind": item.get("kind"),
            }
            for item in run.get("artifacts") or []
        ]
        citations = [
            {
                "id": item["id"],
                "step_id": item.get("step_id"),
                "artifact_id": item.get("artifact_id"),
                "source_kind": item.get("source_kind"),
            }
            for item in run.get("citations") or []
        ]
        return {
            "run": {
                "id": run["id"],
                "request_id": run.get("request_id"),
                "session_id": run.get("session_id"),
                "status": run["status"],
                "requested_model": run.get("requested_model"),
                "selected_model": run.get("selected_model"),
                "capability_set_id": run.get("capability_set_id"),
                "capability_policy_hash": run.get("capability_policy_hash"),
                "error_code": run.get("error_code"),
                "created_at": run.get("created_at"),
                "completed_at": run.get("completed_at"),
            },
            "steps": steps,
            "artifacts": artifacts,
            "citations": citations,
        }

    @staticmethod
    def _finding_values(
        draft: ReviewFindingDraft,
        *,
        subject_digest: str,
        profile_hash: str,
        ordinal: int,
    ) -> dict[str, Any]:
        payload = draft.as_dict() | {
            "subject_digest": subject_digest,
            "profile_hash": profile_hash,
            "ordinal": ordinal,
        }
        return {
            "id": str(uuid4()),
            **draft.as_dict(),
            "ordinal": ordinal,
            "status": ReviewFindingStatus.OPEN.value,
            "finding_hash": canonical_hash(payload),
        }
