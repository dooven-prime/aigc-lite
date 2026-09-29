"""Context-bound, read-only workspace capabilities for AI tool clients."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ...config import settings
from ...core.contracts import (
    RequestContext,
    ToolHints,
    ToolProviderResult,
    ToolSource,
    ToolSpec,
)
from ...core.errors import ApplicationError, ErrorCode
from ...database import get_repository
from ...repository import Repository

if TYPE_CHECKING:
    from ...services.artifacts import ArtifactService
    from ...services.memory import MemoryService
    from ...services.qualification import QualificationService
    from ...services.research_registry import ResearchRegistryService

RepositoryProvider = Callable[[], Repository]

_CONTRACT = "aigc-lite.workspace-capability.v1"
_PROVIDER_ID = "workspace-capabilities"
_MAX_QUERY_CHARS = 2_000
_MAX_ID_CHARS = 200
_MAX_PROFILE_CHARS = 200
_MAX_RESULTS = 50
_MAX_PREVIEW_CHARS = 20_000
_MAX_HISTORY = 50


def _schema(
    properties: dict[str, Any], required: tuple[str, ...] = ()
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        value["required"] = list(required)
    return value


_SPECS = (
    (
        "workspace_search",
        "Search this workspace's conversations, knowledge, execution steps, artifacts, "
        "citations, and research claims. Results are candidates unless qualification "
        "metadata explicitly says otherwise.",
        _schema(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": _MAX_QUERY_CHARS},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_RESULTS,
                    "default": 20,
                },
            },
            ("query",),
        ),
    ),
    (
        "workspace_qualified_search",
        "Search only ClaimRevisions with a current qualification for the requested "
        "profile. Every result carries its exact qualification receipt.",
        _schema(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": _MAX_QUERY_CHARS},
                "profile": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": _MAX_PROFILE_CHARS,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_RESULTS,
                    "default": 20,
                },
            },
            ("query", "profile"),
        ),
    ),
    (
        "workspace_get_run",
        "Get one workspace Run and its ordered Steps. Step input/output is omitted by "
        "default and can be requested as bounded previews.",
        _schema(
            {
                "run_id": {"type": "string", "minLength": 1, "maxLength": _MAX_ID_CHARS},
                "include_step_content": {"type": "boolean", "default": False},
                "max_step_chars": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": _MAX_PREVIEW_CHARS,
                    "default": 4_000,
                },
            },
            ("run_id",),
        ),
    ),
    (
        "workspace_get_artifact",
        "Get one Artifact with provenance and Citations. Artifact content is returned as "
        "a bounded preview so the JSON response stays valid.",
        _schema(
            {
                "artifact_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": _MAX_ID_CHARS,
                },
                "max_content_chars": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 50_000,
                    "default": 12_000,
                },
                "citation_limit": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 20,
                },
            },
            ("artifact_id",),
        ),
    ),
    (
        "workspace_get_claim",
        "Get one exact ClaimRevision with sources, relations, verification history, and "
        "promotion evaluations. Historical collections are bounded, never promoted.",
        _schema(
            {
                "claim_revision_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": _MAX_ID_CHARS,
                },
                "history_limit": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": _MAX_HISTORY,
                    "default": 10,
                },
            },
            ("claim_revision_id",),
        ),
    ),
    (
        "workspace_get_qualification_receipt",
        "Get one immutable Qualification Receipt for an exact ClaimRevision, Profile, "
        "EvidenceClosure, and PolicyVersion relation.",
        _schema(
            {
                "receipt_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": _MAX_ID_CHARS,
                }
            },
            ("receipt_id",),
        ),
    ),
    (
        "workspace_list_qualification_profiles",
        "List the server-owned qualification profiles and policy versions available in "
        "this deployment.",
        _schema({}),
    ),
)
_NAMES = frozenset(name for name, _description, _input_schema in _SPECS)


class WorkspaceCapabilityProviderSource:
    """Create a provider bound to the authenticated request context."""

    source_id = "workspace-capabilities"

    def __init__(
        self,
        repository_provider: RepositoryProvider = get_repository,
        *,
        memory_service: MemoryService | None = None,
        artifact_service: ArtifactService | None = None,
        research_registry_service: ResearchRegistryService | None = None,
        qualification_service: QualificationService | None = None,
    ) -> None:
        if memory_service is None:
            from ...services.memory import MemoryService

            memory_service = MemoryService(repository_provider=repository_provider)
        if artifact_service is None:
            from ...services.artifacts import ArtifactService

            artifact_service = ArtifactService(repository_provider=repository_provider)
        if research_registry_service is None:
            from ...services.evidence import EvidenceService
            from ...services.research_registry import ResearchRegistryService

            evidence = EvidenceService(repository_provider)
            research_registry_service = ResearchRegistryService(
                repository_provider,
                artifact_service=artifact_service,
                evidence_service=evidence,
            )
        if qualification_service is None:
            from ...services.evidence import EvidenceService
            from ...services.qualification import QualificationService

            qualification_service = QualificationService(
                repository_provider,
                artifact_service=artifact_service,
                evidence_service=EvidenceService(repository_provider),
            )
        self._memory = memory_service
        self._artifacts = artifact_service
        self._research = research_registry_service
        self._qualification = qualification_service

    def list_providers(self, context: RequestContext) -> list[WorkspaceCapabilityProvider]:
        return [
            WorkspaceCapabilityProvider(
                context,
                memory_service=self._memory,
                artifact_service=self._artifacts,
                research_registry_service=self._research,
                qualification_service=self._qualification,
            )
        ]


class WorkspaceCapabilityProvider:
    """Read-only service projection scoped to one immutable RequestContext."""

    provider_id = _PROVIDER_ID
    is_remote = False

    def __init__(
        self,
        context: RequestContext,
        *,
        memory_service: MemoryService,
        artifact_service: ArtifactService,
        research_registry_service: ResearchRegistryService,
        qualification_service: QualificationService,
    ) -> None:
        self._context = context
        self._memory = memory_service
        self._artifacts = artifact_service
        self._research = research_registry_service
        self._qualification = qualification_service

    async def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=name,
                native_name=name,
                description=description,
                input_schema=input_schema,
                source=ToolSource.WORKSPACE,
                provider_id=self.provider_id,
                workspace_id=self._context.workspace_id,
                timeout_seconds=settings.default_tool_timeout_seconds,
                hints=ToolHints(
                    read_only=True,
                    destructive=False,
                    idempotent=True,
                    open_world=False,
                ),
                extensions={
                    "contract": _CONTRACT,
                    "authority_mutation": False,
                    "candidate_promotion": False,
                },
            )
            for name, description, input_schema in _SPECS
        ]

    async def call_tool(
        self, native_name: str, arguments: dict[str, Any]
    ) -> ToolProviderResult:
        if native_name not in _NAMES:
            return self._failure(ErrorCode.TOOL_NOT_AVAILABLE)
        try:
            result = self._dispatch(native_name, arguments)
        except (TypeError, ValueError):
            return self._failure(ErrorCode.INVALID_TOOL_ARGUMENTS)
        except ApplicationError as exc:
            return self._failure(exc.code)
        return self._success(native_name, result)

    def _dispatch(self, native_name: str, arguments: dict[str, Any]) -> Any:
        if native_name == "workspace_search":
            self._only(arguments, "query", "limit")
            return self._memory.search(
                self._context,
                self._text(arguments, "query", _MAX_QUERY_CHARS),
                self._integer(arguments, "limit", 20, 1, _MAX_RESULTS),
            )
        if native_name == "workspace_qualified_search":
            self._only(arguments, "query", "profile", "limit")
            return self._qualification.qualified_search(
                self._context,
                self._text(arguments, "query", _MAX_QUERY_CHARS),
                self._text(arguments, "profile", _MAX_PROFILE_CHARS),
                self._integer(arguments, "limit", 20, 1, _MAX_RESULTS),
            )
        if native_name == "workspace_get_run":
            self._only(
                arguments, "run_id", "include_step_content", "max_step_chars"
            )
            run = self._memory.get_run(
                self._context, self._text(arguments, "run_id", _MAX_ID_CHARS)
            )
            return self._project_run(
                run,
                include_content=self._boolean(
                    arguments, "include_step_content", False
                ),
                maximum=self._integer(
                    arguments,
                    "max_step_chars",
                    4_000,
                    0,
                    _MAX_PREVIEW_CHARS,
                ),
            )
        if native_name == "workspace_get_artifact":
            self._only(
                arguments, "artifact_id", "max_content_chars", "citation_limit"
            )
            artifact = self._artifacts.get(
                self._context,
                self._text(arguments, "artifact_id", _MAX_ID_CHARS),
            )
            return self._project_artifact(
                artifact,
                maximum=self._integer(
                    arguments, "max_content_chars", 12_000, 0, 50_000
                ),
                citation_limit=self._integer(
                    arguments, "citation_limit", 20, 0, 100
                ),
            )
        if native_name == "workspace_get_claim":
            self._only(arguments, "claim_revision_id", "history_limit")
            claim = self._research.get_claim(
                self._context,
                self._text(arguments, "claim_revision_id", _MAX_ID_CHARS),
            )
            return self._project_claim(
                claim,
                self._integer(arguments, "history_limit", 10, 0, _MAX_HISTORY),
            )
        if native_name == "workspace_get_qualification_receipt":
            self._only(arguments, "receipt_id")
            return self._qualification.get_receipt(
                self._context,
                self._text(arguments, "receipt_id", _MAX_ID_CHARS),
            )
        if native_name == "workspace_list_qualification_profiles":
            self._only(arguments)
            return self._qualification.list_profiles()
        raise AssertionError("unreachable workspace capability dispatch")

    @staticmethod
    def _project_run(
        run: dict[str, Any], *, include_content: bool, maximum: int
    ) -> dict[str, Any]:
        value = dict(run)
        steps = []
        for original in run.get("steps") or []:
            step = dict(original)
            for field in ("input_content", "output_content"):
                content = str(step.get(field) or "")
                step[f"{field}_chars"] = len(content)
                if include_content:
                    step[field] = content[:maximum]
                    step[f"{field}_truncated"] = len(content) > maximum
                else:
                    step.pop(field, None)
            steps.append(step)
        value["steps"] = steps
        return value

    @staticmethod
    def _project_artifact(
        artifact: dict[str, Any], *, maximum: int, citation_limit: int
    ) -> dict[str, Any]:
        value = dict(artifact)
        content = str(value.get("content_text") or "")
        value["content_text"] = content[:maximum]
        value["content_chars"] = len(content)
        value["content_truncated"] = len(content) > maximum
        citations = list(value.get("citations") or [])
        value["citation_count"] = len(citations)
        value["citations"] = citations[:citation_limit]
        return value

    @staticmethod
    def _project_claim(claim: dict[str, Any], limit: int) -> dict[str, Any]:
        value = dict(claim)
        counts: dict[str, int] = {}
        for field in (
            "relations",
            "verification_attempts",
            "verification_plans",
            "verification_executions",
            "promotion_evaluations",
        ):
            items = list(value.get(field) or [])
            counts[field] = len(items)
            value[field] = items[:limit]
        value["history_counts"] = counts
        value["history_limit"] = limit
        return value

    @staticmethod
    def _only(arguments: dict[str, Any], *allowed: str) -> None:
        if not isinstance(arguments, dict) or set(arguments) - set(allowed):
            raise ValueError

    @staticmethod
    def _text(arguments: dict[str, Any], field: str, maximum: int) -> str:
        value = arguments.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError
        return value.strip()

    @staticmethod
    def _integer(
        arguments: dict[str, Any], field: str, default: int, minimum: int, maximum: int
    ) -> int:
        value = arguments.get(field, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError
        if value < minimum or value > maximum:
            raise ValueError
        return value

    @staticmethod
    def _boolean(arguments: dict[str, Any], field: str, default: bool) -> bool:
        value = arguments.get(field, default)
        if not isinstance(value, bool):
            raise ValueError
        return value

    @staticmethod
    def _failure(code: ErrorCode) -> ToolProviderResult:
        return ToolProviderResult(
            content=json.dumps({"error": code.value}),
            failed=True,
            metadata={"error_code": code.value, "contract": _CONTRACT},
        )

    @staticmethod
    def _success(native_name: str, result: Any) -> ToolProviderResult:
        content = json.dumps(
            {
                "schema": _CONTRACT,
                "capability": native_name,
                "result": result,
            },
            ensure_ascii=False,
            default=str,
        )
        if len(content) > settings.max_tool_result_chars:
            return WorkspaceCapabilityProvider._failure(
                ErrorCode.TOOL_RESULT_TOO_LARGE
            )
        return ToolProviderResult(
            content=content,
            metadata={"contract": _CONTRACT, "read_only": True},
        )
