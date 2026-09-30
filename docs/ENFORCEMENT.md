# External enforcement and signed receipts

The external enforcement seam keeps effective filesystem, network, process,
provider, credential, and physical authority outside model output and outside
the ordinary Agent harness.

```text
Agent intent
   -> server-owned PolicyProposal / PermissionDiff
   -> code-registered EnforcerAdapter
   -> independently operated enforcer
   -> signed receipt envelope
   -> host-owned signature and binding verifier
   -> immutable EnforcementReceipt
```

The core distribution supplies the contract, strict HTTP transport, Ed25519
verification, and receipt ledger. It does not supply an OS sandbox, network
gateway, DPU watchdog, hardware quote verifier, or safety-rated controller.

## Tool execution binding

An external adapter is not automatically allowed to intercept tools. Deployment
code must register an `ExternalToolExecutionBinding` that fixes the workspace,
provider, native tool name, adapter, immutable proposal ID, and exact candidate
policy hash. Registering that binding is the deployer's current-use decision;
creating a `PolicyProposal` alone still grants no execution authority.

When a bound tool is called, the Tool Catalog does not call its ordinary local
or MCP provider. It sends a `tool.execute.v1` envelope to the external enforcer
and returns only the signed receipt projection. A missing binding, stale policy
hash, invalid receipt, timeout, or cancellation never falls back to local
execution.

## Trust configuration

Adapters, issuers, and public verification keys are registered by deployment
code in the composition root. Workspace APIs, Chat, MCP tools, model output,
and imported data cannot add or rotate them.

```python
from app.adapters.enforcement import HTTPEnforcerAdapter
from app.core.enforcement import (
    EnforcementIssuer,
    EnforcementSignatureAlgorithm,
    EnforcementTargetType,
    EnforcementTrustDomain,
    EnforcementVerificationKey,
    ExternalToolExecutionBinding,
)
from app.services.enforcement import EnforcementIssuerRegistry, EnforcementService
from app.services.enforced_tools import (
    ExternalToolExecutionBindingRegistry,
    ExternalToolExecutionRouter,
)
from app.services.enforcer import (
    EnforcementVerificationKeyRegistry,
    EnforcerAdapterRegistry,
    ExternalEnforcerService,
    SignedEnforcementReceiptVerifier,
)
from app.services.tool_catalog import create_default_tool_catalog

issuers = EnforcementIssuerRegistry([EnforcementIssuer(
    issuer_id="sandbox.production",
    backend_id="sandbox-v1",
    identity="spiffe://example.test/enforcer/production",
    trust_domain=EnforcementTrustDomain.EXTERNAL_RUNTIME,
    attestation_type="runtime.measurement",
)])
keys = EnforcementVerificationKeyRegistry([EnforcementVerificationKey(
    issuer_id="sandbox.production",
    key_id="receipt-key-2026-09",
    algorithm=EnforcementSignatureAlgorithm.ED25519,
    public_key="<base64url raw 32-byte Ed25519 public key>",
)])
adapters = EnforcerAdapterRegistry([HTTPEnforcerAdapter(
    adapter_id="sandbox.production.http",
    issuer_id="sandbox.production",
    target_type=EnforcementTargetType.TOOL_EXECUTION_BACKEND,
    target_id="production-sandbox",
    endpoint="https://enforcer.example.test/v1/enforce",
)])
ledger = EnforcementService(issuer_registry=issuers)
external = ExternalEnforcerService(
    enforcement_service=ledger,
    adapter_registry=adapters,
    verifier=SignedEnforcementReceiptVerifier(
        issuer_registry=issuers,
        key_registry=keys,
    ),
)
bindings = ExternalToolExecutionBindingRegistry([
    ExternalToolExecutionBinding(
        binding_id="production-dangerous-tool",
        workspace_id="workspace-id",
        provider_id="local",
        native_name="dangerous_tool",
        adapter_id="sandbox.production.http",
        proposal_id="immutable-proposal-id",
        policy_hash="<candidate policy SHA-256>",
    )
])
tool_router = ExternalToolExecutionRouter(
    registry=bindings,
    enforcer_service=external,
)
tool_catalog = create_default_tool_catalog(
    external_tool_executor=tool_router,
)
```

The public key is deployer-owned configuration. Authentication headers, mTLS
client material, or workload credentials should be supplied by deployment code
through the adapter's `header_provider` or a lower-level transport; they must
not be persisted in workspace configuration. The adapter rejects URL-embedded
credentials, redirects, inherited proxy settings, non-JSON responses, and
oversized responses. HTTPS is mandatory except for an explicitly enabled
loopback development endpoint.

