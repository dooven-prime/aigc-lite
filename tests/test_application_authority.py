"""Final authority services must not trust caller-constructed identity fields."""

from __future__ import annotations

import secrets
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import pytest
from authority_support import provision_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import auth, main
from app.api import research as research_api
from app.core.authority import ADMIN_AUTHORITY_SCOPES, AuthorityContext
from app.core.contracts import RequestContext
from app.core.errors import ApplicationError, AuthorityDeniedError, ResourceNotFoundError
from app.core.invalidation import (
    InvalidationDecisionDraft,
    InvalidationReason,
    InvalidationScope,
    NoticeVerificationDraft,
)
from app.core.qualification import AuthorizationGrantDraft, KnowledgeAdmissionDraft
from app.core.research import ClaimPromotionStage
from app.repository import SQLiteRepository
from app.services.invalidation import InvalidationService
from app.services.qualification import QualificationService
from app.services.research_registry import ResearchRegistryService
from app.tenancy import Tenant


def _operations(repository):
    qualification = QualificationService(lambda: repository)
    invalidation = InvalidationService(
        lambda: repository,
        source_fetcher=lambda _url: b"# Pinned synthetic withdrawal notice\n",
    )
    research = ResearchRegistryService(lambda: repository)
    return {
        "admit": lambda context, authority: qualification.admit_knowledge(
            context,
            KnowledgeAdmissionDraft("missing-receipt", "knowledge.default.v1", "Review."),
            authority=authority,
        ),
        "grant": lambda context, authority: qualification.create_authorization(
            context,
            "runtime:publisher",
            AuthorizationGrantDraft("missing-receipt", "publish", "target", {}),
            authority=authority,
        ),
        "verify_notice": lambda context, authority: invalidation.verify_notice(
            context,
            NoticeVerificationDraft("a" * 40),
            authority=authority,
        ),
        "invalidate": lambda context, authority: invalidation.decide(
            context,
            InvalidationDecisionDraft(
                notice_verification_id="missing-verification",
                target_claim_revision_id="missing-revision",
                target_claim_semantic_hash="a" * 64,
                target_scope=InvalidationScope.CLAIM_REVISION,
                reason_code=InvalidationReason.WITHDRAWN_UPSTREAM_CONSTRUCTION,
                rationale="Synthetic hostile attempt.",
            ),
            authority=authority,
        ),
        "promote": lambda context, authority: research.evaluate_promotion(
            context,
            "missing-revision",
            ClaimPromotionStage.EVIDENCE_READY,
            apply_transition=True,
            authority=authority,
        ),
    }


