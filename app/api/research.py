"""Research, verification, qualification, and assurance HTTP routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ..auth import current_admin_user
from ..core.contracts import RequestContext
from ..core.errors import InvalidEvidenceError
from ..core.kernel_verification import KernelBackendKind, KernelVerificationDraft
from ..core.qualification import (
    AuthorizationGrantDraft,
    MathTheoremCandidateDraft,
    ValidationModality,
)
from ..core.research import (
    ClaimPromotionStage,
    ClaimRelationDraft,
    ClaimRelationType,
    VerificationAttemptDraft,
    VerificationKind,
    VerificationOutcome,
    VerificationPlanDraft,
)
from ..database import get_repository
from ..services.assurance import AssuranceBundleService
from ..services.decision_lab import DecisionLabService
from ..services.evidence import EvidenceService
from ..services.kernel_verification import KernelVerificationService
from ..services.qualification import QualificationService
from ..services.research_registry import ResearchRegistryService
from ..services.verification_runner import VerificationRunner
from ..tenancy import Tenant, current_tenant

RequestContextFactory = Callable[[Request, Tenant], RequestContext]


class ClaimRelationRequest(BaseModel):
    target_claim_id: str = Field(min_length=1, max_length=100)
    relation_type: ClaimRelationType
    rationale: str = Field(min_length=1, max_length=4_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)


class RelationWithdrawalRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2_000)


class VerificationAttemptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: VerificationKind
    outcome: VerificationOutcome
    validation_modality: ValidationModality = ValidationModality.AGENT_REVIEW
    method: str = Field(min_length=1, max_length=4_000)
    scope: str = Field(min_length=1, max_length=8_000)
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str | None = Field(default=None, max_length=100)
    artifact_ids: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PromotionGateRequest(BaseModel):
    target_stage: ClaimPromotionStage


class VerificationPlanRequest(BaseModel):
    plan_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    name: str = Field(min_length=1, max_length=200)
    kind: VerificationKind
    method: str = Field(min_length=1, max_length=4_000)
    scope: str = Field(min_length=1, max_length=8_000)
    prompt: str = Field(min_length=1, max_length=40_000)
    system: str = Field(
        default=(
            "Act as a careful research verification agent. Use only the supplied claim, "
            "declared sources, and auditable tool results."
        ),
        min_length=1,
        max_length=20_000,
    )
    model: str | None = Field(default=None, max_length=200)
    auto_promote: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class MathTheoremCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=20_000)
    scope: str = Field(min_length=1, max_length=10_000)
    definitions: list[str] = Field(default_factory=list, max_length=100)
    negative_boundaries: list[str] = Field(default_factory=list, max_length=100)
    dependency_claim_ids: list[str] = Field(default_factory=list, max_length=500)
    parent_revision_id: str | None = Field(default=None, max_length=100)


class QualificationEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, max_length=200)


class KernelVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: KernelBackendKind
    declaration_name: str = Field(
        min_length=1,
        max_length=300,
        pattern=r"^[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*$",
    )
    source: str = Field(min_length=1, max_length=1_000_000)


class AuthorizationGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qualification_receipt_id: str = Field(min_length=1, max_length=100)
    actor_id: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=1_000)
    scope: dict[str, Any] = Field(default_factory=dict)
    conditions: dict[str, Any] = Field(default_factory=dict)
    expires_at: str | None = Field(default=None, max_length=100)
    budget: dict[str, Any] = Field(default_factory=dict)
    max_calls: int = Field(default=1, ge=1, le=1_000_000)


async def _read_evidence_upload(
    upload: UploadFile, field: str, maximum: int = 5_000_000
) -> bytes:
    content = await upload.read(maximum + 1)
    if not content:
        raise InvalidEvidenceError(field, f"{field} is empty")
    if len(content) > maximum:
        raise InvalidEvidenceError(field, f"{field} exceeds {maximum} bytes")
    return content


def create_research_router(
    *,
    assurance_bundle_service: AssuranceBundleService,
    decision_lab_service: DecisionLabService,
    evidence_service: EvidenceService,
    kernel_verification_service: KernelVerificationService,
    qualification_service: QualificationService,
    research_registry_service: ResearchRegistryService,
    verification_runner: VerificationRunner,
    request_context_factory: RequestContextFactory,
) -> APIRouter:
    """Build the research control plane router with explicit services."""

    router = APIRouter()

    def admin_context(http_request: Request, user: dict) -> RequestContext:
        return request_context_factory(
            http_request, Tenant(user["tenant_id"], user["tenant_id"])
        )

    @router.get("/api/evidence/protocols")
    async def evidence_protocols(
        http_request: Request,
        profile: str | None = None,
        limit: int = 50,
        tenant: Tenant = Depends(current_tenant),
    ) -> list[dict]:
        return evidence_service.list_protocols(
            request_context_factory(http_request, tenant), profile=profile, limit=limit
        )

    @router.get("/api/evidence/protocols/{protocol_id}")
    async def evidence_protocol_detail(
        protocol_id: str,
        http_request: Request,
        tenant: Tenant = Depends(current_tenant),
    ) -> dict:
        return evidence_service.get_protocol(
            request_context_factory(http_request, tenant), protocol_id
        )

    @router.get("/api/research-registry")
    async def research_registry(
        http_request: Request,
        research_case_id: str | None = None,
        limit: int = 2_000,
        tenant: Tenant = Depends(current_tenant),
    ) -> dict:
        return research_registry_service.dashboard(
            request_context_factory(http_request, tenant),
            research_case_id=research_case_id,
            limit=limit,
        )

    @router.get("/api/qualification/profiles")
    async def qualification_profiles(
        _tenant: Tenant = Depends(current_tenant),
    ) -> list[dict]:
        return qualification_service.list_profiles()

    @router.get("/api/qualification/kernel-verifiers")
    async def kernel_verifiers(
        _tenant: Tenant = Depends(current_tenant),
    ) -> list[dict]:
        return kernel_verification_service.list_backends()

    @router.post(
        "/api/qualification/claims/{claim_id}/kernel-verifications",
        status_code=201,
    )
    async def execute_kernel_verification(
        claim_id: str,
        payload: KernelVerificationRequest,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        result = await kernel_verification_service.verify(
            admin_context(http_request, user),
            claim_id,
            KernelVerificationDraft(
                backend=payload.backend,
                declaration_name=payload.declaration_name,
                source=payload.source,
            ),
        )
        get_repository().write_audit(
            user["tenant_id"],
            "qualification.kernel_verification.execute",
            f"/api/qualification/claims/{claim_id}/kernel-verifications",
            {
                "claim_revision_id": claim_id,
                "backend": payload.backend.value,
                "status": result["status"],
                "run_id": result["run"]["id"],
                "attempt_id": result["verification_attempt"]["id"],
                "certificate_artifact_id": result["certificate_artifact"]["id"],
            },
            user_id=user["id"],
        )
        return result

    @router.post("/api/qualification/math-theorems", status_code=201)
    async def register_math_theorem_candidate(
        payload: MathTheoremCandidateRequest,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        result = qualification_service.register_math_theorem(
            admin_context(http_request, user),
            MathTheoremCandidateDraft(
                claim_key=payload.claim_key,
                name=payload.name,
                statement=payload.statement,
                scope=payload.scope,
                definitions=tuple(payload.definitions),
                negative_boundaries=tuple(payload.negative_boundaries),
                dependency_claim_ids=tuple(payload.dependency_claim_ids),
                parent_revision_id=payload.parent_revision_id,
            ),
        )
        get_repository().write_audit(
            user["tenant_id"],
            "qualification.math_theorem.register",
            "/api/qualification/math-theorems",
            {
                "claim_revision_id": result["claim"]["id"],
                "semantic_hash": result["claim"]["semantic_hash"],
                "storage_admission_only": True,
            },
            user_id=user["id"],
        )
        return result

    @router.post("/api/qualification/claims/{claim_id}/evaluations")
    async def evaluate_claim_qualification(
        claim_id: str,
        payload: QualificationEvaluationRequest,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        result = qualification_service.evaluate(
            admin_context(http_request, user), claim_id, payload.profile_id
        )
        evaluation = result["evaluation"]
        get_repository().write_audit(
            user["tenant_id"],
            "qualification.gate.evaluate",
            f"/api/qualification/claims/{claim_id}/evaluations",
            {
                "claim_revision_id": claim_id,
                "profile_id": payload.profile_id,
                "evaluation_id": evaluation["id"],
                "verdict": evaluation["verdict"],
                "evidence_closure_hash": evaluation["evidence_closure_hash"],
            },
            user_id=user["id"],
        )
        return result

    @router.get("/api/qualification/receipts/{receipt_id}")
    async def qualification_receipt(
        receipt_id: str,
        http_request: Request,
        tenant: Tenant = Depends(current_tenant),
    ) -> dict:
        return qualification_service.get_receipt(
            request_context_factory(http_request, tenant), receipt_id
        )

    @router.get("/api/qualification/search")
    async def qualified_search(
        q: str,
        profile: str,
        http_request: Request,
        limit: int = 20,
        tenant: Tenant = Depends(current_tenant),
    ) -> list[dict]:
        return qualification_service.qualified_search(
            request_context_factory(http_request, tenant),
            q,
            profile,
            max(1, min(limit, 100)),
        )

    @router.post("/api/authorization-grants", status_code=201)
    async def create_authorization_grant(
        payload: AuthorizationGrantRequest,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        grant = qualification_service.create_authorization(
            admin_context(http_request, user),
            payload.actor_id,
            AuthorizationGrantDraft(
                qualification_receipt_id=payload.qualification_receipt_id,
                action=payload.action,
                target=payload.target,
                scope=payload.scope,
                conditions=payload.conditions,
                expires_at=payload.expires_at,
                budget=payload.budget,
                max_calls=payload.max_calls,
            ),
        )
        get_repository().write_audit(
            user["tenant_id"],
            "authorization.grant.create",
            "/api/authorization-grants",
            {
                "grant_id": grant["id"],
                "qualification_receipt_id": grant["qualification_receipt_id"],
                "actor_id": grant["actor_id"],
                "action": grant["action"],
                "target": grant["target"],
            },
            user_id=user["id"],
        )
        return grant

    @router.get("/api/research-registry/claims/{claim_id}")
    async def research_claim_detail(
        claim_id: str,
        http_request: Request,
        tenant: Tenant = Depends(current_tenant),
    ) -> dict:
        return research_registry_service.get_claim(
            request_context_factory(http_request, tenant), claim_id
        )

    @router.get(
        "/api/research-registry/cases/{research_case_id}/assurance-bundle"
    )
    async def export_research_assurance_bundle(
        research_case_id: str,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> Response:
        archive, manifest = assurance_bundle_service.export_zip(
            admin_context(http_request, user), research_case_id
        )
        get_repository().write_audit(
            user["tenant_id"],
            "research.assurance_bundle.export",
            f"/api/research-registry/cases/{research_case_id}/assurance-bundle",
            {
                "research_case_id": research_case_id,
                "bundle_digest": manifest["bundle_digest"],
                "member_count": len(manifest["members"]),
            },
            user_id=user["id"],
        )
        return Response(
            archive,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="assurance-{research_case_id}.zip"'
                ),
                "X-Assurance-Bundle-Digest": manifest["bundle_digest"],
            },
        )

    @router.post(
        "/api/research-registry/claims/{claim_id}/relations", status_code=201
    )
    async def create_research_claim_relation(
        claim_id: str,
        payload: ClaimRelationRequest,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        relation = research_registry_service.create_relation(
            admin_context(http_request, user),
            claim_id,
            ClaimRelationDraft(
                target_claim_id=payload.target_claim_id,
                relation_type=payload.relation_type,
                rationale=payload.rationale,
                evidence_refs=tuple(payload.evidence_refs),
            ),
        )
        get_repository().write_audit(
            user["tenant_id"],
            "research.claim_relation.create",
            f"/api/research-registry/claims/{claim_id}/relations",
            {
                "claim_id": claim_id,
                "target_claim_id": relation["target_claim_id"],
                "relation_type": relation["relation_type"],
            },
            user_id=user["id"],
        )
        return relation

    @router.post(
        "/api/research-registry/claims/{claim_id}/relations/{relation_id}/withdraw"
    )
    async def withdraw_research_claim_relation(
        claim_id: str,
        relation_id: str,
        payload: RelationWithdrawalRequest,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        relation = research_registry_service.withdraw_relation(
            admin_context(http_request, user),
            claim_id,
            relation_id,
            reason=payload.reason,
        )
        get_repository().write_audit(
            user["tenant_id"],
            "research.claim_relation.withdraw",
            f"/api/research-registry/claims/{claim_id}/relations/{relation_id}/withdraw",
            {
                "claim_id": claim_id,
                "relation_id": relation_id,
                "relation_type": relation["relation_type"],
            },
            user_id=user["id"],
        )
        return relation

    @router.post(
        "/api/research-registry/claims/{claim_id}/verification-attempts",
        status_code=201,
    )
    async def record_research_verification_attempt(
        claim_id: str,
        payload: VerificationAttemptRequest,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        attempt = research_registry_service.record_verification_attempt(
            admin_context(http_request, user),
            claim_id,
            VerificationAttemptDraft(
                kind=payload.kind,
                outcome=payload.outcome,
                validation_modality=payload.validation_modality,
                method=payload.method,
                scope=payload.scope,
                input_digest=payload.input_digest,
                output_digest=payload.output_digest,
                run_id=payload.run_id,
                artifact_ids=tuple(payload.artifact_ids),
                metadata=payload.metadata,
            ),
        )
        get_repository().write_audit(
            user["tenant_id"],
            "research.verification_attempt.record",
            f"/api/research-registry/claims/{claim_id}/verification-attempts",
            {
                "claim_id": claim_id,
                "attempt_id": attempt["id"],
                "kind": attempt["kind"],
                "outcome": attempt["outcome"],
                "independent": attempt["independent"],
                "independence": attempt["independence"],
            },
            user_id=user["id"],
        )
        return attempt

    @router.post(
        "/api/research-registry/claims/{claim_id}/verification-plans",
        status_code=201,
    )
    async def create_research_verification_plan(
        claim_id: str,
        payload: VerificationPlanRequest,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        plan = verification_runner.create_plan(
            admin_context(http_request, user),
            claim_id,
            VerificationPlanDraft(
                plan_key=payload.plan_key,
                name=payload.name,
                kind=payload.kind,
                method=payload.method,
                scope=payload.scope,
                prompt=payload.prompt,
                system=payload.system,
                model=payload.model,
                auto_promote=payload.auto_promote,
                metadata=payload.metadata,
            ),
        )
        get_repository().write_audit(
            user["tenant_id"],
            "research.verification_plan.create",
            f"/api/research-registry/claims/{claim_id}/verification-plans",
            {
                "claim_id": claim_id,
                "plan_id": plan["id"],
                "plan_key": plan["plan_key"],
                "version": plan["version"],
            },
            user_id=user["id"],
        )
        return plan

    @router.post("/api/research-registry/verification-plans/{plan_id}/runs")
    async def run_research_verification_plan(
        plan_id: str,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        result = await verification_runner.execute(
            admin_context(http_request, user), plan_id
        )
        execution = result["execution"]
        get_repository().write_audit(
            user["tenant_id"],
            "research.verification_plan.execute",
            f"/api/research-registry/verification-plans/{plan_id}/runs",
            {
                "plan_id": plan_id,
                "execution_id": execution["id"],
                "run_id": execution["run_id"],
                "attempt_id": execution["attempt_id"],
                "outcome": execution["outcome"],
            },
            user_id=user["id"],
        )
        return result

    @router.post("/api/research-registry/claims/{claim_id}/promotion-gates")
    async def evaluate_research_promotion_gate(
        claim_id: str,
        payload: PromotionGateRequest,
        http_request: Request,
        user: dict = Depends(current_admin_user),
    ) -> dict:
        result = research_registry_service.evaluate_promotion(
            admin_context(http_request, user), claim_id, payload.target_stage
        )
        evaluation = result["evaluation"]
        get_repository().write_audit(
            user["tenant_id"],
            "research.promotion_gate.evaluate",
            f"/api/research-registry/claims/{claim_id}/promotion-gates",
            {
                "claim_id": claim_id,
                "evaluation_id": evaluation["id"],
                "from_stage": evaluation["from_stage"],
                "target_stage": evaluation["target_stage"],
                "decision": evaluation["decision"],
                "blockers": evaluation["blockers"],
            },
            user_id=user["id"],
        )
        return result

    @router.get("/api/decision-lab")
    async def decision_lab(
        http_request: Request,
        protocol_id: str | None = None,
        family: str | None = None,
        resolution: str | None = None,
        limit: int = 500,
        evaluation: bool = False,
        tenant: Tenant = Depends(current_tenant),
    ) -> dict:
        return decision_lab_service.dashboard(
            request_context_factory(http_request, tenant),
            protocol_id=protocol_id,
            family=family,
            resolution=resolution,
            limit=limit,
            include_gold=evaluation,
        )

    @router.get("/api/decision-lab/cases/{case_id}")
    async def decision_case_detail(
        case_id: str,
        http_request: Request,
        evaluation: bool = False,
        tenant: Tenant = Depends(current_tenant),
    ) -> dict:
        return decision_lab_service.get_case(
            request_context_factory(http_request, tenant),
            case_id,
            include_gold=evaluation,
        )

    @router.post("/api/research-registry/import/frontier", status_code=201)
    async def import_frontier_registry(
        http_request: Request,
        source_name: str = Form(default="AI Frontier Claim Registry", max_length=200),
        registry_file: UploadFile = File(...),
        source_ledger_file: UploadFile | None = File(default=None),
        user: dict = Depends(current_admin_user),
    ) -> dict:
        result = research_registry_service.import_frontier(
            admin_context(http_request, user),
            source_name=source_name,
            registry_bytes=await _read_evidence_upload(
                registry_file, "registry_file", 1_000_000
            ),
            source_ledger_bytes=(
                await _read_evidence_upload(
                    source_ledger_file, "source_ledger_file", 750_000
                )
                if source_ledger_file is not None
                else None
            ),
        )
        research_case = result.get("case") or {}
        get_repository().write_audit(
            user["tenant_id"],
            "research.frontier.import",
            "/api/research-registry/import/frontier",
            {
                "research_case_id": research_case.get("id"),
                "registry_id": research_case.get("registry_id"),
                "registry_version": research_case.get("registry_version"),
                "imported": result.get("imported", False),
                "claim_count": result.get("statistics", {}).get("claims", 0),
            },
            user_id=user["id"],
        )
        return result

    @router.post("/api/decision-lab/import/nanojev", status_code=201)
    async def import_nanojev_bundle(
        http_request: Request,
        source_name: str = Form(default="NanoJev evaluation", max_length=200),
        confidence_threshold: float = Form(default=0.7, ge=0, le=1),
        request_file: UploadFile = File(...),
        predictions_file: UploadFile = File(...),
        metrics_file: UploadFile = File(...),
        receipt_file: UploadFile | None = File(default=None),
        user: dict = Depends(current_admin_user),
    ) -> dict:
        result = decision_lab_service.import_nanojev(
            admin_context(http_request, user),
            source_name=source_name,
            confidence_threshold=confidence_threshold,
            request_bytes=await _read_evidence_upload(request_file, "request_file"),
            predictions_bytes=await _read_evidence_upload(
                predictions_file, "predictions_file"
            ),
            metrics_bytes=await _read_evidence_upload(metrics_file, "metrics_file"),
            receipt_bytes=(
                await _read_evidence_upload(receipt_file, "receipt_file", 1_000_000)
                if receipt_file is not None
                else None
            ),
        )
        protocol = result.get("protocol") or {}
        get_repository().write_audit(
            user["tenant_id"],
            "decision.nanojev.import",
            "/api/decision-lab/import/nanojev",
            {
                "protocol_id": protocol.get("id"),
                "imported": result.get("imported", False),
                "case_count": result.get("statistics", {}).get("cases", 0),
            },
            user_id=user["id"],
        )
        return result

    return router
