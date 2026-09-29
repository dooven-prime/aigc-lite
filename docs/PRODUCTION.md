# Production deployment checklist

This document defines the current production boundary. It is an operator
checklist, not a claim that every deployment using it is secure.

## Before binding beyond loopback

The process refuses a non-loopback bind unless all of these are true:

- `AIGC_LITE_ALLOW_SIGNUP=false`;
- `AIGC_LITE_AUTH_SECRET` is non-default and at least 32 characters;
- `AIGC_LITE_MASTER_KEY` is a valid independently generated Fernet key;
- any static tenant API keys are non-default and at least 16 characters;
- a bootstrap administrator, an existing user, or a tenant API key provides an
  authentication principal.

Compose publishes `127.0.0.1:8000` by default even though the process listens
on the container interface. Put TLS and an authenticated reverse proxy in
front of it before changing the published address.

## Credential boundaries

- Workspace model and MCP records may reference only active credentials owned
  by that workspace through `encrypted-db://credential/UUID`.
- Static MCP `env://NAME` references are deployer-only and require the exact
  name in `AIGC_LITE_MCP_ENV_CREDENTIAL_ALLOWLIST`.
- A workspace model endpoint never inherits the process-wide LLM key. Bind an
  endpoint to its own credential or the request fails closed.
- Back up the database and matching master key together. Never log, export, or
  place decrypted credentials in an Assurance Bundle.

Migration `0013_model_credential_binding` intentionally clears legacy model
configuration `api_key` columns. After upgrading, administrators must create
Credential Store records and explicitly rebind every workspace model route.

## Release smoke checks

From a clean checkout:

```text
python -m pytest -q
ruff check app tests extensions/ros2-bridge/src
python -m compileall -q app
cd frontend && npm ci && npm audit --omit=dev --audit-level=high && npm run build
python scripts/sync_frontend.py
python -m pip wheel . --no-deps --wheel-dir <temporary-directory>
python -m pip wheel extensions/ros2-bridge --no-deps --wheel-dir <temporary-directory>
docker build -t aigc-lite:<version> .
```

Install each wheel into a clean environment outside the checkout. The core
wheel must serve `/ui/` with the React marker; the ROS 2 wheel smoke covers only
the deterministic simulator. A passing simulator test is not Nav2, Gazebo, DDS,
robot, or safety acceptance.

The CI PostgreSQL job migrates a clean PostgreSQL 17 database and exercises
credential-bound model/MCP records and Run persistence. SQLite remains the
quick-start backend.

## Backup and restore drill

1. Stop writes or take an application-consistent database snapshot.
2. Back up the database and the exact `AIGC_LITE_MASTER_KEY` separately with
   access control and retention metadata.
3. Restore into an isolated environment, run Alembic to the expected head, and
   verify login plus a non-secret credential metadata listing.
4. Revoke test sessions/keys and destroy the isolated restore after recording
   the drill result.

Losing the master key makes encrypted credentials unreadable; restoring the
key without the matching database is not useful.

## Current operational limitations

- `/health` is dependency-free liveness. Use `/ready` for database connectivity,
  exact Alembic head, and scheduler loop state; it returns 503 on a failed check
  without exposing database exception text.
- Login-specific lockout, logout, session revocation, password change, and
  expired-session cleanup APIs are not yet complete.
- Scheduler execution is process-local and does not claim multi-worker leasing.
- Python thread tools cannot be forcibly terminated; use process mode or an
  external sandbox where hard cancellation is required.
- Endpoint validation and disabled redirects reduce credential confusion but
  do not constitute complete DNS-rebinding/private-network SSRF protection.
- Tenant self-service data export and verified deletion workflows are not yet
  implemented. Operators must satisfy retention/deletion obligations through
  controlled database procedures and document the result.

Do not describe these open items as accepted release properties.
