import os
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text

from app.config import settings
from app.core.contracts import RequestContext
from app.repository import PostgresRepository
from app.services.credentials import CredentialService

POSTGRES_URL = os.getenv("AIGC_LITE_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="AIGC_LITE_TEST_POSTGRES_URL is not configured"
)


def test_postgres_migration_and_workspace_contract(monkeypatch) -> None:
    monkeypatch.setattr(settings, "master_key", Fernet.generate_key().decode())
    repository = PostgresRepository(POSTGRES_URL)
    repository.init()
    assert repository.schema_revision() == "0013_model_credential_binding"
    workspace_id = f"contract-{uuid4()}"
    repository.create_tenant("PostgreSQL contract", workspace_id)
    context = RequestContext(request_id="postgres-contract", workspace_id=workspace_id)
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
        connection.execute(
            text(
                "INSERT INTO current_use_bindings(id, tenant_id, claim_revision_id, "
                "profile_id, use_scope, qualification_receipt_id, state, stale_reason, "
                "bound_by, created_at, updated_at) VALUES (:id, :tenant_id, :claim_id, "
                ":profile_id, 'knowledge', :receipt_id, 'current', NULL, 'contract-test', "
                ":created_at, :updated_at)"
            ),
            {
                "id": str(uuid4()),
                "tenant_id": workspace_id,
                "claim_id": claim_id,
                "profile_id": "contract.profile.v1",
                "receipt_id": receipt_id,
                "created_at": now,
                "updated_at": now,
            },
        )
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
    assert revision == "0013_model_credential_binding"
