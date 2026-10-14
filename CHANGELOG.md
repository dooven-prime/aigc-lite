# Changelog

Release-level changes are recorded here. Development notes and speculative
roadmap items remain in `docs/DESIGN.md`.

## Unreleased

This is a development ledger for changes merged after `v0.6.0` through
`main` commit `be13369` (2026-10-14), plus the separately marked candidate
branch delta. The source metadata targets `0.7.0`, but this is **not** a tagged
release or a completed `v0.7.0` release closure.

### Scope on main

- Added versioned research source importers and candidate-only OpenAI math
  catalog ingestion. The fixed Math Project 003 path pins source and dependency
  bytes, separates paper-to-challenge alignment from challenge-first Comparator
  and Lean replay, and records bounded build/sandbox diagnostics.
- Added authenticated, policy-bound Invalidation Decisions. A notice or failed
  review is evidence for a decision, not its authority; decisions retain exact
  revision/receipt/attempt targets and historical receipts while propagating
  stale current-use bindings.
- Added the finite RIME consumer witness Case, a pinned r2 replay ZIP and host
  pre-execution checks, and a separate narrow finite reference oracle. Added
  an isolated Fibre Criterion Lean delivery verifier and public source locks;
  neither path is joined to Math Project 003 qualification.
- Added candidate-only citation extraction with occurrence, bibliography-match,
  and claim-relation boundaries; a sealed deterministic blind-evaluation
  protocol; retrospective paired in-vitro arithmetic and signed L1 record
  binding; and frozen Bio/Citation information-sufficiency counterexample
  Cases. These domain Cases do not introduce a new authority primitive.
- Added propositional-entailment and other adversarial regressions for invalid
  evidence combination, source identity, withdrawal propagation, correlated
  agent agreement, and credential-budget escape.
- Added replayable ROS 2 simulator fault-injection evaluation and a narrow
  Nav2 graph evaluation path. Aligned SSE Chat with the synchronous tool loop,
  tightened scheduler multi-worker lease/idempotency behavior, and improved
  the manual chat/research workflow UI without changing MCP's scoped projection
  role.
- Moved candidate-only Bio/Citation Minimal Disambiguating Evidence v0
  diagnostics into an optional casepack. Previously frozen v1 Case locks and
  domain contracts remain unchanged; the core does not auto-discover the pack.
- Added service-level, session-bound administrator checks for knowledge
  admission, AuthorizationGrant issuance, invalidation writes, applied
  workflow transitions, and external-enforcer reconciliation. Session
  revocation and protected writes now have a transactional ordering. A
  consumed Grant is bound to one exact external invocation and dispatch; an
  indeterminate result keeps its execution slot. These application checks do
  not constitute OS isolation from arbitrary in-process code with database
  access.

### Current candidate delta (not yet on main)

- Normalized integer and float tool timeouts before hashing an authorization
  invocation. Previously a bound tool could spend its Grant and then fail the
  external-dispatch digest comparison for an integer timeout.
- Set the core and ROS 2 package metadata to the `0.7.0` release candidate.
  The optional MDE casepack must pin its first compatible core release
  exactly; compatibility with the tagged `v0.6.0` core was never established.

### Verification recorded so far

- The artifact-specific `rime-consumer-finite-replay-r2` handoff records an
  Ubuntu same-checker replay of three positive witnesses and seven negative
  controls with expected verdicts and reasons. The independently written
  finite oracle covers a narrower origin/history/carrier property. Its ZIP,
  manifest, source identities, and unsigned runtime receipt are identified in
  `docs/releases/rime-consumer-finite-replay-r2.md`.
- The Fibre Criterion candidate pins seven Lean declarations and their expected
  axiom sets. Local and cross-host same-verifier Lean 4.33.0 replays were
  reported; `docs/FIBRE_CRITERION_CASE.md` states the exact source and
  verification boundary. The Bio/Citation two-world regressions and locks
  exercise synthetic observation-equivalence cases, not external provenance.
- For `91c38f7`, a Python 3.12 cloud checkout reported `400 passed, 3 skipped`
  with Ruff and byte compilation passing. That report is development test
  evidence; it is not a committed test receipt or the full release checklist.
