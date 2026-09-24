# Changelog

Release-level changes are recorded here. Development notes and speculative
roadmap items remain in `docs/DESIGN.md`.

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
