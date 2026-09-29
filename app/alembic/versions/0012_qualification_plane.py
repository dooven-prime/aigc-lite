"""Add the domain-qualified evidence and authority ledgers."""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, Text

revision = "0012_qualification_plane"
down_revision = "0011_assurance_bundle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    op.add_column(
        "research_claim_revisions",
        Column("semantic_hash", Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "research_claim_revisions",
        Column("definitions", Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "research_claim_revisions",
        Column("negative_boundaries", Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "research_claim_revisions",
        Column("dependency_claim_ids", Text(), nullable=False, server_default="[]"),
    )
    op.add_column("research_claim_revisions", Column("parent_revision_id", Text(), nullable=True))
    op.add_column(
        "research_verification_attempts",
        Column(
            "validation_modality",
            Text(),
            nullable=False,
            server_default="agent_review",
        ),
    )
    op.add_column(
        "research_verification_attempts",
        Column("verifier_lineage", Text(), nullable=False, server_default="{}"),
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS qualification_evaluations (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            claim_revision_id TEXT NOT NULL,
            claim_semantic_hash TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            profile_version INTEGER NOT NULL,
            profile_hash TEXT NOT NULL,
            profile_snapshot TEXT NOT NULL,
            evidence_closure_hash TEXT NOT NULL,
            evidence_closure TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            policy_hash TEXT NOT NULL,
            verdict TEXT NOT NULL,
            criteria TEXT NOT NULL,
            blockers TEXT NOT NULL,
            evidence_vector TEXT NOT NULL,
            independence_summary TEXT NOT NULL,
            evaluated_by TEXT,
            created_at TEXT NOT NULL,
            CHECK(profile_version > 0),
            CHECK(verdict IN ('ADMITTED', 'BLOCKED', 'UNRESOLVED', 'STALE', 'NOT_APPLICABLE'))
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS qualification_evaluations_claim_idx "
        "ON qualification_evaluations(tenant_id, claim_revision_id, profile_id, created_at)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS qualification_receipts (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            evaluation_id TEXT NOT NULL,
            claim_revision_id TEXT NOT NULL,
            claim_semantic_hash TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            profile_version INTEGER NOT NULL,
            evidence_closure_hash TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            policy_hash TEXT NOT NULL,
            verdict TEXT NOT NULL,
            criteria TEXT NOT NULL,
            blockers TEXT NOT NULL,
            evidence_vector TEXT NOT NULL,
            independence_summary TEXT NOT NULL,
            receipt_hash TEXT NOT NULL,
            issued_at TEXT NOT NULL,
            UNIQUE(tenant_id, evaluation_id),
            UNIQUE(tenant_id, receipt_hash),
            CHECK(verdict = 'ADMITTED')
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS qualification_receipts_claim_idx "
        "ON qualification_receipts(tenant_id, claim_revision_id, profile_id, issued_at)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS evidence_edges (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            evaluation_id TEXT NOT NULL,
            source_node_type TEXT NOT NULL,
            source_node_id TEXT NOT NULL,
            target_node_type TEXT NOT NULL,
            target_node_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            source_hash TEXT,
            target_hash TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(status IN ('active', 'tainted')),
            UNIQUE(tenant_id, evaluation_id, source_node_type, source_node_id,
                   target_node_type, target_node_id, edge_type)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS evidence_edges_node_idx "
        "ON evidence_edges(tenant_id, source_node_type, source_node_id, status)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS current_use_bindings (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            claim_revision_id TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            use_scope TEXT NOT NULL,
            qualification_receipt_id TEXT NOT NULL,
            state TEXT NOT NULL,
            stale_reason TEXT,
            bound_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(state IN ('current', 'stale', 'revoked')),
            UNIQUE(tenant_id, claim_revision_id, profile_id, use_scope)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS current_use_bindings_lookup_idx "
        "ON current_use_bindings(tenant_id, profile_id, state, claim_revision_id)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS authorization_grants (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            qualification_receipt_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT NOT NULL,
            scope TEXT NOT NULL,
            conditions TEXT NOT NULL,
            expires_at TEXT,
            budget TEXT NOT NULL,
            max_calls INTEGER NOT NULL,
            calls_used INTEGER NOT NULL DEFAULT 0,
            policy_version TEXT NOT NULL,
            state TEXT NOT NULL,
            grant_receipt TEXT NOT NULL,
            created_by TEXT,
            created_at TEXT NOT NULL,
            CHECK(max_calls > 0),
            CHECK(calls_used >= 0),
            CHECK(state IN ('active', 'expired', 'revoked'))
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS authorization_grants_actor_idx "
        "ON authorization_grants(tenant_id, actor_id, action, state, expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS authorization_grants")
    op.execute("DROP TABLE IF EXISTS current_use_bindings")
    op.execute("DROP TABLE IF EXISTS evidence_edges")
    op.execute("DROP TABLE IF EXISTS qualification_receipts")
    op.execute("DROP TABLE IF EXISTS qualification_evaluations")
    op.drop_column("research_verification_attempts", "verifier_lineage")
    op.drop_column("research_verification_attempts", "validation_modality")
    op.drop_column("research_claim_revisions", "parent_revision_id")
    op.drop_column("research_claim_revisions", "dependency_claim_ids")
    op.drop_column("research_claim_revisions", "negative_boundaries")
    op.drop_column("research_claim_revisions", "definitions")
    op.drop_column("research_claim_revisions", "semantic_hash")
