import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.repository import SQLiteRepository

_LEGACY_MCP_SCHEMA = """
CREATE TABLE mcp_servers (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, provider_id TEXT NOT NULL,
    url TEXT NOT NULL, header_credentials TEXT NOT NULL DEFAULT '{}',
    risk TEXT NOT NULL DEFAULT 'low', required_scopes TEXT NOT NULL DEFAULT '[]',
    timeout_seconds REAL NOT NULL DEFAULT 30, enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, provider_id)
);
"""


def _alembic_config(database_path: Path) -> Config:
    app_root = Path(__file__).resolve().parents[1] / "app"
    config = Config(str(app_root / "alembic.ini"))
    config.set_main_option("script_location", str(app_root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def test_alembic_adopts_existing_database_and_preserves_records(tmp_path) -> None:
    path = tmp_path / "existing.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(_LEGACY_MCP_SCHEMA)
        connection.execute(
            "INSERT INTO mcp_servers(id, tenant_id, provider_id, url, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("server-1", "workspace-a", "research", "https://mcp.example/mcp", "now", "now"),
        )

    repository = SQLiteRepository(path)
    repository.init()

    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(mcp_servers)")
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        research_claim_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(research_claim_revisions)"
            )
        }
    assert {
        "health_status",
        "last_tested_at",
        "last_error_code",
        "last_latency_ms",
        "last_tool_count",
    } <= columns
    assert revision == "0020_knowledge_admission"
    assert {
        "artifacts",
        "citations",
        "credentials",
        "decision_cases",
        "evidence_claims",
        "evidence_protocols",
        "evidence_reviews",
        "execution_receipts",
        "freeze_manifests",
        "scheduled_tasks",
        "search_entries",
        "search_entries_fts",
        "research_cases",
        "research_claim_revisions",
        "research_claim_relations",
        "research_claim_sources",
        "research_promotion_evaluations",
        "research_sources",
        "research_verification_attempts",
        "research_verification_executions",
        "research_verification_plans",
        "qualification_evaluations",
        "qualification_receipts",
        "knowledge_admission_receipts",
        "evidence_edges",
        "current_use_bindings",
        "authorization_grants",
        "review_runs",
        "review_findings",
        "conversation_import_batches",
        "conversation_import_conversations",
        "conversation_import_messages",
        "execution_policy_proposals",
        "enforcement_dispatches",
        "enforcement_receipts",
    } <= tables
    assert "promotion_stage" in research_claim_columns
    assert {
        "semantic_hash",
        "definitions",
        "negative_boundaries",
        "dependency_claim_ids",
        "parent_revision_id",
    } <= research_claim_columns
    with sqlite3.connect(path) as connection:
        run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(agent_runs)")
        }
        attempt_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(research_verification_attempts)")
        }
        promotion_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(research_promotion_evaluations)")
        }
        execution_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(research_verification_executions)")
        }
    assert "independence" in attempt_columns
    assert {"capability_set_id", "capability_policy_hash"} <= run_columns
    with sqlite3.connect(path) as connection:
        search_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(search_entries)")
        }
    assert {"import_batch_id", "conversation_id"} <= search_columns
    with sqlite3.connect(path) as connection:
        enforcement_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(enforcement_receipts)")
        }
        dispatch_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(enforcement_dispatches)")
        }
        binding_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(current_use_bindings)")
        }
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
    assert "knowledge_admission_receipt_id" in binding_columns
    assert {"validation_modality", "verifier_lineage"} <= attempt_columns
    assert "input_snapshot" in promotion_columns
    assert "input_snapshot" in execution_columns
    assert repository.get_mcp_server("workspace-a", "server-1")["provider_id"] == "research"


def test_repository_repairs_unreleased_sqlite_revision_alias(tmp_path) -> None:
    path = tmp_path / "provisional-revision.db"
    repository = SQLiteRepository(path)
    repository.init()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE alembic_version SET version_num = ?",
            ("0017_execution_authority_evidence",),
        )

    reopened = SQLiteRepository(path)
    reopened.init()

    with sqlite3.connect(path) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
    assert revision == "0020_knowledge_admission"


def test_knowledge_admission_migration_does_not_silently_grandfather_bindings(
    tmp_path,
) -> None:
    path = tmp_path / "knowledge-admission-migration.db"
    config = _alembic_config(path)
    command.upgrade(config, "0019_enforcement_dispatches")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO qualification_receipts(id, tenant_id, evaluation_id, "
            "claim_revision_id, claim_semantic_hash, profile_id, profile_version, "
            "evidence_closure_hash, policy_version, policy_hash, verdict, criteria, "
            "blockers, evidence_vector, independence_summary, receipt_hash, issued_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "receipt-legacy",
                "workspace-a",
                "evaluation-legacy",
                "claim-legacy",
                "a" * 64,
                "math.formal.v1",
                1,
                "b" * 64,
                "math.formal.policy.v1",
                "c" * 64,
                "ADMITTED",
                "[]",
                "[]",
                "{}",
                "{}",
                "d" * 64,
                "now",
            ),
        )
        connection.execute(
            "INSERT INTO current_use_bindings(id, tenant_id, claim_revision_id, "
            "profile_id, use_scope, qualification_receipt_id, state, stale_reason, "
            "bound_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "binding-legacy",
                "workspace-a",
                "claim-legacy",
                "math.formal.v1",
                "knowledge",
                "receipt-legacy",
                "current",
                None,
                "legacy-gate",
                "now",
                "now",
            ),
        )

    command.upgrade(config, "head")

    with sqlite3.connect(path) as connection:
        state, reason, admission_id = connection.execute(
            "SELECT state, stale_reason, knowledge_admission_receipt_id "
            "FROM current_use_bindings WHERE id = ?",
            ("binding-legacy",),
        ).fetchone()
    assert state == "stale"
    assert reason == "explicit_knowledge_admission_required"
    assert admission_id is None


def test_model_credential_binding_migration_drops_legacy_secret_authority(
    tmp_path,
) -> None:
    path = tmp_path / "model-credential-migration.db"
    config = _alembic_config(path)
    command.upgrade(config, "0012_qualification_plane")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO model_configs(id, tenant_id, name, base_url, model, api_key, "
            "input_price, output_price, is_default, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "model-1",
                "workspace-a",
                "legacy",
                "https://models.example.test/v1",
                "example-model",
                "enc:v1:legacy-ciphertext",
                0,
                0,
                1,
                "now",
            ),
        )

    command.upgrade(config, "head")

    with sqlite3.connect(path) as connection:
        api_key, credential_reference = connection.execute(
            "SELECT api_key, credential_reference FROM model_configs WHERE id = ?",
            ("model-1",),
        ).fetchone()
    assert api_key == ""
    assert credential_reference == ""
