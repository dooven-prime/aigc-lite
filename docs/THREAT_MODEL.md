# Threat Model

This document defines the trust boundaries of `aigc-lite`. It is an
architecture contract, not a claim that the current process is an operating
system sandbox or a safety-rated controller.

## 1. Governing assumptions

The system is designed around four assumptions:

> **Assume the governed system may eventually understand the gate.**

> **Understanding the gate must not confer authority to change the gate.**

> **Capability growth must not imply authority growth.**

> **Agent intent is not effective authority.**

Models, Agents, prompts, retrieved documents, tools, and model-based reviewers
are all potentially adversarial or mistaken. A capable system may recognize an
evaluation, optimize for its acceptance criteria, exploit a verifier, request a
broader policy, coordinate with another Agent, or find a path that application
authors did not anticipate. Safety therefore cannot depend on the current model
being unable to understand or circumvent a guard.

Model output may create only proposals and evidence candidates. It must not
directly mutate:

- Claim semantics or the canonical `ClaimRevision`;
- evidence qualification or `CurrentUseBinding`;
- authorization grants or effective execution capability;
- the policy, verifier, or enforcement configuration that governs itself.

## 2. Two authority planes

`aigc-lite` separates epistemic authority from execution authority.

### Qualification Plane

Qualification is a relation, not a property of an Artifact:

```text
Q(ClaimRevision, QualificationProfile, EvidenceClosure, PolicyVersion)
    -> QualificationReceipt
```

The deterministic Qualification Gate reads frozen inputs. A model may propose
a verification result or review, but it cannot issue its own Receipt or alter a
current-use binding. A historical Receipt remains part of the record when a
dependency becomes stale; it does not remain currently admissible by default.

### Execution Authority Plane

A qualified statement is not automatically authorized to cause an effect:

```text
authenticated principal
  + current qualification, when required
  + narrow AuthorizationGrant
  + concrete invocation and runtime state
  + external runtime policy
  -> effective execution capability
```

The effective capability is the intersection of independently held limits:

```text
C_effective = C_application ∩ C_runtime ∩ C_infrastructure
```

An Agent may request a capability expansion. The expansion is a proposal until
an external principal reviews the permission delta, versions the policy, and
applies it. The requesting workload cannot approve its own expansion.

## 3. Three enforcement layers

```text
Model / Agent / application
        |  request, proposal, evidence
        v
Runtime enforcement
        |  mediated and policy-checked effects
        v
Independent infrastructure watchdog
        |  observe, deny, quarantine
        v
External systems and the physical world
```

### Layer 1: application and model

This layer includes prompts, Agent loops, Chat capability selection, Tool
Catalog discovery, budgets, Qualification evaluation, and Run/Step recording.
It provides semantic context and auditability, but it is inside the governed
application's failure domain. It must be treated as compromisable.

### Layer 2: runtime enforcement

This layer mediates filesystem, process, network, credential, and provider
effects independently of model output. It should deny by default, bind
credentials to approved endpoints, make authority narrow and expiring, and
record an enforcement receipt for every allowed or denied boundary crossing.

The current `async`, `thread`, and `process` Tool Execution Backends provide
execution lifecycle and cancellation behavior. They are **not** kernel-level
permission sandboxes. The process backend does not, by itself, close host
filesystem, network, credential, or privilege-escalation paths.

### Layer 3: independent infrastructure watchdog

For high-risk or physical execution, the last enforcer should have a failure
domain that is not contained by the governed workload:

```text
failure_domain(enforcer) ⊄ failure_domain(agent)
```

It may be a separately administered gateway, host/hypervisor control, network
enforcement point, device safety controller, or hardware watchdog. It must be
able to observe, deny, or quarantine without trusting the Agent to call a
cooperative safety API.

`aigc-lite` now ships a vendor-neutral HTTP adapter and Ed25519 receipt
verification seam for this layer, but does not ship or operate the external
enforcer itself. ROS 2 cancellation, a signed runtime receipt, and an
application `AuthorizationGrant` do not replace a safety-rated controller,
emergency stop, collision system, network isolation, or out-of-band watchdog.

## 4. Threats in scope

