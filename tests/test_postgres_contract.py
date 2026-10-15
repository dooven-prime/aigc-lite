import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from authority_support import provision_admin
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.adapters.research_import import OpenAIMathReleaseAdapter
from app.config import settings
from app.core.contracts import RequestContext, ToolSource, ToolSpec
from app.core.enforcement import (
    ENFORCEMENT_DISPATCH_CONTRACT_VERSION,
    EnforcementTargetType,
    ExecutionPolicySnapshot,
    PermissionDomain,
    PolicyPermission,
    authorization_invocation_digest,
    authorization_invocation_facts,
    enforcement_payload_hash,
    tool_spec_value,
)
from app.core.errors import InvalidExecutionPolicyError
from app.core.scheduling import ScheduleKind, ScheduleSpec, ScheduleTaskCommand
from app.repository import PostgresRepository
from app.services.conversation_import_registry import ConversationImportRegistry
from app.services.conversation_imports import ConversationImportService
from app.services.credentials import CredentialService
from app.services.enforcement import EnforcementService
from app.services.math_release_imports import MathReleaseImportService
from app.services.reviews import ReviewService
from app.services.scheduler import SchedulerService

POSTGRES_URL = os.getenv("AIGC_LITE_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="AIGC_LITE_TEST_POSTGRES_URL is not configured"
)


def test_postgres_model_usage_receipt_transaction_and_summary() -> None:
    repository = PostgresRepository(POSTGRES_URL)
    repository.init()
    workspace_id = f"model-usage-{uuid4()}"
    repository.create_tenant("Model usage contract", workspace_id)
    session = repository.create_session(workspace_id, "usage")
    run = repository.create_run(workspace_id, session["id"], "request-pg", None, "model-pg")

    def begin(sequence: int) -> dict:
        return {
            "attempt_id": str(uuid4()), "model_call_id": str(uuid4()), "step_id": str(uuid4()),
            "tenant_id": workspace_id, "principal_id": None, "api_key_id": "test-key-id",
            "request_id": "request-pg", "run_id": run["id"], "sequence": sequence,
            "iteration": sequence, "model": "model-pg", "provider_config_id": None,
            "pricing_snapshot": {"version": 1, "price_known": False, "unit": "per_1000_tokens"},
            "input_content": "prompt", "step_metadata": {"iteration": sequence},
        }

    def finish(status: str, final_hash: str) -> dict:
        return {
            "usage_status": status,
            "prompt_tokens": 2**63 - 1 if status == "REPORTED" else None,
            "completion_tokens": 0 if status == "REPORTED" else None,
            "estimated_cost": None,
            "termination": "COMPLETED", "finish_reason": "stop", "provider_request_id": None,
            "output_bytes": 2, "finalization_hash": final_hash,
            "step_status": "succeeded", "output_content": "ok", "step_metadata": {},
        }

    attempts = [begin(index) for index in (1, 2, 3)]
    for attempt in attempts:
        assert repository.begin_model_usage(attempt)["usage_status"] == "IN_FLIGHT"
    first_result = finish("REPORTED", "pg-first")
    assert repository.finish_model_usage(workspace_id, attempts[0]["attempt_id"], first_result)["usage_status"] == "REPORTED"
    assert repository.finish_model_usage(workspace_id, attempts[0]["attempt_id"], first_result)["usage_status"] == "REPORTED"
    with pytest.raises(ValueError):
        repository.finish_model_usage(
            workspace_id, attempts[0]["attempt_id"], {**first_result, "finalization_hash": "conflict"}
        )
    repository.finish_model_usage(workspace_id, attempts[1]["attempt_id"], finish("REPORTED", "pg-second"))
    repository.finish_model_usage(workspace_id, attempts[2]["attempt_id"], finish("UNKNOWN", "pg-unknown"))
    assert repository.get_model_usage(workspace_id, attempts[0]["attempt_id"])["run_id"] == run["id"]
    assert repository.get_model_usage("wrong-workspace", attempts[0]["attempt_id"]) is None
    summary = repository.usage_summary(workspace_id)
    assert summary["prompt_tokens"] == 2 * (2**63 - 1)
    assert summary["receipt_attempts"] == 3
    assert summary["unknown_requests"] == 1
    assert summary["unpriced_reported_requests"] == 2


