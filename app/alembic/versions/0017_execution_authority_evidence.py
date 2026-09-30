"""Add immutable policy proposals and enforcement receipts."""

from __future__ import annotations

from alembic import op

revision = "0017_execution_authority"
down_revision = "0016_conversation_imports"
branch_labels = None
depends_on = None

_IMMUTABLE_TABLES = ("execution_policy_proposals", "enforcement_receipts")


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS execution_policy_proposals (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            state TEXT NOT NULL,
            base_policy_id TEXT NOT NULL,
            base_policy_revision INTEGER NOT NULL,
            base_policy_hash TEXT NOT NULL,
            candidate_policy_id TEXT NOT NULL,
            candidate_policy_revision INTEGER NOT NULL,
            candidate_policy_hash TEXT NOT NULL,
            base_policy TEXT NOT NULL,
            candidate_policy TEXT NOT NULL,
            permission_diff TEXT NOT NULL,
            diff_hash TEXT NOT NULL,
            expands_authority INTEGER NOT NULL,
            expansion_count INTEGER NOT NULL,
            reduction_count INTEGER NOT NULL,
            rationale TEXT NOT NULL,
            requested_by TEXT,
            created_at TEXT NOT NULL,
            CHECK(target_type IN ('tool_execution_backend', 'tool_provider', 'robot_controller')),
            CHECK(state IN ('proposed')),
            CHECK(base_policy_revision >= 0),
            CHECK(candidate_policy_revision > 0),
            CHECK(expands_authority IN (0, 1)),
            CHECK(expansion_count >= 0),
            CHECK(reduction_count >= 0)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS execution_policy_proposals_target_idx "
        "ON execution_policy_proposals(tenant_id, target_type, target_id, created_at)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS enforcement_receipts (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            run_id TEXT NOT NULL,
            step_id TEXT,
            proposal_id TEXT,
            issuer_id TEXT NOT NULL,
            backend_id TEXT NOT NULL,
            enforcement_identity TEXT NOT NULL,
            trust_domain TEXT NOT NULL,
            attestation_type TEXT NOT NULL,
            policy_id TEXT NOT NULL,
            policy_revision INTEGER NOT NULL,
            policy_hash TEXT NOT NULL,
            execution_envelope_hash TEXT NOT NULL,
            tool_spec_hash TEXT NOT NULL,
            arguments_digest TEXT NOT NULL,
            decision TEXT NOT NULL,
            outcome TEXT NOT NULL,
            observed_effects TEXT NOT NULL,
            denied_effects TEXT NOT NULL,
            credential_bindings TEXT NOT NULL,
            image_digest TEXT,
            toolchain_digest TEXT,
            sandbox_id TEXT,
            workload_id TEXT,
            external_signature TEXT,
            attestation TEXT NOT NULL,
            issued_at TEXT NOT NULL,
            receipt_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(policy_revision > 0),
            CHECK(trust_domain IN ('application_process', 'external_runtime', 'independent_infrastructure')),
            CHECK(decision IN ('allow', 'deny', 'quarantine')),
            CHECK(outcome IN ('succeeded', 'failed', 'denied', 'timed_out', 'cancelled', 'quarantined', 'indeterminate')),
            UNIQUE(tenant_id, receipt_hash)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS enforcement_receipts_run_idx "
        "ON enforcement_receipts(tenant_id, run_id, issued_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS enforcement_receipts_policy_idx "
        "ON enforcement_receipts(tenant_id, policy_id, policy_revision, issued_at)"
    )
    if connection.dialect.name == "sqlite":
        for table in _IMMUTABLE_TABLES:
            op.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS immutable_{table}_update
                BEFORE UPDATE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'execution authority evidence is immutable');
                END
                """
            )
            op.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS immutable_{table}_delete
                BEFORE DELETE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'execution authority evidence is immutable');
                END
                """
            )
    elif connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            """
            CREATE OR REPLACE FUNCTION reject_execution_authority_evidence_mutation()
            RETURNS trigger AS $function$
            BEGIN
                RAISE EXCEPTION 'execution authority evidence is immutable';
            END;
            $function$ LANGUAGE plpgsql
            """
        )
        for table in _IMMUTABLE_TABLES:
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS immutable_{table} ON {table}"
            )
            connection.exec_driver_sql(
                f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION "
                "reject_execution_authority_evidence_mutation()"
            )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        for table in reversed(_IMMUTABLE_TABLES):
            op.execute(f"DROP TRIGGER IF EXISTS immutable_{table}_delete")
            op.execute(f"DROP TRIGGER IF EXISTS immutable_{table}_update")
    elif connection.dialect.name == "postgresql":
        for table in reversed(_IMMUTABLE_TABLES):
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS immutable_{table} ON {table}"
            )
        connection.exec_driver_sql(
            "DROP FUNCTION IF EXISTS reject_execution_authority_evidence_mutation()"
        )
    op.execute("DROP TABLE IF EXISTS enforcement_receipts")
    op.execute("DROP TABLE IF EXISTS execution_policy_proposals")
