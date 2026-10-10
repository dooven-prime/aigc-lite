from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import AsyncClient, MockTransport, Request, Response

from app.adapters.enforcement import HTTPEnforcerAdapter
from app.core.contracts import (
    RequestContext,
    ToolProviderResult,
    ToolSource,
    ToolSpec,
)
from app.core.enforcement import (
    SIGNATURE_ENVELOPE_CONTRACT_VERSION,
    SIGNED_ENFORCEMENT_RECEIPT_CONTRACT_VERSION,
    EnforcementIssuer,
    EnforcementSignatureAlgorithm,
    EnforcementTargetType,
    EnforcementTrustDomain,
    EnforcementVerificationKey,
    ExecutionPolicySnapshot,
    ExternalToolExecutionBinding,
    PermissionDomain,
    PolicyPermission,
    SignedEnforcementReceiptEnvelope,
    canonical_enforcement_json,
)
from app.core.errors import (
    EnforcementDispatchIndeterminateError,
    InvalidEnforcementReceiptError,
    InvalidExecutionPolicyError,
)
from app.repository import SQLiteRepository
from app.services.enforced_tools import (
    ExternalToolExecutionBindingRegistry,
    ExternalToolExecutionRouter,
)
from app.services.enforcement import EnforcementIssuerRegistry, EnforcementService
from app.services.enforcer import (
    EnforcementVerificationKeyRegistry,
    EnforcerAdapterRegistry,
    ExternalEnforcerService,
    SignedEnforcementReceiptVerifier,
)
from app.services.tool_catalog import ToolCatalog

HASH_A = "a" * 64
HASH_B = "b" * 64
NOW = datetime(2026, 9, 30, 5, 0, tzinfo=UTC)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _repository(tmp_path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "external-enforcer.db")
    repository.init()
    return repository


def _run(repository: SQLiteRepository) -> tuple[dict, dict]:
    session = repository.create_session("workspace-a", "External enforcement")
    run = repository.create_run(
        "workspace-a", session["id"], "external-request", None, "test-model"
    )
    step = repository.append_run_step(
        "workspace-a",
        run["id"],
        1,
        "tool",
        "external_tool",
        "succeeded",
        "{}",
        "{}",
        {},
    )
    return run, step


def _policy() -> ExecutionPolicySnapshot:
    return ExecutionPolicySnapshot(
        policy_id="sandbox.external",
        revision=1,
        permissions=(
            PolicyPermission(
                PermissionDomain.NETWORK,
                "https://api.example.test:443",
                ("connect",),
            ),
        ),
        limits={"wall_time_seconds": 30},
    )


class SigningAdapter:
    adapter_id = "external.http"
    issuer_id = "external.runtime"
    target_type = EnforcementTargetType.TOOL_EXECUTION_BACKEND
    target_id = "external-sandbox"

    def __init__(
        self,
        private_key: Ed25519PrivateKey,
        *,
        mutate_after_sign: bool = False,
        wrong_nonce: bool = False,
        key_id: str = "key-2026-09",
        decision: str = "allow",
        outcome: str = "succeeded",
    ) -> None:
        self.private_key = private_key
        self.mutate_after_sign = mutate_after_sign
        self.wrong_nonce = wrong_nonce
        self.key_id = key_id
        self.decision = decision
        self.outcome = outcome
        self.requests = []

    async def enforce(self, request):
        self.requests.append(request)
        payload = {
            "contract_version": SIGNED_ENFORCEMENT_RECEIPT_CONTRACT_VERSION,
            "request_id": request.request_id,
            "request_hash": request.request_hash,
            "nonce": "wrong-nonce" if self.wrong_nonce else request.nonce,
            "workspace_id": request.workspace_id,
            "issuer_id": self.issuer_id,
            "backend_id": "external-sandbox-v1",
            "enforcement_identity": "spiffe://example.test/enforcer/runtime",
            "run_id": request.run_id,
            "step_id": request.step_id,
            "proposal_id": request.proposal_id,
            "policy_id": request.policy["policy_id"],
            "policy_revision": request.policy["revision"],
            "policy_hash": request.policy_hash,
            "execution_envelope_hash": request.execution_envelope_hash,
            "tool_spec_hash": request.tool_spec_hash,
            "arguments_digest": request.arguments_digest,
            "decision": self.decision,
            "outcome": self.outcome,
            "observed_effects": (
                [{"domain": "network", "resource": "api.example.test:443"}]
                if self.decision == "allow" else []
            ),
            "denied_effects": (
                [] if self.decision == "allow" else [{
                    "domain": "network",
                    "resource": request.execution_envelope.get("arguments", {}).get(
                        "endpoint", "unapproved-endpoint"
                    ),
                }]
            ),
            "credential_bindings": (
                [{
                    "credential_reference": "encrypted-db://credential/example",
                    "endpoint": "api.example.test:443",
                }] if self.decision == "allow" else []
            ),
            "image_digest": "sha256:" + "c" * 64,
            "toolchain_digest": None,
            "sandbox_id": "sandbox-1",
            "workload_id": "workload-1",
            "attestation": {"runtime_measurement": "d" * 64},
            "issued_at": request.requested_at,
        }
        signature = _b64url(self.private_key.sign(canonical_enforcement_json(payload)))
        if self.mutate_after_sign:
            payload["outcome"] = "failed"
        return SignedEnforcementReceiptEnvelope(
            algorithm=EnforcementSignatureAlgorithm.ED25519,
            key_id=self.key_id,
            payload=payload,
            signature=signature,
        )


