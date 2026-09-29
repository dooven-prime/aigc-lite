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
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "0013_model_credential_binding"
