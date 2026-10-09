"""Record authenticated source snapshots and immutable local invalidation decisions."""

from __future__ import annotations

from alembic import op

revision = "0022_verified_invalidations"
down_revision = "0021_math_release_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS notice_verifications (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            source_repository TEXT NOT NULL,
            source_commit TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_artifact_id TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            verification_method TEXT NOT NULL,
            verification_hash TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            verified_at TEXT NOT NULL,
            CHECK(source_repository = 'openai/math'),
            CHECK(source_path = 'history.md'),
            CHECK(verification_method = 'https_pinned_commit_sha256')
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS notice_verifications_tenant_idx "
        "ON notice_verifications(tenant_id, source_commit, verified_at)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS invalidation_decisions (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            notice_verification_id TEXT NOT NULL,
            target_claim_revision_id TEXT NOT NULL,
            target_claim_semantic_hash TEXT NOT NULL,
            target_receipt_id TEXT,
            target_attempt_id TEXT,
            target_scope TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            evidence_artifact_ids TEXT NOT NULL,
            evidence_artifact_hashes TEXT NOT NULL,
            policy_id TEXT NOT NULL,
            policy_hash TEXT NOT NULL,
            rationale TEXT NOT NULL,
            decided_by TEXT NOT NULL,
            effect_state TEXT NOT NULL,
            affected_binding_ids TEXT NOT NULL,
            decision_hash TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            CHECK(target_scope IN ('claim_revision', 'receipt', 'proof_attempt')),
            CHECK(effect_state IN ('stale', 'revoked')),
            CHECK(policy_id = 'invalidation.pinned-source-admin.v1'),
            UNIQUE(tenant_id, decision_hash)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS invalidation_decisions_target_idx "
        "ON invalidation_decisions(tenant_id, target_claim_revision_id, decided_at)"
    )
    if connection.dialect.name == "sqlite":
        for table in ("notice_verifications", "invalidation_decisions"):
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action.lower()} "
                    f"BEFORE {action} ON {table} BEGIN "
                    "SELECT RAISE(ABORT, 'invalidation record is immutable'); END"
                )
        for action in ("INSERT", "UPDATE"):
            op.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS reject_invalidated_binding_{action.lower()}
                BEFORE {action} ON current_use_bindings
                WHEN NEW.state = 'current' AND EXISTS (
                    SELECT 1 FROM invalidation_decisions d
                    WHERE d.tenant_id = NEW.tenant_id
                      AND d.target_claim_revision_id = NEW.claim_revision_id
                      AND (
                        d.target_scope = 'claim_revision'
                        OR d.target_receipt_id = NEW.qualification_receipt_id
                      )
                )
                BEGIN
                    SELECT RAISE(ABORT, 'invalidated qualification cannot be current');
                END
                """
            )
    elif connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            """
            CREATE OR REPLACE FUNCTION reject_invalidation_record_mutation() RETURNS trigger AS $function$
            BEGIN
                RAISE EXCEPTION 'invalidation record is immutable';
            END;
            $function$ LANGUAGE plpgsql
            """
        )
        for table in ("notice_verifications", "invalidation_decisions"):
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS immutable_{table} ON {table}")
            connection.exec_driver_sql(
                f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_invalidation_record_mutation()"
            )
        connection.exec_driver_sql(
            """
            CREATE OR REPLACE FUNCTION reject_invalidated_binding() RETURNS trigger AS $function$
            BEGIN
                IF NEW.state = 'current' AND EXISTS (
                    SELECT 1 FROM invalidation_decisions d
                    WHERE d.tenant_id = NEW.tenant_id
                      AND d.target_claim_revision_id = NEW.claim_revision_id
                      AND (
                        d.target_scope = 'claim_revision'
                        OR d.target_receipt_id = NEW.qualification_receipt_id
                      )
                ) THEN
                    RAISE EXCEPTION 'invalidated qualification cannot be current';
                END IF;
                RETURN NEW;
            END;
            $function$ LANGUAGE plpgsql
            """
        )
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS reject_invalidated_current_binding ON current_use_bindings"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER reject_invalidated_current_binding "
            "BEFORE INSERT OR UPDATE ON current_use_bindings "
            "FOR EACH ROW EXECUTE FUNCTION reject_invalidated_binding()"
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        for action in ("insert", "update"):
            op.execute(f"DROP TRIGGER IF EXISTS reject_invalidated_binding_{action}")
        for table in ("notice_verifications", "invalidation_decisions"):
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS immutable_{table}_{action}")
    elif connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS reject_invalidated_current_binding ON current_use_bindings"
        )
        for table in ("notice_verifications", "invalidation_decisions"):
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS immutable_{table} ON {table}")
        connection.exec_driver_sql("DROP FUNCTION IF EXISTS reject_invalidated_binding()")
        connection.exec_driver_sql("DROP FUNCTION IF EXISTS reject_invalidation_record_mutation()")
    op.execute("DROP TABLE IF EXISTS invalidation_decisions")
    op.execute("DROP TABLE IF EXISTS notice_verifications")
