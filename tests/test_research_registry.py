import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import database, main
from app.core.artifacts import ArtifactDraft, ArtifactKind
from app.core.contracts import RequestContext
from app.core.errors import InvalidEvidenceError, ResourceNotFoundError
from app.core.qualification import MathTheoremCandidateDraft, ValidationModality
from app.core.research import (
    ClaimPromotionStage,
    ClaimRelationDraft,
    ClaimRelationType,
    VerificationAttemptDraft,
    VerificationKind,
    VerificationOutcome,
)
from app.repository import SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.evidence import EvidenceService
from app.services.qualification import QualificationService
from app.services.research_registry import ResearchRegistryService
from app.tenancy import Tenant


def _registry() -> bytes:
    payload = {
        "registry_id": "frontier-smoke",
        "registry_version": "0.1.0",
        "scope": {
            "as_of_date": "2026-09-28",
            "authority": "research_only",
            "causal_default": "NOT_ESTABLISHED",
        },
        "claim_contract": {
            "required_fields": [
                "claim_id",
                "statement",
                "claim_type",
                "scope",
                "source_refs",
                "calculation_version",
                "uncertainty_status",
                "causal_status",
                "revision_status",
            ],
            "claim_types": [
                "OBSERVATION",
                "DERIVED_MEASURE",
                "COMPARATIVE_FINDING",
                "CONDITIONAL_SYSTEM_HYPOTHESIS",
                "RESEARCH_CONCEPT",
            ],
        },
        "claims": [
            {
                "claim_id": "CLM-CLOSED",
                "statement": "A protocol-bound observation exists.",
                "claim_type": "OBSERVATION",
                "scope": "Smoke-test snapshot only.",
                "source_refs": [
                    {
                        "source_id": "SRC-ONE",
                        "locator": "data/source.csv!row=1",
                        "sha256": "a" * 64,
                    }
                ],
                "calculation_version": "extract-v1",
                "uncertainty_status": "observed",
                "causal_status": "NOT_ESTABLISHED",
                "revision_status": "current",
            },
            {
                "claim_id": "CLM-BLOCKED",
                "statement": "A source-pending hypothesis exists.",
                "claim_type": "CONDITIONAL_SYSTEM_HYPOTHESIS",
                "scope": "Method-design input only.",
                "source_refs": [],
                "calculation_version": "hypothesis-v1",
                "uncertainty_status": "mapping_pending",
                "causal_status": "CAUSAL_EVIDENCE_PENDING",
                "revision_status": "source_pending",
            },
        ],
    }
    return json.dumps(payload).encode()


def _service(repository: SQLiteRepository) -> ResearchRegistryService:
    provider = lambda: repository  # noqa: E731
    return ResearchRegistryService(
        provider,
        artifact_service=ArtifactService(repository_provider=provider),
        evidence_service=EvidenceService(provider),
    )


