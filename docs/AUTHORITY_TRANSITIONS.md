# Authority transition contract

This document is the normative inventory of state changes that can affect
epistemic or execution authority. It is intentionally smaller than the domain
model: a new Artifact, Claim, Attempt, Review, or proposal is evidence, not an
authority transition.

The governing rule is:

> Model and tool output may create candidates and evidence proposals. They may
> not apply semantic, qualification, knowledge, policy, or execution authority.

## Transition matrix

| Transition | Initiator | Gate | Persistent result | Agent path |
|---|---|---|---|---|
| bytes -> candidate Artifact/ClaimRevision | authenticated import/register operation | schema, workspace and content hash validation | candidate record | may propose content; cannot mark it qualified |
| VerificationAttempt -> workflow evaluation | administrator or immutable VerificationPlan execution | deterministic Promotion Gate | immutable evaluation | may trigger proposal-only evaluation |
| workflow evaluation -> `promotion_stage` | explicit administrator control request | next-stage, frozen-attempt and relation criteria | conditional stage update plus evaluation | denied; `auto_promote` never applies the transition |
| ClaimRevision -> QualificationReceipt | administrator-triggered deterministic Qualification Gate | versioned Domain Verifier Profile over frozen evidence closure | immutable ADMITTED receipt | cannot choose verdict or mint receipt |
| QualificationReceipt -> current knowledge | explicit workspace administrator | server-owned Knowledge Admission policy and currentness check | immutable KnowledgeAdmissionReceipt plus atomic CurrentUseBinding | no Chat/MCP/tool capability |
| current qualification -> AuthorizationGrant | explicit workspace administrator | exact current receipt, actor/action/target/scope/conditions/expiry/budget | bounded grant receipt | no Chat/MCP/tool capability |
| invocation -> effective execution | authenticated runtime identity | Tool Catalog policy plus exact grant; external binding where configured | consumed grant, Run/Step, optional signed EnforcementReceipt | may request; cannot expand the envelope |
| PolicyProposal -> effective runtime policy | external policy authority | outside the current core distribution | external policy revision and receipt | proposal only; no apply API |

`promotion_stage` is workflow metadata, not epistemic qualification. The legacy
VerificationPlan field `auto_promote` is retained for stored/API compatibility;
`true` means “automatically evaluate the next workflow gate as a proposal.” It
does not authorize the Verification Runner to update the stage.

## Choke points

- `ResearchRegistryService.evaluate_promotion()` defaults to proposal-only;
  only the administrator HTTP control route requests `apply_transition=True`.
- `QualificationService.evaluate()` owns deterministic qualification verdicts.
  ADMITTED creates a historical receipt and no current binding.
- `QualificationService.admit_knowledge()` is the only supported application
  transition into the qualified retrieval view.
- the database rejects a current knowledge binding without a matching immutable
  KnowledgeAdmissionReceipt for an ADMITTED QualificationReceipt;
- `QualificationService.create_authorization()` requires the exact receipt
  selected by a refreshed current binding;
- `ToolAuthorizationGate` validates concrete invocation restrictions and
  atomically consumes the chosen grant before dispatch;
- workspace MCP capabilities are read-only and carry
  `authority_mutation=false`.

The planned controlled source-maintenance transition is separately frozen in
[PATCH_RUNNER.md](PATCH_RUNNER.md). Until that contract is implemented, a
FailureReceipt, ReviewFinding, failed Run, or model proposal grants no source
write capability.

## Adversarial conformance

`tests/adversarial/` treats model, tool, import, and remote-provider content as
hostile. The suite must grow from discovered failures rather than by adding new
authority objects. Its baseline asserts that:

- authority-shaped fields in Agent verification JSON fail the frozen result
  contract;
- VerificationPlan defaults do not request even an automatic gate evaluation;
- a successful proposal-only gate leaves workflow stage unchanged;
- only an explicit apply transition changes workflow stage;
- Qualification, admission, current-use, authorization, stale-evidence and
  external-enforcer negative paths remain covered by their domain tests.

Real RIME cases, Lean companion workflows, and research Agents should attack
this contract through public transports and registered capabilities. A failure
becomes a regression fixture first. A new core object is justified only when
multiple independent domains cannot express the same necessary distinction
with the existing Claim, Artifact, Attempt, Receipt, Profile, policy, and
binding contracts.
