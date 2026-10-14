"""Hostile checks for a single consumed grant becoming one exact dispatch."""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from threading import Barrier
from uuid import uuid4

import pytest
from authority_support import provision_admin
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from external_handoff_support import provision_external_grant
from test_external_enforcer import SigningAdapter, _policy, _proposal_and_run, _run, _services

from app.core.contracts import RequestContext, ToolProviderResult, ToolSource, ToolSpec
from app.core.enforcement import (
    ExternalToolExecutionBinding,
    authorization_invocation_digest,
    authorization_invocation_facts,
    enforcement_payload_hash,
    tool_spec_value,
)
from app.core.errors import (
    EnforcementDispatchIndeterminateError,
    InvalidExecutionPolicyError,
)
from app.repository import utc_now
from app.services.authorization import ToolAuthorizationGate
from app.services.enforced_tools import (
    ExternalToolExecutionBindingRegistry,
    ExternalToolExecutionRouter,
)
from app.services.tool_catalog import ToolCatalog


class Provider:
    provider_id = "handoff-provider"

    def __init__(self) -> None:
        self.calls = 0
        self.spec = ToolSpec(
            name="sensitive_action",
            native_name="sensitive_action",
            description="Synthetic bound execution.",
            input_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
            source=ToolSource.LOCAL,
            provider_id=self.provider_id,
        )

    async def list_tools(self):
        return [self.spec]

    async def call_tool(self, native_name, arguments):
        self.calls += 1
        return ToolProviderResult(content='{"local_bypass":true}')


@dataclass
class Harness:
    repository: object
    enforcement: object
    external: object
    adapter: SigningAdapter
    context: RequestContext
    proposal: dict
    run: dict
    binding: ExternalToolExecutionBinding
    provider: Provider
    router: ExternalToolExecutionRouter
    gate: ToolAuthorizationGate
    catalog: ToolCatalog
    grant: dict


def _harness(tmp_path, *, outcome: str = "succeeded", conditions: dict | None = None) -> Harness:
    private_key = Ed25519PrivateKey.generate()
    adapter = SigningAdapter(private_key, outcome=outcome)
    repository, enforcement, external, _keys = _services(tmp_path, adapter, private_key)
    context, proposal, run, _step = _proposal_and_run(repository, enforcement)
    provider = Provider()
    binding = ExternalToolExecutionBinding(
        binding_id="handoff-binding",
        workspace_id=context.workspace_id,
        provider_id=provider.provider_id,
        native_name=provider.spec.native_name,
        adapter_id=adapter.adapter_id,
        proposal_id=proposal["id"],
        policy_hash=proposal["candidate_policy_hash"],
    )
    router = ExternalToolExecutionRouter(
        registry=ExternalToolExecutionBindingRegistry([binding]),
        enforcer_service=external,
        repository_provider=lambda: repository,
    )
    grant = provision_external_grant(
        repository,
        context,
        action=provider.spec.native_name,
        target=f"provider:{provider.provider_id}",
        conditions=conditions,
    )
    gate = ToolAuthorizationGate(lambda: repository)
    gate._qualification.refresh_receipt_binding = lambda *_args: None
    catalog = ToolCatalog(
        [provider],
        authorization_gate=gate.authorize_and_consume,
        external_tool_executor=router,
    )
    return Harness(
        repository, enforcement, external, adapter, context, proposal, run,
        binding, provider, router, gate, catalog, grant,
    )


async def _invoke(harness: Harness, arguments: dict) -> object:
    session = await harness.catalog.open(harness.context, run_id=harness.run["id"])
    return await session.invoke(harness.provider.spec.name, json.dumps(arguments))


def _consumed_request(harness: Harness, arguments: dict | None = None) -> dict:
    args = arguments or {"value": 7}
    spec = harness.provider.spec
    binding, failure = harness.router.prepare_if_bound(
        harness.context, harness.run["id"], spec
    )
    assert failure is None
    authorization_spec = replace(
        spec, extensions={"authority_requirement": {"required": True}}
    )
    consumed = harness.gate.authorize_and_consume(
        harness.context,
        authorization_spec,
        args,
        run_id=harness.run["id"],
        dispatch_binding=binding,
        tool_spec_hash=enforcement_payload_hash(tool_spec_value(spec)),
        dispatch_spec=spec,
    )
    assert consumed is not None
    envelope = {
        "operation": "tool.execute.v1",
        "principal_id": harness.context.principal_id,
        "request_id": harness.context.request_id,
        "tool": {
            "public_name": spec.name,
            "native_name": spec.native_name,
            "provider_id": spec.provider_id,
            "source": spec.source.value,
        },
        "arguments": args,
        "grant_binding": {
            "action": spec.native_name,
            "target": f"provider:{spec.provider_id}",
            "grant_id": consumed["id"],
            "grant_call_number": consumed["calls_used"],
            "authorization_invocation_id": consumed["authorization_invocation_id"],
            "invocation_digest": consumed["invocation_digest"],
        },
    }
    return {
        "adapter_id": harness.adapter.adapter_id,
        "proposal_id": harness.proposal["id"],
        "run_id": harness.run["id"],
        "step_id": None,
        "execution_envelope": envelope,
        "tool_spec_hash": enforcement_payload_hash(tool_spec_value(spec)),
        "tool_timeout_seconds": spec.timeout_seconds,
        "arguments_digest": enforcement_payload_hash(args),
        "expires_in_seconds": min(30.0, spec.timeout_seconds),
        "binding_id": harness.binding.binding_id,
        "binding_hash": harness.binding.as_dict()["binding_hash"],
        "authorization_invocation_id": consumed["authorization_invocation_id"],
    }