| Threat | Required response |
|---|---|
| Prompt injection or hostile retrieved content | Untrusted text cannot select policy, add scopes, issue grants, or mutate qualification state. |
| Evaluation awareness and gate gaming | Freeze criteria and inputs; prefer deterministic or orthogonal verification; keep gate ownership outside the generator. |
| Correlated model review | Derive independence from lineage; multiple labels or Agent names do not establish independence. |
| Tool or provider escape | Mediate every cross-boundary effect in an external runtime; deny unmodelled effects. |
| Credential exfiltration | Keep plaintext out of Agent-visible state and bind injection to an approved endpoint and policy revision. |
| Alternate-credential budget escape | A shared service budget covers only mediated calls. Reject raw credential access and direct provider egress in the external runtime; do not count application ledger silence as proof of no spend. |
| Policy self-expansion | Represent changes as permission diffs; require an external principal to approve and apply expansions. |
| Stale evidence or authority | Refresh transitive current-use bindings and expiry before retrieval, export, grant consumption, or dispatch. |
| Replay or substitution | Bind receipts to semantic hashes, invocation digests, policy revisions, runtime identities, and artifact hashes. |
| Indeterminate physical action | Preserve a blocking recovery state until an external observer confirms terminal state. |
| Enforcer compromise or outage | Fail closed for protected effects and keep recovery/quarantine control outside the workload. |

Availability attacks are in scope only where a budget, timeout, quota, or
operator recovery path is specified. This document does not claim protection
against a fully compromised host when the enforcer runs on that same host.

## 5. Security invariants

The concrete transition owners and choke points are enumerated in
[AUTHORITY_TRANSITIONS.md](AUTHORITY_TRANSITIONS.md).

1. Candidate storage is not knowledge admission.
2. Qualification is not authorization.
3. A request for authority is not a grant of authority.
4. Model output cannot change the policy or gate that evaluates that output.
5. Verification binds an immutable revision, never an implicit latest Claim.
6. Every grant is actor/action/target scoped, conditional, bounded, expiring,
   and consumed against the concrete invocation.
7. Every credential is workspace-owned or deployer-owned, never both; runtime
   injection is restricted to the endpoint named by its external policy.
8. Permission expansion requires an explicit diff and an approver outside the
   requesting execution identity.
9. Staleness and taint propagate without erasing historical receipts.
10. An unconfirmed stop is not a successful cancellation and continues to
   block conflicting physical work.
11. A failure candidate is not source-modification authority. A controlled
    patch attempt requires clean-base reproduction, an immutable FailureReceipt,
    and a narrow grant that denies every test, validator, baseline, receipt and
    failure-decision surface used to judge that patch.
12. Individually valid evidence and a closed provenance graph do not establish
    that the evidence entails a target ClaimRevision. Profiles that require
    entailment must verify a revision-bound derivation or remain unresolved.

## 6. Current coverage and gaps

| Boundary | Current state |
|---|---|
| Immutable Claim revisions, evidence closure, Qualification Gate and Receipts | Implemented at the application and database layer. |
| Candidate versus qualified retrieval | Implemented as distinct search paths; imports remain candidate-only. |
| Chat capability selection and Tool Catalog scopes | Implemented and server-owned, but still application-layer enforcement. |
| Narrow AuthorizationGrant consumption | Implemented for delegated and physical dispatch, including invocation restrictions and currentness checks. |
| PolicyProposal and server-derived PermissionDiff | Implemented as an immutable request ledger. Proposals do not approve or apply themselves. |
| EnforcementReceipt ledger and host-owned issuer identity | Implemented as an internal-write/read-only-HTTP seam. A strict external HTTP adapter and Ed25519 receipt verifier bind nonce, request, issuer, execution, policy and time-window claims before persistence. |
| Run, Step, Artifact, Receipt, Review and Finding ledger | Implemented; provides provenance and audit evidence, not prevention by itself. |
| Separate process lifecycle and hard termination | Available for compatible local tools; not a permission sandbox. |
| Kernel filesystem/process/network confinement | Not implemented by the core distribution. Use an external sandbox. |
| Endpoint-bound credential injection enforced below the Agent | Configuration binding exists; complete egress-level enforcement requires an external runtime. |
| Shared provider/service budget across arbitrary worker egress | Not implemented. A process tool can inherit environment variables and make direct network requests outside Tool Catalog accounting. |
| Independent quarantine/watchdog trust domain | Not implemented. |
| Hardware attestation or safety-rated physical interlock | Not implemented and outside the core project's authority. |
| Controlled failure reproduction and source patching | Security contract frozen in [PATCH_RUNNER.md](PATCH_RUNNER.md); no patch runner or source-mutation API is implemented. |

### Collaborative research boundary

Agent-to-Agent communication is transport, not knowledge admission. A peer's
message, copied heuristic, or repeated review remains a candidate tied to its
source Artifact, ClaimRevision, run, principal, and model route. Changing the
model behind one principal does not create a new independent principal; adding
more same-source reviewers does not create independent evidence. A revised
statement starts a new semantic identity and does not inherit the earlier
revision's verification attempts. Default qualified retrieval still requires
an admitted Receipt and an explicit current-use binding. Peer text cannot mint
an `AuthorizationGrant` or bypass tool dispatch checks. The deterministic
regressions are in
[`tests/adversarial/test_swarm_epistemic_boundaries.py`](../tests/adversarial/test_swarm_epistemic_boundaries.py).

