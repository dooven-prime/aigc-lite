# Repository working rules

- Never commit `.env`, credentials, databases, logs, runtime evidence, or local
  proxy/private-host configuration.
- Workspace-persisted credentials use only
  `encrypted-db://credential/UUID`; keep `env://` deployer-owned and allowlisted.
- Add schema changes as Alembic revisions and cover both SQLite and PostgreSQL
  behavior where SQL semantics differ.
- Treat HTTP application/control APIs as the programmatic main path. Keep
  Contract, Application, Port, and Adapter responsibilities separate; source
  importers must be versioned and must not grant qualification or current use.
- Keep MCP as a scoped projection of application capabilities for external
  agents, not a second implementation of business or authority rules.
- Treat `frontend/src` as UI source and `app/static` as the packaged build.
  After UI changes run `npm ci`, `npm run build`, and
  `python scripts/sync_frontend.py`; commit both source and packaged assets.
  Maintain the UI as a manual operations surface; do not expand it by default
  when an HTTP workflow is sufficient.
- For frozen evaluations, record the dependency version actually loaded by the
  freeze process. A compatible `pyproject.toml` range is not an environment
  pin; never rewrite historical manifests to match a later virtual environment.
- Keep core independent of ROS 2. Physical capabilities belong in
  `extensions/ros2-bridge` behind MCP/Tool Catalog contracts.
- Before release, run tests, Ruff, byte compilation, wheel clean-install smoke,
  frontend audit/build, PostgreSQL contract, and Docker hardened/insecure smoke.
- Preserve the distinction between candidate storage, epistemic qualification,
  current-use binding, and execution authorization. Model output cannot mutate
  these authorities directly.
- New research Cases default to separately installable `casepacks/` or
  extensions, with versioned contracts and no automatic authority transition.
  Do not add a core authority primitive unless at least two independent
  domains cannot express the same need using existing primitives and a
  deterministic, versioned, domain-neutral contract has been reviewed.