@pytest.mark.parametrize(
    "tamper",
    (
        "missing_authority",
        "forged_hash",
        "wrong_workspace",
        "wrong_principal",
        "scope_missing",
        "member_session",
        "expired_session",
        "revoked_session",
        "demoted_admin",
        "disabled_admin",
    ),
)
def test_final_authority_operations_fail_closed_before_write(tmp_path, tamper: str) -> None:
    repository = SQLiteRepository(tmp_path / f"{tamper}.db")
    repository.init()
    context, authority = provision_admin(
        repository,
        RequestContext(
            request_id="hostile-service-call",
            workspace_id="authority-workspace",
            principal_id="forged-admin",
            scopes=frozenset(ADMIN_AUTHORITY_SCOPES),  # Catalog scopes are not authority.
        ),
    )
    if tamper == "missing_authority":
        authority = None
    elif tamper == "forged_hash":
        authority = replace(authority, session_hash="0" * 64)
    elif tamper == "wrong_workspace":
        authority = replace(authority, workspace_id="another-workspace")
    elif tamper == "wrong_principal":
        authority = replace(authority, principal_id="another-user")
    elif tamper == "scope_missing":
        authority = replace(authority, scopes=frozenset())
    elif tamper == "member_session":
        member = repository.create_user(
            context.workspace_id,
            "member@example.test",
            "Member",
            "unused-test-password-hash",
            role="member",
        )
        member_hash = secrets.token_hex(32)
        repository.save_auth_session(
            member_hash,
            member["id"],
            context.workspace_id,
            (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        )
        context = replace(context, principal_id=member["id"])
        authority = AuthorityContext(
            context.workspace_id, member["id"], member_hash, ADMIN_AUTHORITY_SCOPES
        )
    elif tamper == "expired_session":
        expired_hash = secrets.token_hex(32)
        repository.save_auth_session(
            expired_hash,
            context.principal_id,
            context.workspace_id,
            (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        )
        authority = replace(authority, session_hash=expired_hash)
    elif tamper == "revoked_session":
        assert repository.revoke_auth_session(authority.session_hash, context.principal_id)
    elif tamper in {"demoted_admin", "disabled_admin"}:
        column, value = (
            ("role", "member") if tamper == "demoted_admin" else ("is_active", 0)
        )
        with repository._connect() as connection:
            connection.execute(
                f"UPDATE users SET {column} = ? WHERE id = ?",
                (value, context.principal_id),
            )

    for operation in _operations(repository).values():
        with pytest.raises(AuthorityDeniedError):
            operation(context, authority)
    with repository._connect() as connection:
        for table in (
            "knowledge_admission_receipts",
            "authorization_grants",
            "notice_verifications",
            "invalidation_decisions",
            "research_promotion_evaluations",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_valid_admin_reaches_domain_checks_and_authority_stays_out_of_run_context(tmp_path):
    repository = SQLiteRepository(tmp_path / "valid-admin.db")
    repository.init()
    context, authority = provision_admin(
        repository, RequestContext("valid-admin", "authority-workspace", "candidate")
    )
    assert authority.session_hash not in repr(authority)
    assert "session_hash" not in asdict(context)
    assert "authority" not in asdict(context)
    http_request = Request({"type": "http", "method": "POST", "path": "/api/chat", "headers": []})
    http_request.state.auth_token_hash = authority.session_hash
    http_request.state.user_id = context.principal_id
    http_request.state.scopes = frozenset({"tools:write"})
    http_request.state.authority_scopes = ADMIN_AUTHORITY_SCOPES
    ordinary_context = main.request_context(
        http_request, Tenant(context.workspace_id, context.workspace_id)
    )
    assert "session_hash" not in asdict(ordinary_context)
    assert "authority" not in asdict(ordinary_context)
    assert not ordinary_context.scopes.intersection(ADMIN_AUTHORITY_SCOPES)
    for name, operation in _operations(repository).items():
        if name == "verify_notice":
            verified = operation(context, authority)
            assert verified["source_commit"] == "a" * 40
            assert authority.session_hash not in str(verified)
        else:
            with pytest.raises(ResourceNotFoundError):
                operation(context, authority)


def test_http_final_authority_requires_session_even_if_admin_dependency_is_overridden(
    tmp_path, monkeypatch
):
    repository = SQLiteRepository(tmp_path / "authority-http.db")
    repository.init()
    context, _authority = provision_admin(
        repository, RequestContext("authority-http", "workspace-http", "candidate")
    )
    app = FastAPI()
    app.add_exception_handler(ApplicationError, main.application_error_handler)
    app.include_router(
        research_api.create_research_router(
            assurance_bundle_service=None,
            decision_lab_service=None,
            evidence_service=None,
            kernel_verification_service=None,
            invalidation_service=InvalidationService(lambda: repository),
            qualification_service=QualificationService(lambda: repository),
            research_registry_service=ResearchRegistryService(lambda: repository),
            verification_runner=None,
            request_context_factory=main.request_context,
        )
    )
    monkeypatch.setattr(auth, "get_repository", lambda: repository)
    app.dependency_overrides[auth.current_admin_user] = lambda: {
        "id": context.principal_id,
        "tenant_id": context.workspace_id,
        "role": "admin",
    }
    admission = {
        "admission_policy_id": "knowledge.default.v1",
        "rationale": "Hostile route override cannot create authority.",
    }
    grant = {
        "qualification_receipt_id": "missing-receipt",
        "actor_id": "runtime:publisher",
        "action": "publish",
        "target": "knowledge/math",
    }
    promotion = {"target_stage": "evidence_ready"}
    with TestClient(app) as client:
        for path, payload in (
            ("/api/qualification/receipts/missing-receipt/knowledge-admissions", admission),
            ("/api/authorization-grants", grant),
            ("/api/research-registry/claims/missing-revision/promotion-gates", promotion),
        ):
            denied = client.post(path, json=payload)
            assert denied.status_code == 403
            assert denied.json()["error"]["code"] == "authority_denied"

        app.dependency_overrides.clear()
        token = secrets.token_urlsafe(32)
        repository.save_auth_session(
            auth.token_hash(token),
            context.principal_id,
            context.workspace_id,
            (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        )
        for path, payload in (
            ("/api/qualification/receipts/missing-receipt/knowledge-admissions", admission),
            ("/api/authorization-grants", grant),
            ("/api/research-registry/claims/missing-revision/promotion-gates", promotion),
        ):
            authenticated = client.post(
                path,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            # Authentication passed the application gate; only the synthetic
            # missing receipt now blocks the operation.
            assert authenticated.status_code == 404, authenticated.text
