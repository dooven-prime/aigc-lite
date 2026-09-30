import json
import os
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.contracts import RequestContext
from app.core.enforcement import (
    ENFORCEMENT_DISPATCH_CONTRACT_VERSION,
    EnforcementTargetType,
    ExecutionPolicySnapshot,
    PermissionDomain,
    PolicyPermission,
)
from app.repository import PostgresRepository
from app.services.conversation_import_registry import ConversationImportRegistry
from app.services.conversation_imports import ConversationImportService
from app.services.credentials import CredentialService
from app.services.enforcement import EnforcementService
from app.services.reviews import ReviewService

POSTGRES_URL = os.getenv("AIGC_LITE_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="AIGC_LITE_TEST_POSTGRES_URL is not configured"
)


def test_postgres_migration_and_workspace_contract(monkeypatch) -> None:
    monkeypatch.setattr(settings, "master_key", Fernet.generate_key().decode())
    repository = PostgresRepository(POSTGRES_URL)
    repository.init()
    assert repository.schema_revision() == "0020_knowledge_admission"
    workspace_id = f"contract-{uuid4()}"
    repository.create_tenant("PostgreSQL contract", workspace_id)
    context = RequestContext(request_id="postgres-contract", workspace_id=workspace_id)
    import_source = json.dumps(
        [
            {
                "id": "postgres-import",
                "title": "PostgreSQL imported conversation",
                "current_node": "node-1",
                "mapping": {
                    "node-1": {
                        "parent": None,
                        "children": [],
                        "message": {
                            "id": "postgres-message",
                            "author": {"role": "user"},
                            "content": {
                                "content_type": "text",
                                "parts": ["postgres conversation import marker"],
                            },
                            "metadata": {},
                        },
                    }
                },
            }
        ]
    ).encode()
    import_service = ConversationImportService(
        registry=ConversationImportRegistry.builtins(),
        repository_provider=lambda: repository,
    )
    import_preview = import_service.preview(
        context,
        importer_id="chatgpt.export.v1",
        source_name="postgres-conversations.json",
        source_bytes=import_source,
    )
    import_batch = import_service.commit(
        context,
        importer_id="chatgpt.export.v1",
        source_name="postgres-conversations.json",
        source_bytes=import_source,
        expected_preview_hash=import_preview["preview_hash"],
    )
    assert import_batch["admission_state"] == "candidate"
    assert any(
        item["kind"] == "conversation_message"
        for item in repository.search_memory(
            workspace_id, "conversation import marker", 20
        )
    )
    credential = CredentialService(lambda: repository).create(
        context, "model-key", "postgres-contract-secret"
    )

    model = repository.save_model_config(
        workspace_id,
        {
            "name": "contract-model",
            "base_url": "https://models.example.test/v1",
            "model": "contract-upstream",
            "credential_reference": credential["reference"],
            "is_default": True,
        },
    )
    assert model["credential_reference"] == credential["reference"]
    assert "api_key" not in repository.get_model_config(workspace_id)

    mcp = repository.save_mcp_server(
        workspace_id,
        {
            "provider_id": "contract-mcp",
            "url": "https://mcp.example.test/mcp",
            "header_credentials": {"Authorization": credential["reference"]},
        },
    )
    assert mcp["header_credentials"] == {
        "Authorization": credential["reference"]
    }
    session = repository.create_session(workspace_id, "PostgreSQL contract")
    run = repository.create_run(
        workspace_id,
        session["id"],
        "postgres-contract",
        None,
        "contract-upstream",
    )
    repository.finish_run(workspace_id, run["id"], "succeeded")
    assert repository.get_run(workspace_id, run["id"])["status"] == "succeeded"
    proposal = EnforcementService(lambda: repository).propose_policy(
        context,
        target_type=EnforcementTargetType.TOOL_EXECUTION_BACKEND,
        target_id="postgres-enforcer",
        candidate_policy=ExecutionPolicySnapshot(
            policy_id="postgres.policy",
            revision=1,
            permissions=(
                PolicyPermission(
                    domain=PermissionDomain.NETWORK,
                    resource="api.example.test:443",
                    actions=("connect",),
                ),
            ),
        ),
    )
    dispatch_values = {
        "id": str(uuid4()),
        "contract_version": ENFORCEMENT_DISPATCH_CONTRACT_VERSION,
        "binding_id": "postgres-binding",
        "adapter_id": "postgres-adapter",
        "issuer_id": "postgres-issuer",
        "target_type": "tool_execution_backend",
        "target_id": "postgres-enforcer",
        "run_id": run["id"],
        "step_id": None,
        "proposal_id": proposal["id"],
        "original_dispatch_id": None,
        "request_hash": "1" * 64,
        "execution_envelope_hash": "2" * 64,
        "tool_spec_hash": "3" * 64,
        "arguments_digest": "4" * 64,
        "requested_at": "2026-09-30T00:00:00+00:00",
        "expires_at": "2026-09-30T00:00:30+00:00",
        "state": "dispatching",
    }
    dispatch = repository.create_enforcement_dispatch(
        workspace_id, dispatch_values
    )
    assert dispatch["state"] == "dispatching"
    with pytest.raises(IntegrityError):
        repository.create_enforcement_dispatch(
            workspace_id, {**dispatch_values, "id": str(uuid4())}
        )
    repository.finish_enforcement_dispatch(
        workspace_id, dispatch["id"], state="terminal"
    )
    replacement = repository.create_enforcement_dispatch(
        workspace_id, {**dispatch_values, "id": str(uuid4())}
    )
    assert replacement["state"] == "dispatching"
    repository.finish_enforcement_dispatch(
        workspace_id, replacement["id"], state="terminal"
    )
    review = ReviewService(lambda: repository).review_execution_run(
        context, run["id"], "execution.integrity.v1"
    )
    assert review["status"] == "completed"
    assert repository.get_review_run(workspace_id, review["id"]) == review

    engine = create_engine(POSTGRES_URL)
    receipt_id = str(uuid4())
    claim_id = str(uuid4())
    now = "2026-09-29T00:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO qualification_receipts(id, tenant_id, evaluation_id, "
                "claim_revision_id, claim_semantic_hash, profile_id, profile_version, "
                "evidence_closure_hash, policy_version, policy_hash, verdict, criteria, "
                "blockers, evidence_vector, independence_summary, receipt_hash, issued_at) "
                "VALUES (:id, :tenant_id, :evaluation_id, :claim_id, :semantic_hash, "
                ":profile_id, 1, :closure_hash, :policy_version, :policy_hash, "
                "'ADMITTED', '[]', '[]', '{}', '{}', :receipt_hash, :issued_at)"
            ),
            {
                "id": receipt_id,
                "tenant_id": workspace_id,
                "evaluation_id": str(uuid4()),
                "claim_id": claim_id,
                "semantic_hash": "a" * 64,
                "profile_id": "contract.profile.v1",
                "closure_hash": "b" * 64,
                "policy_version": "contract-policy-v1",
                "policy_hash": "c" * 64,
                "receipt_hash": "d" * 64,
                "issued_at": now,
            },
        )
    admission = repository.admit_knowledge(
        workspace_id,
        {
            "id": str(uuid4()),
            "contract_version": "knowledge.admission.v1",
            "qualification_receipt_id": receipt_id,
            "qualification_receipt_hash": "d" * 64,
            "claim_revision_id": claim_id,
            "claim_semantic_hash": "a" * 64,
            "profile_id": "contract.profile.v1",
            "profile_version": 1,
            "use_scope": "knowledge",
            "admission_policy_id": "knowledge.default.v1",
            "admission_policy_version": 1,
            "admission_policy_hash": "f" * 64,
            "approved_by": "contract-test",
            "rationale": "PostgreSQL atomic admission contract.",
            "receipt_hash": "0" * 64,
            "issued_at": now,
        },
    )
    assert admission["current_use_binding"]["qualification_receipt_id"] == receipt_id
    grant = repository.create_authorization_grant(
        workspace_id,
        {
            "qualification_receipt_id": receipt_id,
            "actor_id": "contract-actor",
            "action": "robot_navigate_to",
            "target": "robot:contract",
            "max_calls": 1,
            "policy_version": "authorization.policy.v1",
            "grant_receipt": "e" * 64,
        },
    )
    consumed = repository.consume_authorization_grant(
        workspace_id,
        "contract-actor",
        "robot_navigate_to",
        "robot:contract",
        "2026-09-29T00:00:01+00:00",
        grant_id=grant["id"],
    )
    assert consumed is not None
    assert consumed["id"] == grant["id"]
    assert consumed["calls_used"] == 1
    assert (
        repository.consume_authorization_grant(
            workspace_id,
            "contract-actor",
            "robot_navigate_to",
            "robot:contract",
            "2026-09-29T00:00:02+00:00",
        )
        is None
    )
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        enforcement_columns = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'enforcement_receipts'"
                )
            )
        }
        dispatch_columns = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'enforcement_dispatches'"
                )
            )
        }
    assert revision == "0020_knowledge_admission"
    assert {
        "signature_verified",
        "enforcement_request_id",
        "enforcement_request_hash",
        "signature_algorithm",
        "signing_key_id",
        "signed_payload_hash",
        "signature_verified_at",
        "signature_verifier_id",
        "signed_payload",
        "signature_verification",
    } <= enforcement_columns
    assert {
        "binding_id",
        "adapter_id",
        "original_dispatch_id",
        "request_hash",
        "requested_at",
        "expires_at",
        "state",
        "receipt_id",
        "workload_id",
        "last_error_code",
    } <= dispatch_columns