def test_read_only_explorer_spans_profiles_without_promoting_candidates(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "research-explorer.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    service = _service(repository)
    qualification = QualificationService(provider)
    context = RequestContext(request_id="explorer", workspace_id="workspace-a")
    frontier = service.import_frontier(
        context, source_name="Frontier fixture", registry_bytes=_registry()
    )
    theorem = qualification.register_math_theorem(
        context,
        MathTheoremCandidateDraft(
            claim_key="THM-EXPLORER",
            name="Theorem fixture",
            statement="For every integer n, n equals n.",
            scope="Integers.",
        ),
    )["claim"]

    assert [item["profile"] for item in service.dashboard(context)["cases"]] == [
        "research.frontier"
    ]
    explorer = service.explorer(context)
    assert {item["profile"] for item in explorer["cases"]} == {
        "research.frontier", "math.theorem"
    }
    theorem_case = repository.get_research_case(
        context.workspace_id, theorem["research_case_id"]
    )
    selected = service.explorer(context, research_case_id=theorem_case["id"])
    assert [item["id"] for item in selected["claims"]] == [theorem["id"]]
    assert selected["case"]["profile"] == "math.theorem"
    assert selected["claims"][0]["semantic_hash"] == theorem["semantic_hash"]
    assert qualification.claim_status(context, theorem["id"])["receipts"] == []
    assert frontier["case"]["id"] != theorem_case["id"]

    other = RequestContext(request_id="other", workspace_id="workspace-b")
    assert service.explorer(other)["cases"] == []
    with pytest.raises(ResourceNotFoundError):
        service.explorer(other, research_case_id=theorem_case["id"])


def test_frontier_import_builds_searchable_claim_registry(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "research.db")
    repository.init()
    service = _service(repository)
    context = RequestContext(request_id="request-1", workspace_id="workspace-a")

    result = service.import_frontier(
        context,
        source_name="Frontier smoke",
        registry_bytes=_registry(),
        source_ledger_bytes=b"# Source ledger\n\nBounded fixture.\n",
    )

    assert result["imported"] is True
    assert result["case"]["registry_version"] == "0.1.0"
    assert result["protocol"]["status"] == "frozen"
    assert result["statistics"] == {
        "claims": 2,
        "closed": 1,
        "blocked": 1,
        "sources": 1,
        "claim_types": {
            "CONDITIONAL_SYSTEM_HYPOTHESIS": 1,
            "OBSERVATION": 1,
        },
        "uncertainty_statuses": {"mapping_pending": 1, "observed": 1},
        "causal_statuses": {
            "CAUSAL_EVIDENCE_PENDING": 1,
            "NOT_ESTABLISHED": 1,
        },
        "promotion_stages": {"registered": 2},
    }
    claims = {item["claim_key"]: item for item in result["claims"]}
    assert claims["CLM-CLOSED"]["closure_status"] == "closed"
    assert claims["CLM-CLOSED"]["sources"][0]["source_key"] == "SRC-ONE"
    assert claims["CLM-BLOCKED"]["blockers"] == [
        "source_refs_missing",
        "revision_status:source_pending",
    ]
    assert len(result["protocol"]["artifacts"]) == 2
    assert result["protocol"]["receipts"][0]["reviews"][0]["independent"] is False
    assert "research_claim" in {
        item["kind"] for item in repository.search_memory("workspace-a", "source-pending", 20)
    }

    repeated = service.import_frontier(
        context,
        source_name="Duplicate name is ignored",
        registry_bytes=_registry(),
        source_ledger_bytes=b"# Source ledger\n\nBounded fixture.\n",
    )
    assert repeated["imported"] is False
    assert repeated["case"]["id"] == result["case"]["id"]


def test_frontier_import_rejects_invalid_hash_before_writing(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "invalid-research.db")
    repository.init()
    service = _service(repository)
    context = RequestContext(request_id="request-1", workspace_id="workspace-a")
    payload = json.loads(_registry())
    payload["claims"][0]["source_refs"][0]["sha256"] = "not-a-digest"

    with pytest.raises(InvalidEvidenceError):
        service.import_frontier(
            context,
            source_name="Invalid registry",
            registry_bytes=json.dumps(payload).encode(),
        )

    assert repository.list_research_cases("workspace-a") == []
    assert repository.list_evidence_protocols("workspace-a") == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("required_fields", [["claim_id"]]),
        ("claim_types", [{"type": "OBSERVATION"}]),
    ],
)
def test_frontier_import_rejects_non_string_contract_entries(
    tmp_path, field, value
) -> None:
    repository = SQLiteRepository(tmp_path / f"invalid-{field}.db")
    repository.init()
    service = _service(repository)
    context = RequestContext(request_id="request-1", workspace_id="workspace-a")
    payload = json.loads(_registry())
    payload["claim_contract"][field] = value

    with pytest.raises(InvalidEvidenceError):
        service.import_frontier(
            context,
            source_name="Invalid registry",
            registry_bytes=json.dumps(payload).encode(),
        )

    assert repository.list_research_cases("workspace-a") == []
    assert repository.list_evidence_protocols("workspace-a") == []