def _services(tmp_path, adapter: SigningAdapter, key, *, revoked: bool = False):
    repository = _repository(tmp_path)
    issuer_registry = EnforcementIssuerRegistry(
        [
            EnforcementIssuer(
                issuer_id="external.runtime",
                backend_id="external-sandbox-v1",
                identity="spiffe://example.test/enforcer/runtime",
                trust_domain=EnforcementTrustDomain.EXTERNAL_RUNTIME,
                attestation_type="runtime.measurement",
            )
        ]
    )
    enforcement = EnforcementService(lambda: repository, issuer_registry)
    public_key = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_registry = EnforcementVerificationKeyRegistry(
        [
            EnforcementVerificationKey(
                issuer_id="external.runtime",
                key_id="key-2026-09",
                algorithm=EnforcementSignatureAlgorithm.ED25519,
                public_key=_b64url(public_key),
                not_before="2026-09-30T00:00:00Z",
                not_after="2026-10-01T00:00:00Z",
                revoked=revoked,
            )
        ]
    )
    verifier = SignedEnforcementReceiptVerifier(
        issuer_registry=issuer_registry,
        key_registry=key_registry,
        now_provider=lambda: NOW,
    )
    external = ExternalEnforcerService(
        enforcement_service=enforcement,
        adapter_registry=EnforcerAdapterRegistry([adapter]),
        verifier=verifier,
        repository_provider=lambda: repository,
        now_provider=lambda: NOW,
    )
    return repository, enforcement, external, key_registry


def _proposal_and_run(repository, enforcement):
    context = RequestContext(
        request_id="dispatch-1",
        workspace_id="workspace-a",
        principal_id="system:tool-backend",
    )
    proposal = enforcement.propose_policy(
        context,
        target_type=EnforcementTargetType.TOOL_EXECUTION_BACKEND,
        target_id="external-sandbox",
        candidate_policy=_policy(),
    )
    run, step = _run(repository)
    return context, proposal, run, step