def _dispatch_count(harness: Harness) -> int:
    return len(harness.repository.list_enforcement_dispatches(harness.context.workspace_id))


@pytest.mark.parametrize("timeout_seconds", (30, 30.0))
def test_bound_tool_requires_one_grant_and_claims_one_dispatch(
    tmp_path, timeout_seconds: float
) -> None:
    harness = _harness(tmp_path)
    harness.provider.spec = replace(
        harness.provider.spec, timeout_seconds=timeout_seconds
    )
    result = asyncio.run(_invoke(harness, {"value": 7}))
    assert result.failed is False
    assert harness.provider.calls == 0
    assert len(harness.adapter.requests) == 1
    assert _dispatch_count(harness) == 1
    assert harness.repository.list_authorization_grants(harness.context.workspace_id)[0][
        "calls_used"
    ] == 1
    with harness.repository._connect() as connection:
        row = connection.execute(
            "SELECT grant_id, grant_call_number, dispatch_id FROM authorization_invocations"
        ).fetchone()
    assert row["grant_id"] == harness.grant["id"]
    assert row["grant_call_number"] == 1
    assert row["dispatch_id"] == harness.repository.list_enforcement_dispatches(
        harness.context.workspace_id
    )[0]["id"]


def test_disappearing_external_binding_cannot_fall_back_to_local_provider(tmp_path) -> None:
    harness = _harness(tmp_path)

    async def missing_binding(*_args):
        return None

    harness.router.execute_if_bound = missing_binding
    result = asyncio.run(_invoke(harness, {"value": 7}))
    assert result.failed
    assert harness.provider.calls == 0
    assert harness.adapter.requests == []
    assert _dispatch_count(harness) == 0
    assert harness.repository.list_authorization_grants(harness.context.workspace_id)[0][
        "calls_used"
    ] == 1


def test_direct_unconsumed_and_spoofed_generic_dispatch_fail_closed(tmp_path) -> None:
    harness = _harness(tmp_path)
    base = {
        "adapter_id": harness.adapter.adapter_id,
        "proposal_id": harness.proposal["id"],
        "run_id": harness.run["id"],
        "step_id": None,
        "tool_spec_hash": "a" * 64,
        "arguments_digest": "b" * 64,
    }
    for envelope in (
        {"operation": "tool.execute.v1", "arguments": {"value": 7}},
        {"operation": "tool.execute.v0", "arguments": {"value": 7}},
        {"authorized": True, "tool": "sensitive_action"},
    ):
        with pytest.raises(InvalidExecutionPolicyError):
            asyncio.run(harness.external.dispatch(
                harness.context, execution_envelope=envelope, **base
            ))
    assert harness.adapter.requests == []
    assert _dispatch_count(harness) == 0
    assert harness.repository.list_authorization_grants(harness.context.workspace_id)[0][
        "calls_used"
    ] == 0


@pytest.mark.parametrize("mutation", ("actor", "run", "arguments", "tool", "target", "binding", "policy", "step"))
def test_handoff_rejects_changed_invocation_without_external_call(tmp_path, mutation: str) -> None:
    harness = _harness(tmp_path)
    request = _consumed_request(harness)
    if mutation == "actor":
        harness.context = replace(harness.context, principal_id="other-actor")
        request["execution_envelope"]["principal_id"] = "other-actor"
    elif mutation == "run":
        second_run, _ = _run(harness.repository)
        request["run_id"] = second_run["id"]
    elif mutation == "arguments":
        request["execution_envelope"]["arguments"] = {"value": 8}
        request["arguments_digest"] = enforcement_payload_hash({"value": 8})
    elif mutation == "tool":
        request["execution_envelope"]["tool"]["native_name"] = "other_action"
    elif mutation == "target":
        request["execution_envelope"]["grant_binding"]["target"] = "provider:other-provider"
    elif mutation == "binding":
        request["binding_id"] = "other-binding"
    elif mutation == "policy":
        second = harness.enforcement.propose_policy(
            harness.context,
            target_type=harness.adapter.target_type,
            target_id=harness.adapter.target_id,
            candidate_policy=_policy(),
        )
        request["proposal_id"] = second["id"]
    else:
        request["step_id"] = _run(harness.repository)[1]["id"]
    with pytest.raises(InvalidExecutionPolicyError):
        asyncio.run(harness.external.dispatch(harness.context, **request))
    assert harness.adapter.requests == []
    assert _dispatch_count(harness) == 0
    assert harness.repository.list_authorization_grants(harness.context.workspace_id)[0][
        "calls_used"
    ] == 1