def test_claim_relations_verification_and_fail_closed_promotion(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "research-gates.db")
    repository.init()
    service = _service(repository)
    context = RequestContext(
        request_id="request-1",
        workspace_id="workspace-a",
        principal_id="admin-a",
    )
    imported = service.import_frontier(
        context,
        source_name="Promotion fixture",
        registry_bytes=_registry(),
    )
    claims = {item["claim_key"]: item for item in imported["claims"]}
    closed = claims["CLM-CLOSED"]
    blocked = claims["CLM-BLOCKED"]

    with pytest.raises(InvalidEvidenceError, match="next allowed stage"):
        service.evaluate_promotion(
            context, closed["id"], ClaimPromotionStage.REVIEW_READY
        )
    first_gate = service.evaluate_promotion(
        context, closed["id"], ClaimPromotionStage.EVIDENCE_READY
    )
    assert first_gate["evaluation"]["decision"] == "blocked"
    assert first_gate["evaluation"]["blockers"] == [
        "passed_verification_attempt_present"
    ]
    assert first_gate["claim"]["promotion_stage"] == "registered"

    artifact = ArtifactService(repository_provider=lambda: repository).create_artifact(
        context,
        ArtifactDraft(
            name="verification-result.json",
            kind=ArtifactKind.JSON,
            media_type="application/json",
            content_text='{"verified": true}',
        ),
    )
    attempt = service.record_verification_attempt(
        context,
        closed["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.REPRODUCTION,
            outcome=VerificationOutcome.PASSED,
            method="Recompute the declared observation with the frozen fixture.",
            scope="Fixture-bound reproduction only.",
            input_digest="1" * 64,
            output_digest="2" * 64,
            artifact_ids=(artifact["id"],),
        ),
    )
    assert attempt["receipt"]["artifact_ids"] == [artifact["id"]]
    promoted = service.evaluate_promotion(
        context,
        closed["id"],
        ClaimPromotionStage.EVIDENCE_READY,
        apply_transition=True,
    )
    assert promoted["evaluation"]["decision"] == "passed"
    assert promoted["transition_applied"] is True
    assert promoted["claim"]["promotion_stage"] == "evidence_ready"

    review_gate = service.evaluate_promotion(
        context, closed["id"], ClaimPromotionStage.REVIEW_READY
    )
    assert review_gate["evaluation"]["blockers"] == [
        "qualified_independence_passed_attempt_present"
    ]
    ignored_declaration = service.record_verification_attempt(
        context,
        closed["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.REVIEW,
            outcome=VerificationOutcome.PASSED,
            method="A client attempts to declare its own independence.",
            scope="Fixture only.",
            input_digest="3" * 64,
            output_digest="4" * 64,
            independent=True,
            artifact_ids=(artifact["id"],),
        ),
    )
    assert ignored_declaration["independent"] is False
    assert ignored_declaration["independence"]["derived"] is True
    kernel_certificate = ArtifactService(repository_provider=lambda: repository).create_artifact(
        context,
        ArtifactDraft(
            name="orthogonal-checker.json",
            kind=ArtifactKind.JSON,
            media_type="application/json",
            content_text=json.dumps({"checker": {"executable_hash": "a" * 64}}, sort_keys=True),
            metadata={"role": "kernel_certificate"},
        ),
    )
    verifier_context = RequestContext(
        request_id="orthogonal-checker",
        workspace_id=context.workspace_id,
        principal_id="system:verifier:fixture-kernel",
    )
    orthogonal_attempt = service.record_verification_attempt(
        verifier_context,
        closed["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.REVIEW,
            outcome=VerificationOutcome.PASSED,
            validation_modality=ValidationModality.KERNEL_CHECK,
            method="Replay with an orthogonal non-model checker.",
            scope="The exact frozen fixture.",
            input_digest="5" * 64,
            output_digest=kernel_certificate["content_hash"],
            artifact_ids=(kernel_certificate["id"],),
        ),
    )
    reviewed = service.evaluate_promotion(context, closed["id"], ClaimPromotionStage.REVIEW_READY)
    assert orthogonal_attempt["independent"] is False
    assert orthogonal_attempt["independence"]["verification_properties"] == [
        "orthogonal_non_llm_checker"
    ]
    assert reviewed["evaluation"]["decision"] == "blocked"
    assert reviewed["evaluation"]["blockers"] == [
        "qualified_independence_passed_attempt_present"
    ]
    assert reviewed["claim"]["promotion_stage"] == "evidence_ready"

    dependency = service.create_relation(
        context,
        closed["id"],
        ClaimRelationDraft(
            target_claim_id=blocked["id"],
            relation_type=ClaimRelationType.DEPENDS_ON,
            rationale="The observation depends on the unresolved mapping hypothesis.",
        ),
    )
    assert dependency["relation_type"] == "depends_on"
    with pytest.raises(InvalidEvidenceError, match="cycle"):
        service.create_relation(
            context,
            blocked["id"],
            ClaimRelationDraft(
                target_claim_id=closed["id"],
                relation_type=ClaimRelationType.DEPENDS_ON,
                rationale="This reverse edge must fail closed.",
            ),
        )
    with pytest.raises(InvalidEvidenceError, match="next allowed stage"):
        service.evaluate_promotion(
            context, closed["id"], ClaimPromotionStage.RELEASE_READY
        )
    withdrawn = service.withdraw_relation(
        context,
        closed["id"],
        dependency["id"],
        reason="The dependency was registered against the wrong revision.",
    )
    assert withdrawn["status"] == "withdrawn"
    detail = service.get_claim(context, closed["id"])
    assert len(detail["verification_attempts"]) == 3
    assert len(detail["promotion_evaluations"]) == 4
    assert detail["relations"][0]["target_claim_key"] == "CLM-BLOCKED"