def test_postgres_scheduled_firing_is_unique_across_repository_instances() -> None:
    first = PostgresRepository(POSTGRES_URL)
    first.init()
    second = PostgresRepository(POSTGRES_URL)
    now = datetime(2026, 10, 12, tzinfo=UTC)
    workspace_id = f"schedule-{uuid4()}"
    context = RequestContext(request_id="scheduled-contract", workspace_id=workspace_id)
    first_service = SchedulerService(repository_provider=lambda: first, clock=lambda: now)
    second_service = SchedulerService(repository_provider=lambda: second, clock=lambda: now)
    task = first_service.schedule(context, ScheduleTaskCommand(
        name="once", target="test.capture",
        schedule=ScheduleSpec(ScheduleKind.ONCE, now),
    ))
    firing_id = first_service.claim(task, owner="pg-worker-1", now=now, lease_seconds=3)
    assert firing_id
    assert second_service.claim(task, owner="pg-worker-2", now=now) is None
    assert second_service.due(now=now) == []
    assert second_service.reconcile_expired(now=now + timedelta(seconds=4)) == 1
    assert first.get_scheduled_firing(firing_id)["state"] == "indeterminate"
    assert first_service.claim(task, owner="pg-worker-2", now=now + timedelta(minutes=1)) is None
    interval = first_service.schedule(context, ScheduleTaskCommand(
        name="interval", target="test.capture",
        schedule=ScheduleSpec(ScheduleKind.INTERVAL, now, interval_seconds=10),
    ))
    interval_firing = first_service.claim(interval, owner="pg-worker-1", now=now)
    assert interval_firing
    first_service.pause(context, interval.id)
    assert first_service.finish_firing(
        interval_firing, owner="pg-worker-1", state="succeeded", now=now,
    )
    advanced = second_service.acknowledge(context, interval.id, fired_at=now)
    assert advanced.status.value == "paused"
    assert advanced.next_run_at == now + timedelta(seconds=10)