def test_external_enforcer_verifies_signature_and_persists_request_binding(
    tmp_path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    adapter = SigningAdapter(private_key)
    repository, enforcement, external, key_registry = _services(
        tmp_path, adapter, private_key
    )
    context, proposal, run, step = _proposal_and_run(repository, enforcement)

    receipt = asyncio.run(
        external.dispatch(
            context,
            adapter_id=adapter.adapter_id,
            proposal_id=proposal["id"],
            run_id=run["id"],
            step_id=step["id"],
            execution_envelope={
                "tool": "external_tool",
                "credential_reference": "encrypted-db://credential/example",
            },
            tool_spec_hash=HASH_A,
            arguments_digest=HASH_B,
        )
    )

    assert receipt["signature_verified"] is True
    assert receipt["signature_algorithm"] == "ed25519"
    assert receipt["signing_key_id"] == "key-2026-09"
    assert receipt["enforcement_request_id"] == adapter.requests[0].request_id
    assert receipt["enforcement_request_hash"] == adapter.requests[0].request_hash
    assert receipt["execution_envelope_hash"] == adapter.requests[0].execution_envelope_hash
    assert receipt["signed_payload_hash"]
    assert receipt["signed_payload"]["request_hash"] == adapter.requests[0].request_hash
    assert receipt["signature_verification"]["details"] == {
        "signature_valid": True,
        "request_binding_valid": True,
        "issuer_binding_valid": True,
        "freshness_valid": True,
        "public_key_fingerprint": key_registry.list_public()[0][
            "public_key_fingerprint"
        ],
    }
    assert receipt["credential_bindings"][0]["credential_reference"].startswith(
        "encrypted-db://"
    )
    rechecked = external.verify_stored_receipt(context, receipt["id"])
    assert rechecked["currently_valid"] is True
    assert rechecked["signed_payload_hash"] == receipt["signed_payload_hash"]
    assert "hardware_attestation_not_verified" in rechecked["limitations"]

    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    revoked_verifier = SignedEnforcementReceiptVerifier(
        issuer_registry=EnforcementIssuerRegistry(
            [
                EnforcementIssuer(
                    issuer_id="external.runtime",
                    backend_id="external-sandbox-v1",
                    identity="spiffe://example.test/enforcer/runtime",
                    trust_domain=EnforcementTrustDomain.EXTERNAL_RUNTIME,
                    attestation_type="runtime.measurement",
                )
            ]
        ),
        key_registry=EnforcementVerificationKeyRegistry(
            [
                EnforcementVerificationKey(
                    issuer_id="external.runtime",
                    key_id="key-2026-09",
                    algorithm=EnforcementSignatureAlgorithm.ED25519,
                    public_key=_b64url(public_key),
                    revoked=True,
                )
            ]
        ),
        now_provider=lambda: NOW,
    )
    with pytest.raises(InvalidEnforcementReceiptError, match="revoked"):
        revoked_verifier.verify_stored_receipt(receipt)
    assert receipt["signature_verified"] is True


@pytest.mark.parametrize(
    ("adapter_kwargs", "field"),
    [
        ({"mutate_after_sign": True}, "signature"),
        ({"wrong_nonce": True}, "nonce"),
        ({"key_id": "unknown-key"}, "key_id"),
    ],
)
def test_external_enforcer_rejects_tampering_and_untrusted_bindings(
    tmp_path, adapter_kwargs, field
) -> None:
    private_key = Ed25519PrivateKey.generate()
    adapter = SigningAdapter(private_key, **adapter_kwargs)
    repository, enforcement, external, _keys = _services(
        tmp_path, adapter, private_key
    )
    context, proposal, run, step = _proposal_and_run(repository, enforcement)

    with pytest.raises(EnforcementDispatchIndeterminateError) as captured:
        asyncio.run(
            external.dispatch(
                context,
                adapter_id=adapter.adapter_id,
                proposal_id=proposal["id"],
                run_id=run["id"],
                step_id=step["id"],
                execution_envelope={"tool": "external_tool"},
                tool_spec_hash=HASH_A,
                arguments_digest=HASH_B,
            )
        )
    assert captured.value.metadata["cause"] == "unverifiable_receipt"
    assert repository.list_enforcement_receipts("workspace-a") == []
    dispatch = repository.get_enforcement_dispatch(
        "workspace-a", captured.value.metadata["dispatch_id"]
    )
    assert dispatch["state"] == "indeterminate"
    assert dispatch["last_error_code"] == "invalid_enforcement_receipt"


def test_external_enforcer_rejects_revoked_key_and_secret_bearing_request(
    tmp_path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    adapter = SigningAdapter(private_key)
    repository, enforcement, external, _keys = _services(
        tmp_path, adapter, private_key, revoked=True
    )
    context, proposal, run, step = _proposal_and_run(repository, enforcement)
    with pytest.raises(EnforcementDispatchIndeterminateError) as captured:
        asyncio.run(
            external.dispatch(
                context,
                adapter_id=adapter.adapter_id,
                proposal_id=proposal["id"],
                run_id=run["id"],
                step_id=step["id"],
                execution_envelope={"tool": "external_tool"},
                tool_spec_hash=HASH_A,
                arguments_digest=HASH_B,
            )
        )
    assert captured.value.metadata["cause"] == "unverifiable_receipt"

    with pytest.raises(InvalidExecutionPolicyError, match="credential references"):
        asyncio.run(
            external.dispatch(
                context,
                adapter_id=adapter.adapter_id,
                proposal_id=proposal["id"],
                run_id=run["id"],
                step_id=step["id"],
                execution_envelope={"api_key": "do-not-send"},
                tool_spec_hash=HASH_A,
                arguments_digest=HASH_B,
            )
        )


def test_indeterminate_dispatch_requires_signed_reconciliation(tmp_path) -> None:
    private_key = Ed25519PrivateKey.generate()
    adapter = SigningAdapter(private_key, outcome="indeterminate")
    repository, enforcement, external, _keys = _services(
        tmp_path, adapter, private_key
    )
    context, proposal, run, step = _proposal_and_run(repository, enforcement)

    receipt = asyncio.run(
        external.dispatch(
            context,
            adapter_id=adapter.adapter_id,
            proposal_id=proposal["id"],
            run_id=run["id"],
            step_id=step["id"],
            execution_envelope={"operation": "tool.execute.v1"},
            tool_spec_hash=HASH_A,
            arguments_digest=HASH_B,
            binding_id="binding-1",
        )
    )
    root_dispatch_id = receipt["dispatch_id"]
    assert receipt["dispatch_state"] == "indeterminate"
    assert external.unresolved_dispatches(
        context,
        adapter_id=adapter.adapter_id,
        proposal_id=proposal["id"],
    )[0]["id"] == root_dispatch_id
    with pytest.raises(EnforcementDispatchIndeterminateError) as conflict:
        asyncio.run(
            external.dispatch(
                context,
                adapter_id=adapter.adapter_id,
                proposal_id=proposal["id"],
                run_id=run["id"],
                step_id=step["id"],
                execution_envelope={"operation": "tool.execute.v1"},
                tool_spec_hash=HASH_A,
                arguments_digest=HASH_B,
                binding_id="second-binding",
            )
        )
    assert conflict.value.metadata == {
        "dispatch_id": root_dispatch_id,
        "cause": "active_adapter_conflict",
    }

    adapter.outcome = "succeeded"
    reconciled = asyncio.run(
        external.reconcile_dispatch(context, root_dispatch_id)
    )
    assert reconciled["dispatch_state"] == "terminal"
    assert adapter.requests[-1].execution_envelope == {
        "operation": "enforcement.reconcile.v1",
        "original_dispatch_id": root_dispatch_id,
        "original_request_hash": adapter.requests[0].request_hash,
        "workload_id": "workload-1",
    }
    assert external.get_dispatch(context, root_dispatch_id)["state"] == "reconciled"
    assert external.get_dispatch(context, reconciled["dispatch_id"])["state"] == "terminal"
    assert external.unresolved_dispatches(
        context,
        adapter_id=adapter.adapter_id,
        proposal_id=proposal["id"],
    ) == []


def test_live_dispatch_cannot_be_reconciled_and_cancellation_becomes_indeterminate(
    tmp_path,
) -> None:
    class BlockingAdapter(SigningAdapter):
        async def enforce(self, request):
            self.requests.append(request)
            await asyncio.Event().wait()

    private_key = Ed25519PrivateKey.generate()
    adapter = BlockingAdapter(private_key)
    repository, enforcement, external, _keys = _services(
        tmp_path, adapter, private_key
    )
    context, proposal, run, step = _proposal_and_run(repository, enforcement)

    async def scenario():
        task = asyncio.create_task(
            external.dispatch(
                context,
                adapter_id=adapter.adapter_id,
                proposal_id=proposal["id"],
                run_id=run["id"],
                step_id=step["id"],
                execution_envelope={"operation": "tool.execute.v1"},
                tool_spec_hash=HASH_A,
                arguments_digest=HASH_B,
                binding_id="binding-live",
            )
        )
        while not adapter.requests:
            await asyncio.sleep(0)
        dispatch_id = adapter.requests[0].request_id
        with pytest.raises(InvalidExecutionPolicyError, match="active request window"):
            await external.reconcile_dispatch(context, dispatch_id)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return dispatch_id

    dispatch_id = asyncio.run(scenario())
    dispatch = external.get_dispatch(context, dispatch_id)
    assert dispatch["state"] == "indeterminate"
    assert dispatch["last_error_code"] == "agent_cancelled"


def test_tool_catalog_routes_bound_tool_only_through_external_enforcer(
    tmp_path,
) -> None:
    class Provider:
        provider_id = "sensitive-provider"

        def __init__(self) -> None:
            self.calls = 0

        async def list_tools(self):
            return [
                ToolSpec(
                    name="sensitive_action",
                    native_name="sensitive_action",
                    description="Execute one externally enforced action.",
                    input_schema={
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                    },
                    source=ToolSource.LOCAL,
                    provider_id=self.provider_id,
                )
            ]

        async def call_tool(self, native_name, arguments):
            self.calls += 1
            return ToolProviderResult(content='{"unsafe_local_fallback":true}')

    private_key = Ed25519PrivateKey.generate()
    adapter = SigningAdapter(private_key)
    repository, enforcement, external, _keys = _services(
        tmp_path, adapter, private_key
    )
    context, proposal, run, _step = _proposal_and_run(repository, enforcement)
    binding = ExternalToolExecutionBinding(
        binding_id="sensitive-binding",
        workspace_id=context.workspace_id,
        provider_id="sensitive-provider",
        native_name="sensitive_action",
        adapter_id=adapter.adapter_id,
        proposal_id=proposal["id"],
        policy_hash=proposal["candidate_policy_hash"],
    )
    router = ExternalToolExecutionRouter(
        registry=ExternalToolExecutionBindingRegistry([binding]),
        enforcer_service=external,
        repository_provider=lambda: repository,
    )
    provider = Provider()
    catalog = ToolCatalog([provider], external_tool_executor=router)

    async def invoke():
        session = await catalog.open(context, run_id=run["id"])
        return await session.invoke("sensitive_action", '{"value":7}')

    result = asyncio.run(invoke())
    assert result.failed is False
    assert provider.calls == 0
    assert json.loads(result.content)["status"] == "succeeded"
    enforcement_metadata = result.metadata["external_enforcement"]
    assert enforcement_metadata["binding_id"] == binding.binding_id
    assert enforcement_metadata["signature_verified"] is True
    assert repository.list_enforcement_dispatches(
        context.workspace_id, state="terminal"
    )[0]["binding_id"] == binding.binding_id


def test_signed_network_denial_blocks_budget_escape_without_local_fallback(
    tmp_path,
) -> None:
    """A bound tool cannot spend through an unapproved provider endpoint."""
    class Provider:
        provider_id = "budget-escape-provider"

        def __init__(self) -> None:
            self.calls = 0

        async def list_tools(self):
            return [ToolSpec(
                name="budget_escape_canary",
                native_name="budget_escape_canary",
                description="A synthetic provider call used only by this test.",
                input_schema={
                    "type": "object",
                    "properties": {"endpoint": {"type": "string"}},
                    "required": ["endpoint"],
                },
                source=ToolSource.LOCAL,
                provider_id=self.provider_id,
            )]

        async def call_tool(self, native_name, arguments):
            self.calls += 1
            return ToolProviderResult(content='{"unsafe_local_fallback":true}')

    private_key = Ed25519PrivateKey.generate()
    adapter = SigningAdapter(private_key, decision="deny", outcome="denied")
    repository, enforcement, external, _keys = _services(
        tmp_path, adapter, private_key
    )
    context, proposal, run, _step = _proposal_and_run(repository, enforcement)
    binding = ExternalToolExecutionBinding(
        binding_id="budget-escape-binding",
        workspace_id=context.workspace_id,
        provider_id="budget-escape-provider",
        native_name="budget_escape_canary",
        adapter_id=adapter.adapter_id,
        proposal_id=proposal["id"],
        policy_hash=proposal["candidate_policy_hash"],
    )
    router = ExternalToolExecutionRouter(
        registry=ExternalToolExecutionBindingRegistry([binding]),
        enforcer_service=external,
        repository_provider=lambda: repository,
    )
    provider = Provider()
    catalog = ToolCatalog([provider], external_tool_executor=router)
    endpoint = "https://unapproved.example.test/v1/chat"

    async def invoke():
        session = await catalog.open(context, run_id=run["id"])
        return await session.invoke("budget_escape_canary", json.dumps({"endpoint": endpoint}))

    result = asyncio.run(invoke())
    assert result.failed is True
    assert json.loads(result.content) == {"error": "external_enforcement_denied"}
    assert provider.calls == 0
    assert len(adapter.requests) == 1
    receipt = repository.list_enforcement_receipts(context.workspace_id)[0]
    assert receipt["signature_verified"] is True
    assert receipt["decision"] == "deny"
    assert receipt["outcome"] == "denied"
    assert receipt["denied_effects"] == [{"domain": "network", "resource": endpoint}]
    assert receipt["arguments_digest"] == adapter.requests[0].arguments_digest


def test_tool_catalog_blocks_reentry_while_external_state_is_indeterminate(
    tmp_path,
) -> None:
    class Provider:
        provider_id = "blocked-provider"
        calls = 0

        async def list_tools(self):
            return [
                ToolSpec(
                    name="blocked_action",
                    native_name="blocked_action",
                    description="Never falls back while external state is unknown.",
                    input_schema={"type": "object"},
                    source=ToolSource.LOCAL,
                    provider_id=self.provider_id,
                )
            ]

        async def call_tool(self, native_name, arguments):
            self.calls += 1
            return ToolProviderResult(content="{}")

    private_key = Ed25519PrivateKey.generate()
    adapter = SigningAdapter(private_key, outcome="indeterminate")
    repository, enforcement, external, _keys = _services(
        tmp_path, adapter, private_key
    )
    context, proposal, run, _step = _proposal_and_run(repository, enforcement)
    binding = ExternalToolExecutionBinding(
        binding_id="blocked-binding",
        workspace_id=context.workspace_id,
        provider_id="blocked-provider",
        native_name="blocked_action",
        adapter_id=adapter.adapter_id,
        proposal_id=proposal["id"],
        policy_hash=proposal["candidate_policy_hash"],
    )
    router = ExternalToolExecutionRouter(
        registry=ExternalToolExecutionBindingRegistry([binding]),
        enforcer_service=external,
        repository_provider=lambda: repository,
    )
    provider = Provider()
    catalog = ToolCatalog([provider], external_tool_executor=router)

    async def invoke_twice():
        session = await catalog.open(context, run_id=run["id"])
        return (
            await session.invoke("blocked_action", "{}"),
            await session.invoke("blocked_action", "{}"),
        )

    first, second = asyncio.run(invoke_twice())
    assert first.failed is True
    assert second.failed is True
    assert json.loads(first.content)["error"] == "enforcement_dispatch_indeterminate"
    assert json.loads(second.content)["error"] == "enforcement_dispatch_indeterminate"
    assert len(adapter.requests) == 1
    assert provider.calls == 0
    assert (
        first.metadata["external_enforcement"]["dispatch_id"]
        == second.metadata["external_enforcement"]["dispatch_id"]
    )


def test_http_enforcer_adapter_uses_hardened_transport_and_bounded_wire_contract() -> None:
    captured = {}

    async def handler(request: Request) -> Response:
        captured["request"] = request
        captured["body"] = json.loads(request.content)
        return Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "contract_version": SIGNATURE_ENVELOPE_CONTRACT_VERSION,
                "algorithm": "ed25519",
                "key_id": "key-1",
                "payload": {},
                "signature": "signed-value",
            },
        )

    transport = MockTransport(handler)

    def client_factory(**kwargs):
        captured["client"] = kwargs
        return AsyncClient(transport=transport, **kwargs)

    adapter = HTTPEnforcerAdapter(
        adapter_id="external.http",
        issuer_id="external.runtime",
        target_type=EnforcementTargetType.TOOL_EXECUTION_BACKEND,
        target_id="external-sandbox",
        endpoint="https://enforcer.example.test/v1/enforce",
        header_provider=lambda: {"Authorization": "Bearer deployer-owned"},
        client_factory=client_factory,
    )
    request = type("RequestContract", (), {"as_dict": lambda self: {"request": "frozen"}})()
    result = asyncio.run(adapter.enforce(request))

    assert result.key_id == "key-1"
    assert captured["request"].method == "POST"
    assert captured["body"] == {"request": "frozen"}
    assert captured["client"]["follow_redirects"] is False
    assert captured["client"]["trust_env"] is False
    assert captured["request"].headers["authorization"] == "Bearer deployer-owned"
    with pytest.raises(ValueError, match="HTTPS"):
        HTTPEnforcerAdapter(
            adapter_id="bad",
            issuer_id="external.runtime",
            target_type=EnforcementTargetType.TOOL_EXECUTION_BACKEND,
            target_id="external-sandbox",
            endpoint="http://enforcer.example.test/v1/enforce",
        )