- Before this candidate's version metadata changed, the merged authority
  branch reported `480 passed, 14 skipped` locally, with focused live
  PostgreSQL contract tests, Ruff, and byte compilation passing. The timeout
  normalization then passed 24 handoff tests and 11 neighboring tests (2
  opt-in PostgreSQL tests skipped), plus Ruff and byte compilation. These are
  development checks. The final candidate code commit's Python 3.12 suite,
  three clean-install wheels, frontend audit/build, PostgreSQL 17/18 contract,
  and hardened/insecure Docker checks are recorded separately in
  `docs/releases/v0.7.0-candidate.md`; that local evidence is not a tag or
  published release. GitHub CI for the pushed candidate remains a separate
  acceptance check.

### Non-claims and release gate

- Math Project 003 has **no passing full Comparator/kernel replay**; statement
  identity and kernel results remain undetermined after a bounded cold build
  timed out. A cache hash, paper review of a different revision, or a weaker
  theorem cannot substitute for the frozen `7/8` challenge.
- A valid finite replay or Lean artifact does not establish checker correctness,
  the intended paper theorem, organizational independence, knowledge admission,
  or execution authorization. The RIME r2 runtime receipt is unsigned, and
  Fibre Criterion is not a general image/kernel theorem.
- Correct arithmetic or signed record binding does not authenticate a complete
  experiment or biological independence. Citation anchors and bibliography
  candidates do not establish real paper identity, claim support, or independent
  evidence roots. The citation blind cohort remains **unscored** until complete
  independent human Gold is sealed.
- Simulator/graph acceptance is not real Nav2/Gazebo or hardware acceptance.
  The application does not provide a complete OS sandbox or external
  trust-domain attestation. Candidate imports and diagnostic Cases cannot
  issue QualificationReceipts, CurrentUseBindings, or AuthorizationGrants.
- Do not tag the next software release from this ledger alone. Recheck the
  final source and dependency identities, full test/Ruff/byte-compilation
  results, clean-install wheels, frontend audit/build, live PostgreSQL contract,
  and hardened/insecure Docker smokes; then write a separate versioned release
  closure and tag its exact commit.

## 0.6.0 - 2026-09-30

- Made `/mcp/` the canonical Streamable HTTP endpoint and internally
  canonicalized the exact `/mcp` compatibility path, so protocol POSTs do not
  depend on redirects that the default MCP HTTP client does not follow.
- Frozen the planned Controlled Patch Runner contract without exposing source
  mutation: clean-base independent reproduction and an immutable
  FailureReceipt must precede any narrow write grant, and the patching identity
  cannot modify the tests, validator, baseline, receipt, or failure predicate
  that judges its own patch.
- Frozen the first Authority Separation Baseline. VerificationPlan now defaults
  `auto_promote` to false; the retained compatibility flag can only request a
  proposal-only Promotion Gate evaluation. Agent/plan execution cannot apply a
  workflow transition, while the explicit administrator control route remains
  the sole application caller that requests `apply_transition=True`.
- Added the normative authority-transition matrix and an adversarial authority
  suite. Hostile Agent verification output cannot smuggle qualification,
  admission, current-use, authorization, or release fields through the frozen
  result contract.

- Split orthogonal verification from epistemic independence. Lean/Coq kernel
  success now satisfies `orthogonal_verification` while
  `independent_validation` remains undetermined until distinct authority,
  organization/trust-domain, and reproduction lineage can be established.
- Added migration `0020_knowledge_admission`, immutable
  `KnowledgeAdmissionReceipt` records, a server-owned admission policy, and an
  explicit admin transition from QualificationReceipt to the current knowledge
  view. `ADMITTED` no longer creates a CurrentUseBinding, and the database
  rejects current knowledge bindings without a matching admission receipt.
- Added a vendor-neutral `EnforcerAdapter` port, a hardened out-of-process HTTP
  adapter, nonce/expiry-bound request contracts, a host-owned Ed25519 keyring,
  and strict signed Enforcement Receipt verification. Verified receipts bind
  issuer, request, workspace, Run/Step/Proposal, policy, execution envelope,
  ToolSpec and argument digests before immutable persistence; legacy or merely
  signature-bearing receipts remain explicitly unverified.
