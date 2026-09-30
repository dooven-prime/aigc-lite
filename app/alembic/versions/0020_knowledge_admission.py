"""Separate qualification receipts from explicit knowledge admission."""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, Text, inspect

revision = "0020_knowledge_admission"
down_revision = "0019_enforcement_dispatches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS knowledge_admission_receipts (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            qualification_receipt_id TEXT NOT NULL,
            qualification_receipt_hash TEXT NOT NULL,
            claim_revision_id TEXT NOT NULL,
            claim_semantic_hash TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            profile_version INTEGER NOT NULL,
            use_scope TEXT NOT NULL,
            admission_policy_id TEXT NOT NULL,
            admission_policy_version INTEGER NOT NULL,
            admission_policy_hash TEXT NOT NULL,
            approved_by TEXT NOT NULL,
            rationale TEXT NOT NULL,
            receipt_hash TEXT NOT NULL,
            issued_at TEXT NOT NULL,
            CHECK(profile_version > 0),
            CHECK(admission_policy_version > 0),
            CHECK(use_scope = 'knowledge'),
            UNIQUE(tenant_id, receipt_hash)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS knowledge_admission_claim_idx "
        "ON knowledge_admission_receipts(tenant_id, claim_revision_id, profile_id, issued_at)"
    )
    binding_columns = {
        item["name"] for item in inspect(connection).get_columns("current_use_bindings")
    }
    if "knowledge_admission_receipt_id" not in binding_columns:
        op.add_column(
            "current_use_bindings",
            Column("knowledge_admission_receipt_id", Text(), nullable=True),
        )
    connection.exec_driver_sql(
        "UPDATE current_use_bindings SET state = 'stale', "
        "stale_reason = 'explicit_knowledge_admission_required' "
        "WHERE use_scope = 'knowledge' AND state = 'current' "
        "AND knowledge_admission_receipt_id IS NULL"
    )

    if connection.dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS validate_knowledge_admission_insert
            BEFORE INSERT ON knowledge_admission_receipts
            WHEN NOT EXISTS (
                SELECT 1 FROM qualification_receipts q
                WHERE q.id = NEW.qualification_receipt_id
                  AND q.tenant_id = NEW.tenant_id
                  AND q.receipt_hash = NEW.qualification_receipt_hash
                  AND q.claim_revision_id = NEW.claim_revision_id
                  AND q.claim_semantic_hash = NEW.claim_semantic_hash
                  AND q.profile_id = NEW.profile_id
                  AND q.profile_version = NEW.profile_version
                  AND q.verdict = 'ADMITTED'
            )
            BEGIN
                SELECT RAISE(ABORT, 'knowledge admission requires matching qualification');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS immutable_knowledge_admission_receipts_update
            BEFORE UPDATE ON knowledge_admission_receipts
            BEGIN
                SELECT RAISE(ABORT, 'knowledge admission receipts are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS immutable_knowledge_admission_receipts_delete
            BEFORE DELETE ON knowledge_admission_receipts
            BEGIN
                SELECT RAISE(ABORT, 'knowledge admission receipts are immutable');
            END
            """
        )
        for event in ("INSERT", "UPDATE"):
            op.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS require_knowledge_admission_{event.lower()}
                BEFORE {event} ON current_use_bindings
                WHEN NEW.use_scope = 'knowledge' AND NEW.state = 'current' AND (
                    NEW.knowledge_admission_receipt_id IS NULL OR NOT EXISTS (
                        SELECT 1 FROM knowledge_admission_receipts a
                        WHERE a.id = NEW.knowledge_admission_receipt_id
                          AND a.tenant_id = NEW.tenant_id
                          AND a.qualification_receipt_id = NEW.qualification_receipt_id
                          AND a.claim_revision_id = NEW.claim_revision_id
                          AND a.profile_id = NEW.profile_id
                          AND a.use_scope = NEW.use_scope
                    )
                )
                BEGIN
                    SELECT RAISE(ABORT, 'current knowledge binding requires admission receipt');
                END
                """
            )
    elif connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            """
            CREATE OR REPLACE FUNCTION validate_knowledge_admission()
            RETURNS trigger AS $function$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM qualification_receipts q
                    WHERE q.id = NEW.qualification_receipt_id
                      AND q.tenant_id = NEW.tenant_id
                      AND q.receipt_hash = NEW.qualification_receipt_hash
                      AND q.claim_revision_id = NEW.claim_revision_id
                      AND q.claim_semantic_hash = NEW.claim_semantic_hash
                      AND q.profile_id = NEW.profile_id
                      AND q.profile_version = NEW.profile_version
                      AND q.verdict = 'ADMITTED'
                ) THEN
                    RAISE EXCEPTION 'knowledge admission requires matching qualification';
                END IF;
                RETURN NEW;
            END;
            $function$ LANGUAGE plpgsql
            """
        )
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS validate_knowledge_admission_receipt "
            "ON knowledge_admission_receipts"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER validate_knowledge_admission_receipt "
            "BEFORE INSERT ON knowledge_admission_receipts "
            "FOR EACH ROW EXECUTE FUNCTION validate_knowledge_admission()"
        )
        connection.exec_driver_sql(
            """
            CREATE OR REPLACE FUNCTION reject_knowledge_admission_mutation()
            RETURNS trigger AS $function$
            BEGIN
                RAISE EXCEPTION 'knowledge admission receipts are immutable';
            END;
            $function$ LANGUAGE plpgsql
            """
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER immutable_knowledge_admission_receipts "
            "BEFORE UPDATE OR DELETE ON knowledge_admission_receipts "
            "FOR EACH ROW EXECUTE FUNCTION reject_knowledge_admission_mutation()"
        )
        connection.exec_driver_sql(
            """
            CREATE OR REPLACE FUNCTION require_knowledge_admission()
            RETURNS trigger AS $function$
            BEGIN
                IF NEW.use_scope = 'knowledge' AND NEW.state = 'current' AND (
                    NEW.knowledge_admission_receipt_id IS NULL OR NOT EXISTS (
                        SELECT 1 FROM knowledge_admission_receipts a
                        WHERE a.id = NEW.knowledge_admission_receipt_id
                          AND a.tenant_id = NEW.tenant_id
                          AND a.qualification_receipt_id = NEW.qualification_receipt_id
                          AND a.claim_revision_id = NEW.claim_revision_id
                          AND a.profile_id = NEW.profile_id
                          AND a.use_scope = NEW.use_scope
                    )
                ) THEN
                    RAISE EXCEPTION 'current knowledge binding requires admission receipt';
                END IF;
                RETURN NEW;
            END;
            $function$ LANGUAGE plpgsql
            """
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER require_knowledge_admission_binding "
            "BEFORE INSERT OR UPDATE ON current_use_bindings "
            "FOR EACH ROW EXECUTE FUNCTION require_knowledge_admission()"
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS require_knowledge_admission_update")
        op.execute("DROP TRIGGER IF EXISTS require_knowledge_admission_insert")
        op.execute("DROP TRIGGER IF EXISTS immutable_knowledge_admission_receipts_delete")
        op.execute("DROP TRIGGER IF EXISTS immutable_knowledge_admission_receipts_update")
        op.execute("DROP TRIGGER IF EXISTS validate_knowledge_admission_insert")
    elif connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS require_knowledge_admission_binding "
            "ON current_use_bindings"
        )
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS immutable_knowledge_admission_receipts "
            "ON knowledge_admission_receipts"
        )
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS validate_knowledge_admission_receipt "
            "ON knowledge_admission_receipts"
        )
        connection.exec_driver_sql("DROP FUNCTION IF EXISTS require_knowledge_admission()")
        connection.exec_driver_sql(
            "DROP FUNCTION IF EXISTS reject_knowledge_admission_mutation()"
        )
        connection.exec_driver_sql(
            "DROP FUNCTION IF EXISTS validate_knowledge_admission()"
        )
    op.drop_column("current_use_bindings", "knowledge_admission_receipt_id")
    op.execute("DROP TABLE IF EXISTS knowledge_admission_receipts")