The current system records verifier lineage but cannot certify external
organization or trust-domain independence. It also has per-run Agent budgets,
not a shared root budget across a research swarm. Therefore agent count,
message count, reviewer count, and model-route diversity must not be reported
as effective independent search branches or as a swarm-wide safety limit.

### Alternate-credential budget escape

The [RSIGym budget-hacking case](https://arxiv.org/html/2610.10310v1#S5.SS3)
illustrates a distinct failure mode: a workload obtains another usable model
credential and calls the provider directly, bypassing the metered rollout
service. The `aigc-lite` application credential resolver can reject an
unallowlisted `env://` reference, but that does not prevent a spawned worker
from reading its inherited process environment or opening a socket. Tool-call
budgets are not provider-spend budgets.

The synthetic, loopback-only regression in
[`tests/adversarial/test_credential_budget_escape.py`](../tests/adversarial/test_credential_budget_escape.py)
deliberately confirms this **known gap** using a fake canary: one unmediated
worker action makes two direct requests despite the application resolver
rejecting the reference. It demonstrates a reachable unmetered path, not
actual provider spend or a monetary-budget calculation. The complementary
signed-denial test in
[`tests/test_external_enforcer.py`](../tests/test_external_enforcer.py) shows
that a *bound Tool Catalog invocation* is denied with a verified Receipt and
does not fall back to the local provider. Neither test establishes that
arbitrary process egress is blocked in production. That requires a separately
deployed sandbox/network enforcer with no Agent-visible provider credentials,
endpoint-bound injection, deny-by-default egress, and an actual out-of-process
acceptance test. Until then, no shared budget should be described as an upper
bound on all reachable provider use.

## 7. External enforcement integration contract

External sandboxes remain adapters behind `EnforcerAdapter`,
`ToolExecutionBackend`, or a provider boundary; core contracts do not depend
on a particular vendor or kernel implementation. The current contract records
immutable Policy Proposals, conservative server-derived Permission Diffs, and
signed backend-issued Enforcement Receipts. A backend receives an immutable execution
envelope containing at least:

```text
run_id / step_id / workspace_id / principal_id
tool_spec_hash / arguments_digest
authorization_grant_id / grant_digest
allowed filesystem, network, process and provider effects
credential bindings
budgets, deadlines and cancellation mode
runtime policy ID and revision hash
```

It returns an immutable signed payload containing the sandbox and
workload identities, policy hash, image/toolchain digest, observed and denied
effects, credential bindings used, exit/timeout/quarantine state, and an
attestation or external signature when available. The Run ledger stores this
Receipt as evidence; neither the model nor the in-process Agent may mint it.

The current service has no public arbitrary-dispatch or Receipt write route. A receipt is
marked verified only after an Ed25519 signature from a host-configured key and
exact request/issuer/freshness bindings pass. Key registration and adapter
registration remain deployer-owned. Tool execution additionally requires a
deployer-owned binding to the exact workspace/provider/tool/proposal/policy
hash. Uncertain or in-flight dispatches retain the adapter slot until signed
reconciliation; there is no local-provider fallback. This authenticates the signed payload; it
does not make the payload truthful, validate a hardware quote, prove that the
enforcer is independently administered, or guarantee that an indeterminate
workload stopped. Those properties require a domain attestation verifier and
the actual external control plane.

An OpenShell adapter is a plausible deployment behind this seam. It should be
optional and out of process, not copied into core or represented as a feature
already provided by `aigc-lite`.

## 8. Evidence and source status

- NVIDIA's September 28, 2026 announcement describes OpenShell as an enforceable
  runtime boundary outside the model and Agent harness, and Sentry as an
  out-of-band BlueField-4 DPU watchdog in an isolated trust domain. These are
  vendor claims; some announced features may remain staged or platform-specific.
  See the [official announcement](https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Launches-Open-Agent-Safety-Platform-to-Secure-Agents-From-Testing-to-Deployment/default.aspx).
- The Apache-2.0 [OpenShell repository](https://github.com/NVIDIA/OpenShell)
  documents isolated sandboxes, kernel-level file/system-call controls,
  policy-checked egress, endpoint-bound credential injection, and permission
  expansion review. Its limitations and support matrix still apply.
- The Anthropic prospectus language about evaluation awareness and unexpected
  capabilities is currently attributable to a prospectus reviewed by Reuters,
  not to a public SEC filing verified by this project. See the
  [Reuters report](https://ca.marketscreener.com/news/anthropic-warns-ai-may-pose-existential-risks-to-humanity-in-ipo-filing-ce785addd98cf522).
  Anthropic's own [Transparency Hub](https://www.anthropic.com/transparency)
  separately reports models recognizing simulated evaluations and discusses
  risks from increasingly autonomous AI R&D.
