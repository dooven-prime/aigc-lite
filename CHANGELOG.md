# Changelog

Release-level changes are recorded here. Development notes and speculative
roadmap items remain in `docs/DESIGN.md`.

## Unreleased

- Development version: `0.3.0-dev.0`.
- Added a workspace-isolated encrypted Credential Store with write-only API,
  revocation, replacement, and `encrypted-db://credential/UUID` references for
  remote MCP headers.
- Added Agent model-turn and tool-call budgets, whole-run wall-clock limits,
  stable `limit_reached` outcomes, and cancellation propagation through model
  and remote MCP waits with an explicit active-run cancellation endpoint.
- Added pluggable local Tool Execution Backends: cooperative async execution,
  soft-cancelled threads, and disposable spawned processes with hard timeout
  termination and execution/cancellation metadata.
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