def test_claimed_invocation_cannot_dispatch_twice(tmp_path) -> None:
    harness = _harness(tmp_path)
    request = _consumed_request(harness)
    first = asyncio.run(harness.external.dispatch(harness.context, **request))
    assert first["dispatch_id"]
    with pytest.raises(InvalidExecutionPolicyError):
        asyncio.run(harness.external.dispatch(harness.context, **request))
    assert len(harness.adapter.requests) == 1
    assert _dispatch_count(harness) == 1
    assert harness.repository.list_authorization_grants(harness.context.workspace_id)[0][
        "calls_used"
    ] == 1


def test_two_dispatchers_cannot_claim_the_same_invocation(tmp_path) -> None:
    harness = _harness(tmp_path)
    request = _consumed_request(harness)
    barrier = Barrier(2)

    def attempt() -> object:
        barrier.wait(timeout=5)
        try:
            return asyncio.run(harness.external.dispatch(harness.context, **request))
        except (InvalidExecutionPolicyError, EnforcementDispatchIndeterminateError) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(isinstance(item, dict) for item in results) == 1
    assert sum(isinstance(item, Exception) for item in results) == 1
    assert len(harness.adapter.requests) == 1
    assert _dispatch_count(harness) == 1
    assert harness.repository.list_authorization_grants(harness.context.workspace_id)[0][
        "calls_used"
    ] == 1


def test_dispatch_cannot_exceed_consumed_tool_timeout(tmp_path) -> None:
    harness = _harness(tmp_path)
    harness.provider.spec = replace(harness.provider.spec, timeout_seconds=1.0)
    request = _consumed_request(harness)
    request["expires_in_seconds"] = 300.0
    with pytest.raises(InvalidExecutionPolicyError):
        asyncio.run(harness.external.dispatch(harness.context, **request))
    assert harness.adapter.requests == []
    assert _dispatch_count(harness) == 0
    request["expires_in_seconds"] = 1.0
    receipt = asyncio.run(harness.external.dispatch(harness.context, **request))
    assert receipt["dispatch_id"]
    assert len(harness.adapter.requests) == 1


def test_integer_timeout_consumed_handoff_dispatches_once(tmp_path) -> None:
    harness = _harness(tmp_path)
    harness.provider.spec = replace(harness.provider.spec, timeout_seconds=30)
    request = _consumed_request(harness)
    receipt = asyncio.run(harness.external.dispatch(harness.context, **request))
    assert receipt["dispatch_id"]
    assert len(harness.adapter.requests) == 1
    assert _dispatch_count(harness) == 1
    assert harness.repository.list_authorization_grants(harness.context.workspace_id)[0][
        "calls_used"
    ] == 1


def test_expired_unclaimed_handoff_cannot_dispatch(tmp_path) -> None:
    harness = _harness(tmp_path)
    request = _consumed_request(harness)
    with harness.repository._connect() as connection:
        connection.execute(
            "UPDATE authorization_invocations SET claim_expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", request["authorization_invocation_id"]),
        )
    with pytest.raises(InvalidExecutionPolicyError):
        asyncio.run(harness.external.dispatch(harness.context, **request))
    assert harness.adapter.requests == []
    assert _dispatch_count(harness) == 0