## Frozen request

`ExternalEnforcerService.dispatch()` builds the request itself from an existing
workspace Run and immutable PolicyProposal. It adds a random request ID and
nonce, a short expiry, and freezes:

- workspace, Run, optional Step, Proposal, issuer, adapter target;
- candidate policy snapshot/hash and server-derived PermissionDiff/hash;
- execution envelope and its canonical SHA-256 hash;
- ToolSpec and argument digests;
- request and expiry timestamps.

Execution envelopes containing credential-shaped material are rejected.
Credentials should be referenced and injected by the external enforcer only at
the policy-authorized endpoint.

Before network dispatch, the application writes an `enforcement.dispatch.v1`
row in `dispatching`. A signed terminal response moves it to `terminal`.
Transport failure, cancellation, invalid signature, `timed_out`, or an explicit
`indeterminate` outcome moves it to `indeterminate`. `dispatching` and
`indeterminate` both occupy the adapter execution slot, including after process
restart, so a second action cannot bypass an uncertain workload.

An administrator may request reconciliation for an existing uncertain dispatch.
The fresh nonce-bound request contains only the original request ID/hash and
known workload ID. A newly signed terminal receipt closes the root and prior
reconciliation attempts as `reconciled`; another uncertain result keeps the
slot blocked. A `dispatching` row can be reconciled only after its persisted
request expiry, and only one reconciliation may be active for a root dispatch.
Reconciliation cannot create an arbitrary tool invocation.

## Signed response

The HTTP endpoint returns exactly one `enforcer.signature-envelope.v1` object:

```json
{
  "contract_version": "enforcer.signature-envelope.v1",
  "algorithm": "ed25519",
  "key_id": "receipt-key-2026-09",
  "payload": {
    "contract_version": "enforcer.signed-receipt.v1",
    "request_id": "...",
    "request_hash": "...",
    "nonce": "...",
    "workspace_id": "...",
    "issuer_id": "sandbox.production",
    "backend_id": "sandbox-v1",
    "enforcement_identity": "spiffe://example.test/enforcer/production",
    "run_id": "...",
    "step_id": "...",
    "proposal_id": "...",
    "policy_id": "sandbox.default",
    "policy_revision": 1,
    "policy_hash": "...",
    "execution_envelope_hash": "...",
    "tool_spec_hash": "...",
    "arguments_digest": "...",
    "decision": "allow",
    "outcome": "succeeded",
    "observed_effects": [],
    "denied_effects": [],
    "credential_bindings": [],
    "image_digest": null,
    "toolchain_digest": null,
    "sandbox_id": "...",
    "workload_id": "...",
    "attestation": {},
    "issued_at": "2026-09-30T05:00:00+00:00"
  },
  "signature": "<base64url Ed25519 signature over canonical payload JSON>"
}
```

Canonical JSON uses UTF-8, sorted object keys, compact separators, and rejects
NaN/Infinity. The signature covers only `payload`, not the surrounding envelope.

Before persistence, the verifier checks the signature, host-owned issuer and
key association, key validity/revocation, exact nonce/request binding, every
identity and digest, and the request/receipt time window. Any failure produces
no receipt. Successful verification stores the request hash, signing key ID,
canonical signed payload, payload hash, verifier identity, verification time,
and criterion details. The original execution envelope remains hash-only so
credential references and invocation context are not duplicated into evidence.

`GET /api/enforcement/receipts/{receipt_id}/signature-verification` rechecks
the immutable signed payload against the trust roots loaded by the current
process. Historical verification is never erased; a missing or revoked key, or
a key that was not valid at the signed receipt timestamp, makes the current
recheck fail closed instead of rewriting the old receipt.

## What verification does not prove

`signature_verified=true` means the configured private key signed the exact
payload accepted by the application. It does not by itself prove:

- that the signer is outside the Agent host or administered independently;
- that observed effects are complete or truthful;
- that an attestation value is a valid TPM/TEE/DPU quote;
- that a timed-out or indeterminate workload stopped;
- that a policy proposal was approved or installed as current policy.

Those claims need separate deployment evidence, attestation profiles, runtime
state reconciliation, and an authority gate. A signed receipt remains execution
evidence; it cannot mutate Qualification, CurrentUseBinding, or Authorization.
