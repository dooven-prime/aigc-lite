# Configuration index

`aigc-lite` reads configuration from environment variables and an optional
local `.env`. Never commit a populated `.env`. Production rules are documented
in [`PRODUCTION.md`](PRODUCTION.md).

## Service and persistence

| Variable | Default | Purpose |
|---|---|---|
| `AIGC_LITE_APP_NAME` | `aigc-lite` | Service display name. |
| `AIGC_LITE_HOST` | `127.0.0.1` | Listen address. Non-loopback values activate the production startup guard. |
| `AIGC_LITE_PORT` | `8000` | Listen port. |
| `AIGC_LITE_DEBUG` | `false` | Development diagnostics. Keep disabled in production. |
| `AIGC_LITE_DATA_DIR` | `data` | SQLite/runtime data directory. |
| `AIGC_LITE_DATABASE_URL` | empty | SQLAlchemy PostgreSQL URL; empty selects SQLite. |

## Authentication and tenancy

| Variable | Default | Purpose |
|---|---|---|
| `AIGC_LITE_AUTH_SECRET` | insecure example | Session signing secret; production requires a non-default 32+ character value. |
| `AIGC_LITE_MASTER_KEY` | empty | Fernet key for encrypted workspace credentials. |
| `AIGC_LITE_ALLOW_SIGNUP` | `true` | Public registration; must be `false` for non-loopback startup. |
| `AIGC_LITE_ADMIN_EMAIL` / `AIGC_LITE_ADMIN_PASSWORD` | empty | Bootstrap administrator pair; production password must be 12+ characters. |
| `AIGC_LITE_SESSION_TTL_HOURS` | `168` | Session lifetime. |
| `AIGC_LITE_RATE_LIMIT_PER_MINUTE` | `60` | General request rate limit; not a dedicated login lockout. |
| `AIGC_LITE_API_KEY` | empty | Single-tenant compatibility API key. |
| `AIGC_LITE_TENANTS_JSON` | empty | Static tenant/API-key list. |

Local single-workspace mode can leave the API key empty. With
`AIGC_LITE_API_KEY` enabled, requests use `Authorization: Bearer <key>`.
Multiple static tenants can be declared as JSON; every persisted query is still
scoped by the resolved `tenant_id`:

```dotenv
AIGC_LITE_TENANTS_JSON=[{"id":"team-a","name":"Team A","api_key":"team-a-secret"},{"id":"team-b","name":"Team B","api_key":"team-b-secret"}]
```

Generate the credential encryption key independently from the login signing
secret:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Store the output as `AIGC_LITE_MASTER_KEY`. Existing legacy ciphertext can be
read during migration, but new writes use the authenticated `enc:v1:` format.

## Model access

| Variable | Default | Purpose |
|---|---|---|
| `AIGC_LITE_LLM_API_KEY` | empty | Platform default route credential only. It is never inherited by a workspace endpoint. |
| `AIGC_LITE_LLM_BASE_URL` | OpenAI API | Platform default OpenAI-compatible HTTPS endpoint. |
| `AIGC_LITE_LLM_MODEL` | `gpt-4o-mini` | Platform default upstream model. |
| `AIGC_LITE_LLM_TIMEOUT` | `120` | Model request timeout in seconds. |
| `AIGC_LITE_DEFAULT_INPUT_PRICE` / `AIGC_LITE_DEFAULT_OUTPUT_PRICE` | `0` | Default usage prices. |

Workspace model records are API-managed and require an active same-workspace
`encrypted-db://credential/UUID`. Their endpoint and credential are atomic; an
empty workspace credential never falls back to `AIGC_LITE_LLM_API_KEY`.

MiniMax Token Plan uses its OpenAI-compatible endpoint and M-series model, for
example:

```dotenv
AIGC_LITE_LLM_API_KEY=your-sk-cp-key
AIGC_LITE_LLM_BASE_URL=https://api.minimaxi.com/v1
AIGC_LITE_LLM_MODEL=MiniMax-M3
```

The provider adapter separates MiniMax thinking content from final `content`,
so reasoning tags do not enter the final chat answer or the Agent tool loop.

## Agent, tools, and scheduling

| Variable | Default | Purpose |
|---|---|---|
| `AIGC_LITE_MAX_AGENT_STEPS` | `8` | Model-turn budget. |
| `AIGC_LITE_MAX_AGENT_TOOL_CALLS` | `16` | Tool-call budget. |
| `AIGC_LITE_MAX_AGENT_RUN_SECONDS` | `300` | Run wall-clock budget. |
| `AIGC_LITE_DEFAULT_TOOL_TIMEOUT_SECONDS` | `30` | Default single-tool timeout. |
| `AIGC_LITE_MAX_TOOL_RESULT_CHARS` / `AIGC_LITE_MAX_TOOL_RECORD_CHARS` | `100000` / `50000` | Tool return/ledger bounds. |
| `AIGC_LITE_ADMIN_TOOL_SCOPES` | empty | Additional scopes granted to workspace admins. |
| `AIGC_LITE_HTTP_POLL_ALLOWED_HOSTS` | empty | Exact destination allowlist for `http.poll`. |
| `AIGC_LITE_SCHEDULER_ENABLED` | `true` | Enable scheduler recovery and dispatch. |
| `AIGC_LITE_SCHEDULER_TICK_SECONDS` | `1` | Time-wheel tick. |
| `AIGC_LITE_SCHEDULER_RECONCILE_SECONDS` | `5` | Persistent-state reconciliation interval. |
| `AIGC_LITE_SCHEDULER_MAX_CONCURRENCY` | `4` | Local scheduler concurrency. |

## MCP

| Variable | Default | Purpose |
|---|---|---|
| `AIGC_LITE_MCP_SERVERS_JSON` | empty | Deployer-owned static remote server definitions. |
| `AIGC_LITE_MCP_ENV_CREDENTIAL_ALLOWLIST` | empty | Exact environment names static MCP headers may reference. |
| `AIGC_LITE_MCP_ALLOWED_HOSTS` | loopback patterns | Inbound Host allowlist. |
| `AIGC_LITE_MCP_ALLOWED_ORIGINS` | empty | Inbound Origin allowlist. |
| `AIGC_LITE_MCP_API_KEY` | empty | Legacy default-workspace compatibility key. |
| `AIGC_LITE_LEGACY_MCP_PROTOCOL_VERSION` | `2025-06-18` | Hand-written `/mcp-legacy` only; official transports negotiate through the SDK. |

`env://` is not accepted in workspace-persisted MCP records. Static environment
references are deployment authority and fail closed unless explicitly
allowlisted.

## Formal kernels

| Variable | Default | Purpose |
|---|---|---|
| `AIGC_LITE_LEAN_EXECUTABLE` / `AIGC_LITE_COQ_EXECUTABLE` | empty | Server-owned absolute verifier executable paths. |
| `AIGC_LITE_KERNEL_VERIFY_TIMEOUT_SECONDS` | `30` | Verifier wall-clock limit. |
| `AIGC_LITE_KERNEL_VERIFY_MAX_SOURCE_BYTES` | `500000` | Input source bound. |
| `AIGC_LITE_KERNEL_VERIFY_MAX_OUTPUT_BYTES` | `100000` | Captured output bound. |
| `AIGC_LITE_KERNEL_VERIFY_MEMORY_MB` | `512` | Requested process memory boundary where supported. |

Kernel execution is bounded host process execution, not an OS filesystem or
network sandbox.