def test_repository_cannot_relabel_grant_consumer_or_bypass_conditions(tmp_path) -> None:
    harness = _harness(tmp_path, conditions={"arguments": {"value": {"$lte": 5}}})
    spec = harness.provider.spec
    details, failure = harness.router.prepare_if_bound(
        harness.context, harness.run["id"], spec
    )
    assert failure is None
    for actor_id, arguments in (("other-actor", {"value": 3}), (harness.context.principal_id, {"value": 7})):
        facts = authorization_invocation_facts(
            workspace_id=harness.context.workspace_id,
            actor_id=actor_id,
            request_id=harness.context.request_id,
            run_id=harness.run["id"],
            action=spec.native_name,
            target=f"provider:{spec.provider_id}",
            provider_id=spec.provider_id,
            native_name=spec.native_name,
            public_name=spec.name,
            source=spec.source.value,
            binding_id=details["binding_id"],
            binding_hash=details["binding_hash"],
            adapter_id=details["adapter_id"],
            proposal_id=details["proposal_id"],
            policy_hash=details["policy_hash"],
            adapter_target_type=details["adapter_target_type"],
            adapter_target_id=details["adapter_target_id"],
            tool_spec_hash=enforcement_payload_hash(tool_spec_value(spec)),
            tool_timeout_seconds=spec.timeout_seconds,
            arguments_digest=enforcement_payload_hash(arguments),
        )
        forged = {
            "id": str(uuid4()),
            **facts,
            "invocation_digest": authorization_invocation_digest(facts),
            "spec_snapshot": tool_spec_value(spec),
            "arguments": arguments,
        }
        assert harness.repository.consume_authorization_grant(
            harness.context.workspace_id,
            harness.context.principal_id,
            spec.native_name,
            f"provider:{spec.provider_id}",
            utc_now(),
            grant_id=harness.grant["id"],
            invocation=forged,
        ) is None
    assert harness.repository.list_authorization_grants(harness.context.workspace_id)[0][
        "calls_used"
    ] == 0
    with harness.repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM authorization_invocations").fetchone()[0] == 0


def test_indeterminate_reentry_does_not_consume_another_grant(tmp_path) -> None:
    harness = _harness(tmp_path, outcome="indeterminate")
    first = asyncio.run(_invoke(harness, {"value": 7}))
    second = asyncio.run(_invoke(harness, {"value": 7}))
    assert first.failed and second.failed
    assert len(harness.adapter.requests) == 1
    assert _dispatch_count(harness) == 1
    assert harness.repository.list_authorization_grants(harness.context.workspace_id)[0][
        "calls_used"
    ] == 1


@pytest.mark.parametrize("forged_state", ("terminal", "reconciled"))
def test_direct_repository_finish_cannot_release_indeterminate_slot(
    tmp_path, forged_state: str
) -> None:
    harness = _harness(tmp_path, outcome="indeterminate")
    first = asyncio.run(_invoke(harness, {"value": 7}))
    assert first.failed
    original = harness.repository.list_enforcement_dispatches(
        harness.context.workspace_id
    )[0]
    with pytest.raises(InvalidExecutionPolicyError):
        harness.repository.finish_enforcement_dispatch(
            harness.context.workspace_id,
            original["id"],
            state=forged_state,
        )
    assert harness.repository.get_enforcement_dispatch(
        harness.context.workspace_id, original["id"]
    )["state"] == "indeterminate"
    second = asyncio.run(_invoke(harness, {"value": 7}))
    assert second.failed
    assert len(harness.adapter.requests) == 1
    assert harness.repository.list_authorization_grants(harness.context.workspace_id)[0][
        "calls_used"
    ] == 1


def test_reconcile_requires_admin_and_rejects_terminal_root(tmp_path) -> None:
    harness = _harness(tmp_path)
    request = _consumed_request(harness)
    receipt = asyncio.run(harness.external.dispatch(harness.context, **request))
    with pytest.raises(InvalidExecutionPolicyError):
        asyncio.run(harness.external.dispatch(
            harness.context,
            **{**request, "original_dispatch_id": receipt["dispatch_id"]},
        ))
    admin_context, authority = provision_admin(harness.repository, harness.context)
    with pytest.raises(InvalidExecutionPolicyError):
        asyncio.run(harness.external.reconcile_dispatch(
            admin_context, receipt["dispatch_id"], authority=authority
        ))
    assert len(harness.adapter.requests) == 1


@pytest.mark.parametrize("changed", ("original_request_hash", "workload_id"))
def test_reconcile_rejects_forged_root_identity_before_adapter(tmp_path, changed: str) -> None:
    harness = _harness(tmp_path, outcome="indeterminate")
    root_receipt = asyncio.run(harness.external.dispatch(
        harness.context, **_consumed_request(harness)
    ))
    root = harness.external.get_dispatch(harness.context, root_receipt["dispatch_id"])
    assert root["state"] == "indeterminate"
    admin_context, authority = provision_admin(harness.repository, harness.context)
    envelope = {
        "operation": "enforcement.reconcile.v1",
        "original_dispatch_id": root["id"],
        "original_request_hash": root["request_hash"],
        "workload_id": root["workload_id"],
    }
    envelope[changed] = "forged-value"
    with pytest.raises(InvalidExecutionPolicyError):
        asyncio.run(harness.external.dispatch(
            admin_context,
            adapter_id=root["adapter_id"],
            proposal_id=root["proposal_id"],
            run_id=root["run_id"],
            step_id=root["step_id"],
            execution_envelope=envelope,
            tool_spec_hash=root["tool_spec_hash"],
            arguments_digest=root["arguments_digest"],
            original_dispatch_id=root["id"],
            authority=authority,
            expected_original_dispatch=root,
        ))
    assert len(harness.adapter.requests) == 1
    assert _dispatch_count(harness) == 1