def test_postgres_migration_and_workspace_contract(monkeypatch) -> None:
    monkeypatch.setattr(settings, "master_key", Fernet.generate_key().decode())
    repository = PostgresRepository(POSTGRES_URL)
    repository.init()
    assert repository.schema_revision() == "0028_native_goal_binding"
    workspace_id = f"contract-{uuid4()}"
    repository.create_tenant("PostgreSQL contract", workspace_id)
    context = RequestContext(request_id="postgres-contract", workspace_id=workspace_id)
    assert repository.search_qualified_claims(
        workspace_id, "math.formal.v1", "postgres-contract"
    ) == []
    auth_user = repository.create_user(
        workspace_id, f"{workspace_id}@example.test", "Session owner", "hash"
    )
    repository.save_auth_session("postgres-session-token", auth_user["id"], workspace_id, "2999-01-01T00:00:00+00:00")
    assert repository.get_auth_session("postgres-session-token") is not None
    assert not repository.revoke_auth_session("postgres-session-token", "wrong-user")
    assert repository.revoke_auth_session("postgres-session-token", auth_user["id"])
    assert repository.get_auth_session("postgres-session-token") is None

    class StaticMathAdapter(OpenAIMathReleaseAdapter):
        def fetch(self, source_commit: str) -> tuple[bytes, bytes]:
            self._validate_commit(source_commit)
            return (
                b"# Mathematics manuscript collection\n"
                b"**1 manuscripts covering 1 result families.**\n"
                b"**001. PostgreSQL candidate.** Catalogue summary only.\n"
                b"</td>\n&emsp;[Paper](preprints/paper.pdf)\n"
                b"Candidate abstract.\n</td>\n",
                b"version: v0.4\n",
            )

    math_imports = MathReleaseImportService(
        lambda: repository, adapter=StaticMathAdapter()
    )
    math_commit = "b" * 40
    math_preview = math_imports.preview(context, math_commit)
    math_batch = math_imports.commit(
        context, math_commit, math_preview["preview_hash"]
    )
    assert math_batch["admission_state"] == "candidate"
    assert math_imports.get(context, math_batch["id"])["family_count"] == 1
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
    with pytest.raises(InvalidExecutionPolicyError, match="consumed grant"):
        repository.create_enforcement_dispatch(
            workspace_id, dispatch_values, context=context
        )
    assert repository.list_enforcement_dispatches(workspace_id) == []
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
    write_context, write_authority = provision_admin(repository, context)
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
        context=write_context,
        authority=write_authority,
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
        context=write_context,
        authority=write_authority,
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

    spec = ToolSpec(
        name="postgres_bound_tool",
        native_name="postgres_bound_tool",
        description="PostgreSQL handoff contract",
        input_schema={"type": "object"},
        source=ToolSource.LOCAL,
        provider_id="postgres-provider",
        timeout_seconds=30.0,
        extensions={"authority_requirement": {"required": True}},
    )
    spec_snapshot = tool_spec_value(spec)
    arguments = {"value": 7}

    def consumed_handoff(request_id: str):
        actor_context = RequestContext(request_id, workspace_id, "contract-actor")
        narrow_grant = repository.create_authorization_grant(
            workspace_id,
            {
                "qualification_receipt_id": receipt_id,
                "actor_id": actor_context.principal_id,
                "action": spec.native_name,
                "target": "provider:postgres-provider",
                "max_calls": 1,
                "policy_version": "authorization.policy.v1",
                "grant_receipt": "e" * 64,
            },
            context=write_context,
            authority=write_authority,
        )
        facts = authorization_invocation_facts(
            workspace_id=workspace_id,
            actor_id=actor_context.principal_id,
            request_id=request_id,
            run_id=run["id"],
            action=spec.native_name,
            target="provider:postgres-provider",
            provider_id=spec.provider_id,
            native_name=spec.native_name,
            public_name=spec.name,
            source=spec.source.value,
            binding_id="postgres-binding",
            binding_hash="f" * 64,
            adapter_id="postgres-adapter",
            proposal_id=proposal["id"],
            policy_hash=proposal["candidate_policy_hash"],
            adapter_target_type="tool_execution_backend",
            adapter_target_id="postgres-enforcer",
            tool_spec_hash=enforcement_payload_hash(spec_snapshot),
            tool_timeout_seconds=spec.timeout_seconds,
            arguments_digest=enforcement_payload_hash(arguments),
        )
        digest = authorization_invocation_digest(facts)
        handoff_id = str(uuid4())
        used_at = datetime.now(UTC).isoformat()
        consumed_grant = repository.consume_authorization_grant(
            workspace_id,
            actor_context.principal_id,
            spec.native_name,
            "provider:postgres-provider",
            used_at,
            grant_id=narrow_grant["id"],
            invocation={
                "id": handoff_id,
                **facts,
                "invocation_digest": digest,
                "spec_snapshot": spec_snapshot,
                "arguments": arguments,
            },
        )
        assert consumed_grant is not None
        values = {
            **dispatch_values,
            "id": str(uuid4()),
            "tool_spec_hash": facts["tool_spec_hash"],
            "arguments_digest": facts["arguments_digest"],
            "requested_at": used_at,
            "expires_at": (datetime.fromisoformat(used_at) + timedelta(seconds=10)).isoformat(),
        }
        claim = {
            "id": handoff_id,
            "grant_id": narrow_grant["id"],
            "grant_call_number": consumed_grant["calls_used"],
            **facts,
            "invocation_digest": digest,
        }
        return actor_context, values, claim

    actor_context, authorized_values, claim = consumed_handoff("postgres-handoff-1")
    with pytest.raises(InvalidExecutionPolicyError, match="dispatching state"):
        repository.create_enforcement_dispatch(
            workspace_id, {**authorized_values, "state": "terminal"},
            context=actor_context, authorization_invocation=claim,
        )
    dispatch = repository.create_enforcement_dispatch(
        workspace_id, authorized_values,
        context=actor_context, authorization_invocation=claim,
    )
    assert dispatch["state"] == "dispatching"
    with pytest.raises(InvalidExecutionPolicyError, match="already claimed"):
        repository.create_enforcement_dispatch(
            workspace_id, {**authorized_values, "id": str(uuid4())},
            context=actor_context, authorization_invocation=claim,
        )
    second_context, second_values, second_claim = consumed_handoff("postgres-handoff-2")
    with pytest.raises(IntegrityError):
        repository.create_enforcement_dispatch(
            workspace_id, second_values,
            context=second_context, authorization_invocation=second_claim,
        )
    with pytest.raises(InvalidExecutionPolicyError, match="verified receipt"):
        repository.finish_enforcement_dispatch(
            workspace_id, dispatch["id"], state="terminal"
        )
    with pytest.raises(IntegrityError):
        repository.create_enforcement_dispatch(
            workspace_id, second_values,
            context=second_context, authorization_invocation=second_claim,
        )
    notice_id = str(uuid4())
    repository.record_notice_verification(
        workspace_id,
        {
            "id": notice_id,
            "source_repository": "openai/math",
            "source_commit": "a" * 40,
            "source_path": "history.md",
            "source_artifact_id": str(uuid4()),
            "source_hash": "1" * 64,
            "verification_method": "https_pinned_commit_sha256",
            "verification_hash": "2" * 64,
            "requested_by": "contract-admin",
            "verified_at": now,
        },
        context=write_context,
        authority=write_authority,
    )
    assert repository.get_notice_verification(workspace_id, notice_id) is not None
    decision = repository.apply_invalidation_decision(
        workspace_id,
        {
            "id": str(uuid4()),
            "notice_verification_id": notice_id,
            "target_claim_revision_id": claim_id,
            "target_claim_semantic_hash": "a" * 64,
            "target_receipt_id": receipt_id,
            "target_attempt_id": None,
            "target_scope": "receipt",
            "reason_code": "RECEIPT_DEFECT",
            "evidence_artifact_ids": [],
            "evidence_artifact_hashes": {},
            "policy_id": "invalidation.pinned-source-admin.v1",
            "policy_hash": "3" * 64,
            "rationale": "PostgreSQL atomic invalidation contract.",
            "decided_by": "contract-admin",
            "effect_state": "stale",
            "decision_hash": "4" * 64,
            "decided_at": now,
        },
        context=write_context,
        authority=write_authority,
    )
    assert decision["affected_binding_ids"] == [admission["current_use_binding"]["id"]]
    assert repository.get_current_use_binding(workspace_id, claim_id, "contract.profile.v1")[
        "state"
    ] == "stale"
    with pytest.raises(DatabaseError, match="invalidated qualification"):
        repository.upsert_current_use_binding(
            workspace_id,
            {**admission["current_use_binding"], "state": "current"},
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
    assert revision == "0028_native_goal_binding"
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
