# Repository working rules

- Never commit `.env`, credentials, databases, logs, runtime evidence, or local
  proxy/private-host configuration.
- Workspace-persisted credentials use only
  `encrypted-db://credential/UUID`; keep `env://` deployer-owned and allowlisted.
- Add schema changes as Alembic revisions and cover both SQLite and PostgreSQL
  behavior where SQL semantics differ.
- Treat `frontend/src` as UI source and `app/static` as the packaged build.
  After UI changes run `npm ci`, `npm run build`, and
  `python scripts/sync_frontend.py`; commit both source and packaged assets.
- Keep core independent of ROS 2. Physical capabilities belong in
  `extensions/ros2-bridge` behind MCP/Tool Catalog contracts.
- Before release, run tests, Ruff, byte compilation, wheel clean-install smoke,
  frontend audit/build, PostgreSQL contract, and Docker hardened/insecure smoke.
- Preserve the distinction between candidate storage, epistemic qualification,
  current-use binding, and execution authorization. Model output cannot mutate
  these authorities directly.