def test_research_registry_http_import_is_workspace_scoped(tmp_path, monkeypatch) -> None:
    def connect_to_test_db():
        import sqlite3

        connection = sqlite3.connect(tmp_path / "research-api.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    user = {
        "id": "admin-a",
        "tenant_id": "workspace-a",
        "email": "admin@example.test",
        "name": "Admin",
        "role": "admin",
    }
    main.app.dependency_overrides[main.current_admin_user] = lambda: user
    main.app.dependency_overrides[main.current_tenant] = lambda: Tenant(
        "workspace-a", "Workspace A"
    )
    try:
        with TestClient(main.app) as client:
            imported = client.post(
                "/api/research-registry/import/frontier",
                data={"source_name": "Frontier API"},
                files={
                    "registry_file": (
                        "claim-registry.json",
                        _registry(),
                        "application/json",
                    )
                },
            )
            listed = client.get("/api/research-registry")
            assert imported.status_code == 201
            assert imported.json()["statistics"]["claims"] == 2
            assert listed.status_code == 200
            assert listed.json()["case"]["name"] == "Frontier API"
            importers = client.get("/api/research/importers")
            assert importers.status_code == 200
            assert {item["importer_id"] for item in importers.json()} == {
                "frontier.registry", "openai.math", "rime.event-anchored-consumer"
            }
            assert client.get("/api/research/importers/openai.math/1").json()[
                "target_surface"
            ] == "catalogue_candidate_only"
            assert client.get("/api/research/importers/openai.math/2").status_code == 422
            theorem = main.qualification_service.register_math_theorem(
                RequestContext("api-explorer", "workspace-a", "admin-a"),
                MathTheoremCandidateDraft(
                    claim_key="THM-API-EXPLORER",
                    name="API theorem candidate",
                    statement="For all integers n, n equals n.",
                    scope="Integers.",
                ),
            )["claim"]
            explorer = client.get("/api/research/explorer")
            assert explorer.status_code == 200
            assert {item["profile"] for item in explorer.json()["cases"]} == {
                "research.frontier", "math.theorem"
            }
            exact = client.get(
                "/api/research/explorer",
                params={"research_case_id": theorem["research_case_id"]},
            )
            assert exact.status_code == 200
            assert exact.json()["claims"][0]["id"] == theorem["id"]
            assert client.get("/api/research-registry").json()["case"]["profile"] == (
                "research.frontier"
            )
            exported = client.get(
                f"/api/research-registry/cases/{imported.json()['case']['id']}/assurance-bundle"
            )
            assert exported.status_code == 200
            assert exported.headers["content-type"] == "application/zip"
            assert len(exported.headers["x-assurance-bundle-digest"]) == 64
            with zipfile.ZipFile(io.BytesIO(exported.content)) as bundle:
                assert {
                    "manifest.json",
                    "claims.json",
                    "verification.json",
                    "signatures/status.json",
                } <= set(bundle.namelist())

            claim_id = listed.json()["claims"][0]["id"]
            assert client.get(f"/api/research-registry/claims/{claim_id}").status_code == 200
            rejected_independence = client.post(
                f"/api/research-registry/claims/{claim_id}/verification-attempts",
                json={
                    "kind": "review",
                    "outcome": "passed",
                    "validation_modality": "expert_review",
                    "method": "Client-declared independence must be rejected.",
                    "scope": "Transport contract only.",
                    "input_digest": "1" * 64,
                    "output_digest": "2" * 64,
                    "independent": True,
                },
            )
            assert rejected_independence.status_code == 422

            plan_response = client.post(
                f"/api/research-registry/claims/{claim_id}/verification-plans",
                json={
                    "plan_key": "api-source-check",
                    "name": "API source check",
                    "kind": "source_audit",
                    "method": "Inspect the frozen source declaration.",
                    "scope": "Imported fixture only.",
                    "prompt": "Check the claim and report a bounded result.",
                    "auto_promote": True,
                },
            )
            assert plan_response.status_code == 201
            assert plan_response.json()["version"] == 1
            scheduled = client.post(
                "/api/schedules",
                json={
                    "name": "Daily claim verification",
                    "target": "research.verify",
                    "payload": {"plan_id": plan_response.json()["id"]},
                    "cadence": "daily",
                    "run_at": "2030-01-01T09:00:00+08:00",
                },
            )
            assert scheduled.status_code == 201
            assert scheduled.json()["target"] == "research.verify"
            detail = client.get(f"/api/research-registry/claims/{claim_id}")
            assert detail.json()["verification_plans"][0]["id"] == plan_response.json()["id"]

            claims = listed.json()["claims"]
            source = next(item for item in claims if item["claim_key"] == "CLM-CLOSED")
            target = next(item for item in claims if item["claim_key"] == "CLM-BLOCKED")
            related = client.post(
                f"/api/research-registry/claims/{source['id']}/relations",
                json={
                    "target_claim_id": target["id"],
                    "relation_type": "qualifies",
                    "rationale": "The pending hypothesis limits interpretation.",
                },
            )
            gated = client.post(
                f"/api/research-registry/claims/{source['id']}/promotion-gates",
                json={"target_stage": "evidence_ready"},
            )
            assert related.status_code == 201
            assert gated.status_code == 200
            assert gated.json()["evaluation"]["decision"] == "blocked"
            withdrawn = client.post(
                f"/api/research-registry/claims/{source['id']}/relations/"
                f"{related.json()['id']}/withdraw",
                json={"reason": "HTTP lifecycle regression fixture."},
            )
            assert withdrawn.status_code == 200
            assert withdrawn.json()["status"] == "withdrawn"

            main.app.dependency_overrides[main.current_tenant] = lambda: Tenant(
                "workspace-b", "Workspace B"
            )
            isolated = client.get("/api/research-registry")
            assert isolated.status_code == 200
            assert isolated.json()["case"] is None
            assert isolated.json()["claims"] == []
    finally:
        main.app.dependency_overrides.clear()
