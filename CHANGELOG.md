# Changelog

Release-level changes are recorded here. Development notes and speculative
roadmap items remain in `docs/DESIGN.md`.

## Unreleased

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
