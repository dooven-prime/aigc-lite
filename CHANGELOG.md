# Changelog

Release-level changes are recorded here. Development notes and speculative
roadmap items remain in `docs/DESIGN.md`.

## Unreleased

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
