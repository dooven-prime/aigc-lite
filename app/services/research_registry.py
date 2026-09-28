"""Research Registry application service and AI Frontier importer."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict

from ..core.artifacts import ArtifactDraft, ArtifactKind
from ..core.contracts import RequestContext
from ..core.errors import InvalidEvidenceError, ResourceNotFoundError
from ..core.evidence import (
    ClaimDraft,
    EvidenceResolution,
    ExecutionReceiptDraft,
    FreezeManifestDraft,
    ProtocolDraft,
    ProtocolStatus,
    ReviewDraft,
    ReviewerKind,
    ReviewStatus,
)
from ..core.research import (
    ClaimClosureStatus,
    ClaimPromotionStage,
    ClaimRelationDraft,
    ClaimRelationStatus,
    ClaimRelationType,
    ClaimRevisionDraft,
    PromotionGateDecision,
    ResearchCaseDraft,
    ResearchCaseStatus,
    SourceReferenceDraft,
    VerificationAttemptDraft,
    VerificationOutcome,
)
from ..database import get_repository
from ..profiles.frontier import (
    CAUSAL_STATUSES,
    CLAIM_TYPES,
    IMPORTER_VERSION,
    PROFILE_ID,
    PROMOTION_POLICY_VERSION,
    UNCERTAINTY_STATUSES,
)
from ..redaction import redact, redact_record_text, redact_text
from ..repository import Repository
from .artifacts import ArtifactService
from .evidence import EvidenceService

RepositoryProvider = Callable[[], Repository]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NEXT_PROMOTION_STAGE = {
    ClaimPromotionStage.REGISTERED: ClaimPromotionStage.EVIDENCE_READY,
    ClaimPromotionStage.EVIDENCE_READY: ClaimPromotionStage.REVIEW_READY,
    ClaimPromotionStage.REVIEW_READY: ClaimPromotionStage.RELEASE_READY,
}
_REQUIRED_CLAIM_FIELDS = frozenset(
    {
        "claim_id",
        "statement",
        "claim_type",
        "scope",
        "source_refs",
        "calculation_version",
        "uncertainty_status",
        "causal_status",
        "revision_status",
    }
)


class ResearchRegistryService:
    def __init__(
        self,
        repository_provider: RepositoryProvider = get_repository,
        *,
        artifact_service: ArtifactService | None = None,
        evidence_service: EvidenceService | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._artifacts = artifact_service or ArtifactService(
            repository_provider=repository_provider
        )
        self._evidence = evidence_service or EvidenceService(repository_provider)

    def import_frontier(
        self,
        context: RequestContext,
        *,
        source_name: str,
        registry_bytes: bytes,
        source_ledger_bytes: bytes | None = None,
    ) -> dict:
        source_name = self._text("source_name", source_name, 200)
        payload = self._json(registry_bytes, "registry_file")
        normalized = self._normalize_frontier(payload)
        ledger_text = None
        if source_ledger_bytes is not None:
            try:
                ledger_text = source_ledger_bytes.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise InvalidEvidenceError(
                    "source_ledger_file", "source_ledger_file must contain UTF-8 text"
                ) from exc

        members = [self._source_member("claim-registry.json", registry_bytes)]
        if source_ledger_bytes is not None:
            members.append(
                self._source_member("public-source-ledger.md", source_ledger_bytes)
            )
        bundle_hash = self._manifest_hash(
            [*members, {"name": "importer", "version": IMPORTER_VERSION}]
        )
        repository = self._repository_provider()
        existing = repository.get_evidence_protocol_by_hash(
            context.workspace_id, PROFILE_ID, bundle_hash
        )
        if existing is not None:
            research_case = repository.get_research_case_by_protocol(
                context.workspace_id, existing["id"]
            )
            if research_case is not None:
                return {
                    **self.dashboard(context, research_case_id=research_case["id"]),
                    "imported": False,
                }

        protocol = self._evidence.create_protocol(
            context,
            ProtocolDraft(
                name=source_name,
                profile=PROFILE_ID,
                purpose="Inspect a versioned research claim registry and its source closure.",
                scope=(
                    "Imported research snapshot only. Structural closure does not establish "
                    "truth, causality, scientific validity, or decision authority."
                ),
                completion_predicate=(
                    "Every claim satisfies the declared Frontier contract and every declared "
                    "source contains a locator and syntactically valid SHA-256 digest."
                ),
                stop_conditions=(
                    "Duplicate or missing claim identifier",
                    "Unsupported claim, uncertainty, or causal status",
                    "Malformed source locator or content digest",
                    "Uploaded registry exceeds the bounded import contract",
                ),
                budget={"max_claims": 2_000, "max_sources_per_claim": 100, "network_calls": 0},
                status=ProtocolStatus.FROZEN,
            ),
            content_hash=bundle_hash,
        )

        registry_artifact = self._artifacts.create_artifact(
            context,
            ArtifactDraft(
                name=f"{source_name} claim-registry.json",
                kind=ArtifactKind.JSON,
                media_type="application/json",
                content_text=json.dumps(payload, ensure_ascii=False, indent=2),
                metadata={
                    "profile": PROFILE_ID,
                    "protocol_id": protocol["id"],
                    "role": "claim_registry",
                    "registry_id": normalized["registry_id"],
                    "registry_version": normalized["registry_version"],
                },
            ),
        )
        artifacts = [registry_artifact]
        ledger_artifact = None
        if ledger_text is not None:
            ledger_artifact = self._artifacts.create_artifact(
                context,
                ArtifactDraft(
                    name=f"{source_name} public-source-ledger.md",
                    kind=ArtifactKind.MARKDOWN,
                    media_type="text/markdown",
                    content_text=ledger_text,
                    metadata={
                        "profile": PROFILE_ID,
                        "protocol_id": protocol["id"],
                        "role": "source_ledger",
                    },
                ),
            )
            artifacts.append(ledger_artifact)

        normalized_output = {
            "registry_id": normalized["registry_id"],
            "registry_version": normalized["registry_version"],
            "claim_keys": [item.claim_key for item in normalized["claims"]],
            "source_refs": [item.ref_key for item in normalized["sources"]],
            "blocked_claims": sum(
                item.closure_status is ClaimClosureStatus.BLOCKED
                for item in normalized["claims"]
            ),
        }
        receipt = self._evidence.create_receipt(
            context,
            protocol["id"],
            ExecutionReceiptDraft(
                status="imported",
                input_digest=hashlib.sha256(registry_bytes).hexdigest(),
                output_digest=self._json_hash(normalized_output),
                runtime={"importer": IMPORTER_VERSION, "network_calls": 0},
                budget={
                    "claims": len(normalized["claims"]),
                    "unique_sources": len(normalized["sources"]),
                },
                artifact_ids=tuple(item["id"] for item in artifacts),
                metadata={
                    "source": "ai-frontier-claim-registry",
                    "structural_validation_only": True,
                },
            ),
        )
        integrity_claim = self._evidence.create_claim(
            context,
            protocol["id"],
            ClaimDraft(
                statement=(
                    f"Registry {normalized['registry_id']}@{normalized['registry_version']} "
                    f"contains {len(normalized['claims'])} unique contract-compatible claims."
                ),
                resolution=EvidenceResolution.SUPPORTED,
                scope="Import-time schema, enum, identifier, locator, and digest validation.",
                evidence_refs=tuple(item["id"] for item in artifacts),
                prohibited_upgrades=(
                    "Do not infer that an imported research claim is true.",
                    "Do not infer causality from a structurally closed source reference.",
                    "Do not treat local validation as independent scientific review.",
                    "Do not use this registry as authority for an external action.",
                ),
            ),
        )
        self._evidence.create_review(
            context,
            receipt["id"],
            ReviewDraft(
                status=ReviewStatus.ACCEPTED,
                reviewer_kind=ReviewerKind.AUTOMATED,
                reviewer_id="aigc-lite.frontier-registry-importer",
                independent=False,
                finding=(
                    "The registry passed local structural validation. Referenced source "
                    "content was not fetched or independently reviewed."
                ),
                evidence_refs=(integrity_claim["id"],),
            ),
        )
        stored_members = tuple(
            {
                "name": artifact["name"],
                "artifact_id": artifact["id"],
                "sha256": artifact["content_hash"],
                "size_bytes": artifact["size_bytes"],
            }
            for artifact in artifacts
        )
        self._evidence.create_freeze(
            context,
            protocol["id"],
            FreezeManifestDraft(
                name=f"{source_name} registry freeze",
                version=1,
                members=stored_members,
                content_hash=self._manifest_hash(
                    [
                        {
                            "name": item["name"],
                            "sha256": item["sha256"],
                            "size_bytes": item["size_bytes"],
                        }
                        for item in stored_members
                    ]
                ),
            ),
        )

        case_draft = ResearchCaseDraft(
            name=source_name,
            profile=PROFILE_ID,
            registry_id=normalized["registry_id"],
            registry_version=normalized["registry_version"],
            authority=normalized["authority"],
            as_of_date=normalized["as_of_date"],
            status=ResearchCaseStatus.FROZEN,
            protocol_id=protocol["id"],
            receipt_id=receipt["id"],
            source_artifact_id=registry_artifact["id"],
            source_ledger_artifact_id=(
                ledger_artifact["id"] if ledger_artifact is not None else None
            ),
            metadata={
                "claim_contract": normalized["claim_contract"],
                "structural_validation_only": True,
            },
        )
        research_case = repository.create_research_registry(
            context.workspace_id,
            self._case_values(case_draft),
            [asdict(item) for item in normalized["sources"]],
            [self._claim_values(item) for item in normalized["claims"]],
        )
        return {
            **self.dashboard(context, research_case_id=research_case["id"]),
            "imported": True,
        }

    def dashboard(
        self,
        context: RequestContext,
        *,
        research_case_id: str | None = None,
        limit: int = 2_000,
    ) -> dict:
        repository = self._repository_provider()
        cases = repository.list_research_cases(
            context.workspace_id, profile=PROFILE_ID, limit=100
        )
        research_case = None
        if research_case_id is not None:
            research_case = repository.get_research_case(
                context.workspace_id, research_case_id
            )
            if research_case is None or research_case.get("profile") != PROFILE_ID:
                raise ResourceNotFoundError("research_case", research_case_id)
        elif cases:
            research_case = cases[0]
        if research_case is None:
            return {
                "cases": cases,
                "case": None,
                "protocol": None,
                "claims": [],
                "relations": [],
                "statistics": self._statistics([]),
            }
        claims = repository.list_research_claims(
            context.workspace_id,
            research_case_id=research_case["id"],
            limit=limit,
        )
        protocol = self._protocol_projection(
            self._evidence.get_protocol(context, research_case["protocol_id"])
        )
        return {
            "cases": cases,
            "case": research_case,
            "protocol": protocol,
            "claims": claims,
            "relations": repository.list_research_claim_relations(
                context.workspace_id, research_case_id=research_case["id"]
            ),
            "statistics": self._statistics(claims),
        }

    def get_claim(self, context: RequestContext, claim_id: str) -> dict:
        repository = self._repository_provider()
        claim = repository.get_research_claim(context.workspace_id, claim_id)
        if claim is None:
            raise ResourceNotFoundError("research_claim", claim_id)
        claim["relations"] = repository.list_research_claim_relations(
            context.workspace_id,
            research_case_id=claim["research_case_id"],
            claim_id=claim_id,
        )
        claim["verification_attempts"] = (
            repository.list_research_verification_attempts(
                context.workspace_id, claim_id
            )
        )
        claim["verification_plans"] = repository.list_research_verification_plans(
            context.workspace_id, claim_id
        )
        claim["verification_executions"] = (
            repository.list_research_verification_executions(
                context.workspace_id, claim_id
            )
        )
        claim["promotion_evaluations"] = (
            repository.list_research_promotion_evaluations(
                context.workspace_id, claim_id
            )
        )
        return claim

    def create_relation(
        self,
        context: RequestContext,
        claim_id: str,
        draft: ClaimRelationDraft,
    ) -> dict:
        repository = self._repository_provider()
        source = repository.get_research_claim(context.workspace_id, claim_id)
        if source is None:
            raise ResourceNotFoundError("research_claim", claim_id)
        target = repository.get_research_claim(
            context.workspace_id, draft.target_claim_id
        )
        if target is None or target["research_case_id"] != source["research_case_id"]:
            raise ResourceNotFoundError("research_claim", draft.target_claim_id)
        if source["id"] == target["id"]:
            raise InvalidEvidenceError(
                "target_claim_id", "A claim relation cannot target itself"
            )
        rationale = self._text("rationale", draft.rationale, 4_000)
        evidence_refs = tuple(
            self._text("evidence_ref", item, 500) for item in draft.evidence_refs
        )
        if len(evidence_refs) > 100:
            raise InvalidEvidenceError(
                "evidence_refs", "evidence_refs must contain at most 100 entries"
            )
        relations = repository.list_research_claim_relations(
            context.workspace_id, research_case_id=source["research_case_id"]
        )
        if any(
            item["source_claim_id"] == source["id"]
            and item["target_claim_id"] == target["id"]
            and item["relation_type"] == draft.relation_type.value
            for item in relations
        ):
            raise InvalidEvidenceError("relation", "The claim relation already exists")
        if draft.relation_type is ClaimRelationType.DEPENDS_ON and self._would_cycle(
            relations, source["id"], target["id"]
        ):
            raise InvalidEvidenceError(
                "relation", "The dependency relation would create a cycle"
            )
        return repository.create_research_claim_relation(
            context.workspace_id,
            {
                "research_case_id": source["research_case_id"],
                "source_claim_id": source["id"],
                "target_claim_id": target["id"],
                "relation_type": draft.relation_type.value,
                "status": ClaimRelationStatus.ACTIVE.value,
                "rationale": rationale,
                "evidence_refs": list(evidence_refs),
                "metadata": {"profile": PROFILE_ID},
                "created_by": context.principal_id,
            },
        )

    def withdraw_relation(
        self,
        context: RequestContext,
        claim_id: str,
        relation_id: str,
        *,
        reason: str,
    ) -> dict:
        repository = self._repository_provider()
        claim = repository.get_research_claim(context.workspace_id, claim_id)
        if claim is None:
            raise ResourceNotFoundError("research_claim", claim_id)
        relations = repository.list_research_claim_relations(
            context.workspace_id,
            research_case_id=claim["research_case_id"],
            claim_id=claim_id,
        )
        if not any(item["id"] == relation_id for item in relations):
            raise ResourceNotFoundError("research_claim_relation", relation_id)
        value = repository.withdraw_research_claim_relation(
            context.workspace_id,
            relation_id,
            reason=self._text("reason", reason, 2_000),
            withdrawn_by=context.principal_id,
        )
        if value is None:
            raise ResourceNotFoundError("research_claim_relation", relation_id)
        return value

    def record_verification_attempt(
        self,
        context: RequestContext,
        claim_id: str,
        draft: VerificationAttemptDraft,
    ) -> dict:
        repository = self._repository_provider()
        claim = repository.get_research_claim(context.workspace_id, claim_id)
        if claim is None:
            raise ResourceNotFoundError("research_claim", claim_id)
        research_case = repository.get_research_case(
            context.workspace_id, claim["research_case_id"]
        )
        if research_case is None:
            raise ResourceNotFoundError("research_case", claim["research_case_id"])
        self._digest("input_digest", draft.input_digest)
        self._digest("output_digest", draft.output_digest)
        method = self._text("method", draft.method, 4_000)
        scope = self._text("scope", draft.scope, 8_000)
        if len(draft.artifact_ids) > 100:
            raise InvalidEvidenceError(
                "artifact_ids", "artifact_ids must contain at most 100 entries"
            )
        try:
            receipt = self._evidence.create_receipt(
                context,
                research_case["protocol_id"],
                ExecutionReceiptDraft(
                    status=f"verification_{draft.outcome.value}",
                    input_digest=draft.input_digest,
                    output_digest=draft.output_digest,
                    run_id=draft.run_id,
                    runtime={"kind": draft.kind.value, "method": method},
                    artifact_ids=draft.artifact_ids,
                    metadata={
                        **redact(draft.metadata),
                        "claim_revision_id": claim_id,
                        "independent": draft.independent,
                        "independence_self_attested": draft.independent,
                        "scope": scope,
                    },
                ),
            )
        except KeyError as exc:
            raise InvalidEvidenceError(
                "verification_attempt",
                "run_id or artifact_ids do not belong to this workspace",
            ) from exc
        attempt = repository.create_research_verification_attempt(
            context.workspace_id,
            {
                "research_case_id": claim["research_case_id"],
                "claim_revision_id": claim_id,
                "receipt_id": receipt["id"],
                "run_id": draft.run_id,
                "plan_id": draft.plan_id,
                "verification_execution_id": draft.verification_execution_id,
                "kind": draft.kind.value,
                "outcome": draft.outcome.value,
                "method": method,
                "scope": scope,
                "independent": draft.independent,
                "input_digest": draft.input_digest,
                "output_digest": draft.output_digest,
                "artifact_ids": list(draft.artifact_ids),
                "metadata": redact(draft.metadata),
                "created_by": context.principal_id,
            },
        )
        attempt["receipt"] = receipt
        return attempt

    def next_promotion_stage(
        self, context: RequestContext, claim_id: str
    ) -> ClaimPromotionStage | None:
        claim = self._repository_provider().get_research_claim(
            context.workspace_id, claim_id
        )
        if claim is None:
            raise ResourceNotFoundError("research_claim", claim_id)
        try:
            current = ClaimPromotionStage(
                claim.get("promotion_stage") or ClaimPromotionStage.REGISTERED
            )
        except ValueError as exc:
            raise InvalidEvidenceError(
                "promotion_stage", "The stored promotion stage is unsupported"
            ) from exc
        return _NEXT_PROMOTION_STAGE.get(current)

    def evaluate_promotion(
        self,
        context: RequestContext,
        claim_id: str,
        target_stage: ClaimPromotionStage,
        *,
        required_attempt_id: str | None = None,
    ) -> dict:
        repository = self._repository_provider()
        claim = repository.get_research_claim(context.workspace_id, claim_id)
        if claim is None:
            raise ResourceNotFoundError("research_claim", claim_id)
        try:
            current_stage = ClaimPromotionStage(
                claim.get("promotion_stage") or ClaimPromotionStage.REGISTERED
            )
        except ValueError as exc:
            raise InvalidEvidenceError(
                "promotion_stage", "The stored promotion stage is unsupported"
            ) from exc
        expected = _NEXT_PROMOTION_STAGE.get(current_stage)
        if expected is None or target_stage is not expected:
            expected_label = expected.value if expected is not None else "none"
            raise InvalidEvidenceError(
                "target_stage",
                f"The next allowed stage from {current_stage.value} is {expected_label}",
            )

        attempts = repository.list_research_verification_attempts(
            context.workspace_id, claim_id
        )
        case_relations = repository.list_research_claim_relations(
            context.workspace_id, research_case_id=claim["research_case_id"]
        )
        relevant_relations = [
            item
            for item in case_relations
            if item["source_claim_id"] == claim_id
            or item["target_claim_id"] == claim_id
        ]
        criteria = self._promotion_criteria(
            repository,
            context,
            claim,
            target_stage,
            attempts,
            case_relations,
            required_attempt_id,
        )
        blockers = [item["code"] for item in criteria if not item["passed"]]
        decision = (
            PromotionGateDecision.BLOCKED
            if blockers
            else PromotionGateDecision.PASSED
        )
        input_snapshot = {
            "policy_version": PROMOTION_POLICY_VERSION,
            "claim": {
                key: claim.get(key)
                for key in (
                    "id",
                    "revision_number",
                    "closure_status",
                    "lifecycle_status",
                    "promotion_stage",
                    "status_axes",
                    "blockers",
                )
            },
            "target_stage": target_stage.value,
            "required_attempt_id": required_attempt_id,
            "attempts": [
                {
                    key: item.get(key)
                    for key in (
                        "id",
                        "kind",
                        "outcome",
                        "independent",
                        "input_digest",
                        "output_digest",
                        "artifact_ids",
                    )
                }
                for item in attempts
            ],
            "relations": [
                {
                    key: item.get(key)
                    for key in (
                        "id",
                        "source_claim_id",
                        "target_claim_id",
                        "relation_type",
                        "status",
                    )
                }
                for item in relevant_relations
            ],
            "criteria": criteria,
        }
        input_digest = self._json_hash(input_snapshot)
        evaluation_digest = self._json_hash(
            {
                "input_digest": input_digest,
                "from_stage": current_stage.value,
                "target_stage": target_stage.value,
                "decision": decision.value,
                "blockers": blockers,
            }
        )
        try:
            evaluation = repository.create_research_promotion_evaluation(
                context.workspace_id,
                {
                    "research_case_id": claim["research_case_id"],
                    "claim_revision_id": claim_id,
                    "from_stage": current_stage.value,
                    "target_stage": target_stage.value,
                    "decision": decision.value,
                    "policy_version": PROMOTION_POLICY_VERSION,
                    "input_digest": input_digest,
                    "evaluation_digest": evaluation_digest,
                    "criteria": criteria,
                    "blockers": blockers,
                    "attempt_ids": [item["id"] for item in attempts],
                    "relation_ids": [item["id"] for item in relevant_relations],
                    "created_by": context.principal_id,
                },
                promote=decision is PromotionGateDecision.PASSED,
            )
        except KeyError as exc:
            raise InvalidEvidenceError(
                "promotion_stage",
                "The claim changed while the promotion gate was being evaluated",
            ) from exc
        return {"evaluation": evaluation, "claim": self.get_claim(context, claim_id)}

    @staticmethod
    def _would_cycle(relations: list[dict], source_id: str, target_id: str) -> bool:
        graph: dict[str, set[str]] = {}
        for relation in relations:
            if (
                relation["relation_type"] == ClaimRelationType.DEPENDS_ON.value
                and relation["status"] == ClaimRelationStatus.ACTIVE.value
            ):
                graph.setdefault(relation["source_claim_id"], set()).add(
                    relation["target_claim_id"]
                )
        graph.setdefault(source_id, set()).add(target_id)
        pending = [target_id]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == source_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(graph.get(current, ()))
        return False

    @staticmethod
    def _promotion_criteria(
        repository: Repository,
        context: RequestContext,
        claim: dict,
        target_stage: ClaimPromotionStage,
        attempts: list[dict],
        case_relations: list[dict],
        required_attempt_id: str | None = None,
    ) -> list[dict]:
        criteria = [
            {
                "code": "source_closure_closed",
                "passed": claim["closure_status"] == ClaimClosureStatus.CLOSED.value,
                "observed": claim["closure_status"],
            }
        ]
        passed_attempts = [
            item
            for item in attempts
            if item["outcome"] == VerificationOutcome.PASSED.value
        ]
        if required_attempt_id is not None:
            triggering_attempt = next(
                (item for item in attempts if item["id"] == required_attempt_id),
                None,
            )
            criteria.append(
                {
                    "code": "triggering_attempt_passed",
                    "passed": bool(
                        triggering_attempt is not None
                        and triggering_attempt["outcome"]
                        == VerificationOutcome.PASSED.value
                    ),
                    "observed": (
                        triggering_attempt["outcome"]
                        if triggering_attempt is not None
                        else "missing"
                    ),
                }
            )
        if target_stage is ClaimPromotionStage.EVIDENCE_READY:
            criteria.append(
                {
                    "code": "passed_verification_attempt_present",
                    "passed": bool(passed_attempts),
                    "observed": len(passed_attempts),
                }
            )
        if target_stage is ClaimPromotionStage.REVIEW_READY:
            independent = [item for item in passed_attempts if item["independent"]]
            criteria.append(
                {
                    "code": "declared_independent_passed_attempt_present",
                    "passed": bool(independent),
                    "observed": len(independent),
                }
            )
        if target_stage is ClaimPromotionStage.RELEASE_READY:
            artifact_bound = [
                item
                for item in passed_attempts
                if item["independent"] and item.get("artifact_ids")
            ]
            criteria.append(
                {
                    "code": "artifact_bound_declared_independent_attempt_present",
                    "passed": bool(artifact_bound),
                    "observed": len(artifact_bound),
                }
            )
            refutations = [
                item
                for item in case_relations
                if item["target_claim_id"] == claim["id"]
                and item["relation_type"] == ClaimRelationType.REFUTES.value
                and item["status"] == ClaimRelationStatus.ACTIVE.value
            ]
            criteria.append(
                {
                    "code": "no_active_refutation",
                    "passed": not refutations,
                    "observed": [item["id"] for item in refutations],
                }
            )
            dependencies = [
                item
                for item in case_relations
                if item["source_claim_id"] == claim["id"]
                and item["relation_type"] == ClaimRelationType.DEPENDS_ON.value
                and item["status"] == ClaimRelationStatus.ACTIVE.value
            ]
            unresolved = []
            for relation in dependencies:
                dependency = repository.get_research_claim(
                    context.workspace_id, relation["target_claim_id"]
                )
                if dependency is None or dependency.get("promotion_stage") not in {
                    ClaimPromotionStage.REVIEW_READY.value,
                    ClaimPromotionStage.RELEASE_READY.value,
                }:
                    unresolved.append(
                        {
                            "relation_id": relation["id"],
                            "claim_id": relation["target_claim_id"],
                            "stage": (
                                dependency.get("promotion_stage")
                                if dependency is not None
                                else "missing"
                            ),
                        }
                    )
            criteria.append(
                {
                    "code": "dependencies_review_ready",
                    "passed": not unresolved,
                    "observed": unresolved,
                }
            )
        return criteria

    def _normalize_frontier(self, payload: dict) -> dict:
        registry_id = self._text("registry_id", payload.get("registry_id"), 200)
        registry_version = self._text(
            "registry_version", payload.get("registry_version"), 64
        )
        scope = payload.get("scope")
        if not isinstance(scope, dict):
            raise InvalidEvidenceError("scope", "scope must be an object")
        authority = self._text(
            "scope.authority", scope.get("authority") or "research_only", 100
        )
        as_of_date = self._optional_text("scope.as_of_date", scope.get("as_of_date"), 32)

        contract = payload.get("claim_contract")
        if not isinstance(contract, dict):
            raise InvalidEvidenceError(
                "claim_contract", "claim_contract must be an object"
            )
        required = contract.get("required_fields")
        if (
            not isinstance(required, list)
            or not all(isinstance(field, str) for field in required)
            or not _REQUIRED_CLAIM_FIELDS <= set(required)
        ):
            raise InvalidEvidenceError(
                "claim_contract.required_fields",
                "claim_contract does not include the required Frontier fields",
            )
        declared_types = contract.get("claim_types")
        if (
            not isinstance(declared_types, list)
            or not declared_types
            or not all(isinstance(claim_type, str) for claim_type in declared_types)
        ):
            raise InvalidEvidenceError(
                "claim_contract.claim_types", "claim_types must be a non-empty list"
            )

        raw_claims = payload.get("claims")
        if not isinstance(raw_claims, list) or not 1 <= len(raw_claims) <= 2_000:
            raise InvalidEvidenceError(
                "claims", "claims must contain between 1 and 2000 entries"
            )
        seen_claims: set[str] = set()
        sources: dict[str, SourceReferenceDraft] = {}
        claims: list[ClaimRevisionDraft] = []
        for index, raw_claim in enumerate(raw_claims):
            field = f"claims[{index}]"
            if not isinstance(raw_claim, dict):
                raise InvalidEvidenceError(field, f"{field} must be an object")
            missing = _REQUIRED_CLAIM_FIELDS - set(raw_claim)
            if missing:
                raise InvalidEvidenceError(
                    field, f"{field} is missing {', '.join(sorted(missing))}"
                )
            claim_key = self._text(f"{field}.claim_id", raw_claim["claim_id"], 200)
            if claim_key in seen_claims:
                raise InvalidEvidenceError(
                    f"{field}.claim_id", f"Duplicate claim id {claim_key}"
                )
            seen_claims.add(claim_key)
            claim_type = self._text(
                f"{field}.claim_type", raw_claim["claim_type"], 100
            )
            if claim_type not in CLAIM_TYPES or claim_type not in declared_types:
                raise InvalidEvidenceError(
                    f"{field}.claim_type", f"Unsupported claim type {claim_type}"
                )
            uncertainty = self._text(
                f"{field}.uncertainty_status", raw_claim["uncertainty_status"], 100
            )
            if uncertainty not in UNCERTAINTY_STATUSES:
                raise InvalidEvidenceError(
                    f"{field}.uncertainty_status",
                    f"Unsupported uncertainty status {uncertainty}",
                )
            causal = self._text(
                f"{field}.causal_status", raw_claim["causal_status"], 100
            )
            if causal not in CAUSAL_STATUSES:
                raise InvalidEvidenceError(
                    f"{field}.causal_status", f"Unsupported causal status {causal}"
                )
            revision_status = self._text(
                f"{field}.revision_status", raw_claim["revision_status"], 100
            )
            raw_sources = raw_claim["source_refs"]
            if not isinstance(raw_sources, list) or len(raw_sources) > 100:
                raise InvalidEvidenceError(
                    f"{field}.source_refs", "source_refs must be a list of at most 100 entries"
                )
            source_ref_keys = []
            for source_index, raw_source in enumerate(raw_sources):
                source_field = f"{field}.source_refs[{source_index}]"
                if not isinstance(raw_source, dict):
                    raise InvalidEvidenceError(
                        source_field, f"{source_field} must be an object"
                    )
                source_key = self._text(
                    f"{source_field}.source_id", raw_source.get("source_id"), 200
                )
                locator = self._text(
                    f"{source_field}.locator", raw_source.get("locator"), 4_000
                )
                content_hash = self._text(
                    f"{source_field}.sha256", raw_source.get("sha256"), 64
                ).lower()
                if not _SHA256.fullmatch(content_hash):
                    raise InvalidEvidenceError(
                        f"{source_field}.sha256", "source sha256 must be a SHA-256 digest"
                    )
                ref_key = self._json_hash(
                    {
                        "source_key": source_key,
                        "locator": locator,
                        "content_hash": content_hash,
                    }
                )
                source_ref_keys.append(ref_key)
                sources.setdefault(
                    ref_key,
                    SourceReferenceDraft(
                        ref_key=ref_key,
                        source_key=source_key,
                        locator=locator,
                        content_hash=content_hash,
                        metadata={"declared_by_registry": registry_id},
                    ),
                )
            blockers = []
            if not source_ref_keys:
                blockers.append("source_refs_missing")
            if revision_status != "current":
                blockers.append(f"revision_status:{revision_status}")
            closure_status = (
                ClaimClosureStatus.BLOCKED
                if blockers
                else ClaimClosureStatus.CLOSED
            )
            claims.append(
                ClaimRevisionDraft(
                    claim_key=claim_key,
                    statement=self._text(
                        f"{field}.statement", raw_claim["statement"], 8_000
                    ),
                    claim_type=claim_type,
                    scope=self._text(f"{field}.scope", raw_claim["scope"], 8_000),
                    method_revision=self._text(
                        f"{field}.calculation_version",
                        raw_claim["calculation_version"],
                        200,
                    ),
                    lifecycle_status=revision_status,
                    closure_status=closure_status,
                    status_axes={"uncertainty": uncertainty, "causal": causal},
                    blockers=tuple(blockers),
                    source_ref_keys=tuple(source_ref_keys),
                )
            )
        return {
            "registry_id": registry_id,
            "registry_version": registry_version,
            "authority": authority,
            "as_of_date": as_of_date,
            "claim_contract": redact(contract),
            "sources": list(sources.values()),
            "claims": claims,
        }

    @staticmethod
    def _case_values(draft: ResearchCaseDraft) -> dict:
        value = asdict(draft)
        value["status"] = draft.status.value
        return value

    @staticmethod
    def _claim_values(draft: ClaimRevisionDraft) -> dict:
        value = asdict(draft)
        value["closure_status"] = draft.closure_status.value
        value["blockers"] = list(draft.blockers)
        value["source_ref_keys"] = list(draft.source_ref_keys)
        return value

    @staticmethod
    def _json(content: bytes, field: str) -> dict:
        try:
            value = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidEvidenceError(field, f"{field} must contain UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise InvalidEvidenceError(field, f"{field} must contain a JSON object")
        return value

    @staticmethod
    def _text(field: str, value: object, maximum: int) -> str:
        if not isinstance(value, str):
            raise InvalidEvidenceError(field, f"{field} must be a string")
        cleaned = redact_record_text(value.strip(), max_chars=maximum + 1)
        if not cleaned:
            raise InvalidEvidenceError(field, f"{field} is required")
        if len(cleaned) > maximum:
            raise InvalidEvidenceError(field, f"{field} exceeds {maximum} characters")
        return cleaned

    @staticmethod
    def _optional_text(field: str, value: object, maximum: int) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise InvalidEvidenceError(field, f"{field} must be a string")
        cleaned = redact_text(value.strip())
        if len(cleaned) > maximum:
            raise InvalidEvidenceError(field, f"{field} exceeds {maximum} characters")
        return cleaned or None

    @staticmethod
    def _source_member(name: str, content: bytes) -> dict:
        return {
            "name": name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }

    @staticmethod
    def _json_hash(value: object) -> str:
        rendered = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(rendered).hexdigest()

    @staticmethod
    def _digest(field: str, value: str) -> None:
        if not _SHA256.fullmatch(value):
            raise InvalidEvidenceError(field, f"{field} must be a SHA-256 digest")

    @classmethod
    def _manifest_hash(cls, members: list[dict]) -> str:
        return cls._json_hash(members)

    @staticmethod
    def _protocol_projection(protocol: dict) -> dict:
        value = dict(protocol)
        value["artifacts"] = [
            {
                key: item.get(key)
                for key in (
                    "id",
                    "name",
                    "kind",
                    "media_type",
                    "content_hash",
                    "size_bytes",
                    "metadata",
                    "created_at",
                )
            }
            | {"preview": (item.get("content_text") or "")[:4_000]}
            for item in protocol.get("artifacts", [])
        ]
        return value

    @staticmethod
    def _statistics(claims: list[dict]) -> dict:
        claim_types = Counter(item["claim_type"] for item in claims)
        closures = Counter(item["closure_status"] for item in claims)
        uncertainties = Counter(
            item.get("status_axes", {}).get("uncertainty", "unknown")
            for item in claims
        )
        causal = Counter(
            item.get("status_axes", {}).get("causal", "unknown") for item in claims
        )
        promotions = Counter(
            item.get("promotion_stage", ClaimPromotionStage.REGISTERED.value)
            for item in claims
        )
        sources = {source["id"] for item in claims for source in item.get("sources", [])}
        return {
            "claims": len(claims),
            "closed": closures[ClaimClosureStatus.CLOSED.value],
            "blocked": closures[ClaimClosureStatus.BLOCKED.value],
            "sources": len(sources),
            "claim_types": dict(sorted(claim_types.items())),
            "uncertainty_statuses": dict(sorted(uncertainties.items())),
            "causal_statuses": dict(sorted(causal.items())),
            "promotion_stages": dict(sorted(promotions.items())),
        }