- Added Alembic revision `0018_enforcer_signatures` and read-only operational
  projections for registered adapters and verification-key fingerprints. No
  public endpoint can register trust roots, dispatch arbitrary workloads, or
  write receipts.
- Added deployer-owned `ExternalToolExecutionBinding` projection into the Tool
  Catalog. Bound tools execute only through the external adapter and never
  fall back to their ordinary provider.
- Added migration `0019_enforcement_dispatches`, persistent
  `dispatching/terminal/indeterminate/reconciled` state, one active root
  workload per adapter, and admin-only signed reconciliation of existing
  uncertain dispatches.

## 0.5.0 - 2026-09-30

- Added immutable `PolicyProposal`, server-derived `PermissionDiff`, and
  backend-issued `EnforcementReceipt` contracts. Policy requests freeze
  deny-by-default base/candidate revisions without approving or applying them;
  conservative diffs expose every expansion. Receipts have no public write
  route, bind execution and policy digests to host-registered issuer identity,
  remain workspace-scoped and immutable, and deduplicate retry writes by hash.
- Added an explicit threat model built around two authority planes and three
  enforcement layers. It assumes governed Agents may understand their gates,
  prevents capability growth from implying authority growth, inventories the
  current in-process controls versus external sandbox/watchdog gaps, and
  defines a vendor-neutral enforcement receipt seam for future runtimes.
- Closed four authority/physical-execution races found during review: Nav2 now
  owns and cancels goals accepted after a dispatch timeout, indeterminate stop
  results continue blocking new motion, Assurance export refreshes transitive
  current-use bindings, and Tool authorization enforces invocation scope,
  conditions, and budgets before atomically consuming the exact matching grant.
- Added protocol-level coverage requiring every discovered MCP tool to emit all
  four boolean behavior annotations. The wheel's minified React asset remains a
  reproducible packaged build; its readable source is maintained in
  `frontend/src`.
- Added a versioned Conversation Import Registry with ChatGPT and DeepSeek
  adapters, stateless preview hashes, atomic immutable ImportBatch commits,
  content-addressed raw source Artifacts, complete parent/branch/citation/
  attachment provenance, and candidate-only FTS5/lexical search projection.
  Imports do not create qualification, current-use, or authorization records.
- Added a server-owned `ChatCapabilityPolicy`. Chat now defaults to the
  `chat.read-only.v1` capability set, strips caller administration scopes, and
  exposes only low-risk, read-only, non-destructive, closed-world local or
  workspace tools. Explicit `chat.delegated.v1` selection requires
  `tools:write`; every remote MCP or side-effecting invocation additionally
  consumes a current narrow `AuthorizationGrant`. Runs and Steps freeze the
  selected policy ID/hash and effective scopes for later review.
- Added a versioned Review Profile Registry, the deterministic
  `execution.integrity.v1` profile, and an immutable ReviewRun/ReviewFinding
  ledger. Reviews freeze privacy-bounded execution digests and evidence refs,
  remain workspace-scoped, and cannot mutate qualification or authorization.
- Added a context-bound, AI-first `WorkspaceCapabilityProvider` to the default
  Tool Catalog. MCP clients and Agents can now search execution memory and
  qualified knowledge, and read Runs, Artifacts, ClaimRevisions,
  Qualification Receipts, and profiles through seven bounded read-only tools;
  calls retain workspace isolation and the normal Run/Step ledger.
- Reduced the root README to project positioning, quick start, security
  boundaries, and a documentation map. Detailed HTTP, execution/scheduling,
  MCP/tool, and qualification/assurance guidance now lives in focused files
  under `docs/`.
- Continued the domain architecture split by moving Research Registry, Decision
  Lab, Qualification Plane, kernel verification, authorization grant, and
  Assurance Bundle HTTP contracts into an explicitly injected `api.research`
  router without changing their persisted evidence or authority semantics.
- Split the relational repository into explicit Research and Qualification
  ports plus SQLite/PostgreSQL domain mixins. The existing `Repository`,
  `SQLiteRepository`, and `PostgresRepository` names remain compatibility
  facades, so application services keep the same transactional behavior.

## 0.4.1 - 2026-09-29

- Fixed the hardened Docker CI smoke so authentication and encryption use
  independently generated valid secrets.
- Added a fail-closed physical execution authorization gate. ROS 2 motion now
  consumes a current, actor/action/provider/robot-bound `AuthorizationGrant`
  atomically before provider dispatch, records the consumed grant in the Tool
  Step, and rejects missing, expired, exhausted, stale, or superseded grants.
- Prevented authorization grants from binding an older Qualification Receipt
  when a newer receipt owns the current-use binding, and excluded expired or
  exhausted grants from portable Assurance Bundle authority.
- Made dependency taint propagation recursive, cycle-safe, and duplicate-edge
  safe so transitive stale qualifications cannot remain current.
- Made the theorem verifier select the newest policy-valid Lean/Coq kernel
  certificate instead of being pinned by an older invalid certificate.
- Preserved the default model-provider flag when workspace administrators
  rebind an existing provider to an encrypted credential.

## 0.4.0 - 2026-09-29

- Closed two credential-confusion boundaries: workspace MCP records now accept
  only same-workspace encrypted credential references, while deployer-owned
  static `env://` references require an explicit environment-variable
  allowlist; workspace model endpoints now require their own encrypted
  credential and never inherit the platform LLM key.
- Added fail-closed non-loopback startup checks, a loopback-only default
  Compose port, and hardened bootstrap requirements for public binds.
- Made the React application the canonical packaged UI, added wheel and Docker
  UI smoke tests, exact frontend direct dependency versions, and reproducible
  frontend-to-package synchronization.
- Added live PostgreSQL migration/repository contract coverage plus core wheel,
  ROS 2 simulator wheel, and container startup jobs to CI.
- Added a fail-closed `/ready` projection for database connectivity, exact
  Alembic head, and scheduler loop state; container smoke now gates on it while
  `/health` remains dependency-free liveness.
- Started the post-closure architecture split by moving the credential/model/MCP
  configuration surface into an injected FastAPI router and the matching model
  configuration UI into its own view module.
- Added real Lean 4 and Coq KernelVerifier process backends. Server-owned
  executable selection, bounded output, wall-clock termination, cancellation,
  executable/toolchain identity, `#print axioms`/`Print Assumptions` closure,
  and fail-closed placeholder detection now produce linked Run, Step, proof and
  certificate Artifacts, Receipt, and server-derived Verification Attempt.
  The certificate explicitly discloses that the bounded host process is not an
  OS network/filesystem sandbox.
- Added the Qualification Plane with a versioned Domain Verifier Registry and
  the first `math.formal.v1` theorem profile. Exact ClaimRevision semantic
  hashes, Merkle-like evidence closures, deterministic criterion receipts,
  immutable Qualification Receipts, CurrentUse bindings, and separate narrow
  Authorization Grants prevent storage or workflow admission from becoming a
  trust/authority flag.
- Verification independence is now derived from server-bound verifier lineage
  rather than accepted from request booleans. Added qualified-only search and
  qualification/authorization closure to portable Assurance Bundles.

- Added deterministic, read-only Research Case Assurance Bundle export with
  content-addressed Artifact payloads, a closed member manifest, explicit
  limitations and unsigned-signature status, plus `aigc-lite verify` for
  bounded directory/ZIP verification without a database or network.
- Replaced boolean-only verification independence as a promotion authority
  with a structured overlap disclosure and explicit qualification basis.
  Legacy `independent=true` rows remain visible but fail closed. Verification
  executions and promotion gates now retain their frozen input snapshots so
  offline verifiers can recompute historical digests after Claim state moves.
- Started the `0.4.0` Physical Capability Bridge as a separately installable
  `aigc-lite-ros2` package rather than adding ROS/DDS dependencies to core.
- Added bounded `get_state`, `inspect`, `navigate_to`, and `cancel_action` MCP
  capabilities with high-risk motion scopes, idempotency conflict detection,
  cancellation propagation, bounded feedback, simulation/hardware identity,
  and versioned physical action/observation receipts.
- Added a deterministic simulator for CI and failure/indeterminate demos plus a
  lazy ROS 2/Nav2 adapter using NavigateToPose Action cancellation and TF2 pose
  observations. Live ROS graph and hardware validation remain external.
- Added safe remote MCP policy metadata: tools can only elevate configured
  risk, add required scopes, or shorten provider timeouts; bounded extension
  metadata is retained in Tool Steps and inbound MCP discovery.
- Added explicitly configured administrator tool scopes so physical motion
  permissions remain disabled by default.

## 0.3.0 - 2026-09-28
- Added a workspace-isolated encrypted Credential Store with write-only API,
  revocation, replacement, and `encrypted-db://credential/UUID` references for
  remote MCP headers.
- Added Agent model-turn and tool-call budgets, whole-run wall-clock limits,
  stable `limit_reached` outcomes, and cancellation propagation through model
  and remote MCP waits with an explicit active-run cancellation endpoint.
- Added pluggable local Tool Execution Backends: cooperative async execution,
  soft-cancelled threads, and disposable spawned processes with hard timeout
  termination and execution/cancellation metadata.
- Added persistent one-time and interval schedules, startup recovery through a
  hierarchical time-wheel backend, registered `TaskRunner` dispatch, and
  workspace-admin create/query/pause/resume/cancel APIs with audit events.
- Added daily/weekly schedule API shorthand and MiniMax OpenAI-compatible
  reasoning separation so final Agent content excludes embedded think blocks.
- Registered `mcp.probe`, policy-filtered `tool.call`, and allowlisted
  content-free `http.poll` as independent scheduled TaskRunner targets.
- Added workspace-scoped Artifact/Citation storage linked to Run/Step records,
  Run detail projection, unified search, and automatic capture of structured or
  resource-bearing remote MCP tool results.
- Added an injectable `SearchBackend`, a migration-backed SQLite FTS5 index with
  existing-data backfill and source-table synchronization triggers, plus a safe
  lexical fallback for short queries and runtimes without FTS5.
- Added the first execution-memory UI slice: a responsive Run Explorer with run
  filtering, status and duration summaries, Step timelines, bounded payload
  previews, and linked Artifact/Citation inspection.
- Added a unified workspace search UI across conversations, knowledge, Run
  Steps, Artifacts, and Citations, with direct Run/Step/Artifact navigation and
  focused result highlighting in Run Explorer.
- Added a cross-domain evidence control layer for versioned Protocols, scoped
  Claims, execution Receipts, explicit Reviews, and content-addressed Freeze
  manifests. Epistemic outcomes preserve insufficient/undetermined/rejected as
  distinct states.
- Added a read-only NanoJev bundle importer and Decision Lab for Boolean,
  Choice, and Score distributions, confidence/entropy review thresholds,
  fail-closed schema validation, frozen artifacts, and unified-search links.
- Added a versioned Research Registry and AI Frontier importer with normalized
  Claim Revisions and source references, explicit closure blockers, immutable
  import receipts/freezes, a Claim Explorer UI, and unified-search navigation.
- Added typed Claim Relations with dependency-cycle rejection, receipt-bound
  Verification Attempts, and immutable fail-closed Promotion Gate evaluations
  across registered, evidence-ready, review-ready, and release-ready stages.
- Added immutable, versioned Verification Plans and a `research.verify` Agent /
  scheduler runner that validates a strict result contract and automatically
  links Run, Step, Artifact, Receipt, Verification Attempt, and Promotion Gate.
  Invalid output, upstream failure, and cancellation remain queryable and fail
  closed; Agent executions never self-declare independent review.
- Restricted workspace administrators to their own tenant and removed the
  unsupported cross-workspace tenant creation surface.

## 0.2.0 - 2026-09-24

- Positioned the project as a self-hosted AI workspace and Agent/MCP runtime
  with searchable execution memory.
- Added workspace-isolated sessions, knowledge, Agent Run/Step records, usage,
  audit records, and unified search.
- Added `GatewayService`, stable application errors, and shared core contracts.
- Added the Tool Catalog with local and remote MCP discovery, scope/risk
  filtering, timeouts, output bounds, redaction, and Step recording.
- Added official MCP SDK Streamable HTTP and SSE transports, inbound workspace
  projection, persistent remote-server configuration, and health probes.
- Added independent versioned credential encryption, centralized ledger/audit
  redaction, and Alembic-managed SQLite/PostgreSQL migrations.
- Added a lightweight React workspace UI and container deployment files.

This is the first frozen repository baseline. Earlier work was not published as
a versioned repository release.
