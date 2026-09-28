# Security

## Reporting

Please use GitHub private vulnerability reporting in the repository Security tab.
Do not include exploit details, credentials, or tenant data in a public issue.
Include the affected version, reproduction steps, and whether credentials or
cross-workspace data may be exposed.

## Deployment baseline

- Replace `AIGC_LITE_AUTH_SECRET` and configure a separately generated
  `AIGC_LITE_MASTER_KEY` before storing credentials.
- Keep `.env`, databases, logs, backups, and master keys outside version control.
- Put public deployments behind TLS and a trusted reverse proxy, disable open
  signup when it is not required, and configure explicit authentication.
- Treat remote model and MCP URLs as privileged configuration. The current
  release validates URL shape but does not yet provide complete DNS rebinding,
  redirect, private-network, or cloud-metadata SSRF protection.
- Do not register filesystem, shell, browser, or arbitrary network tools in a
  public deployment without a separate sandbox and explicit authorization.
- Back up the database and its matching master key together. Losing or rotating
  the key without re-encryption makes `enc:v1` credentials unreadable.
- Encrypted credential values are write-only through the API. Store references,
  replace values when rotating, and revoke records instead of attempting to
  recover plaintext through an administrative endpoint.

Execution records and audit metadata use centralized best-effort redaction.
This is defense in depth, not a substitute for keeping secrets out of prompts
and tool results.

## Physical capability extensions

- Keep ROS 2/DDS and hardware dependencies in a separate provider process; do
  not give the main web process direct driver or arbitrary ROS graph access.
- Motion tools must be high risk and require an explicitly granted scope. The
  repository does not grant `robot:motion` or `robot:control` by default.
- Treat MCP/async cancellation as best effort. It cannot replace an emergency
  stop, safety-rated controller, watchdog, collision protection, or hardware
  interlock.
- A timeout with no stop acknowledgement is `indeterminate`, never success or
  confirmed cancellation. Operators must resolve the physical state before a
  follow-up action.
- Use simulation first. On hardware, constrain workspaces, maps, frames, speed,
  acceleration, operating zones, and action allowlists below the Agent layer.
- The bridge binds to loopback by default. Cross-host deployments need mutually
  authenticated transport, network segmentation, and explicit Host/Origin
  allowlists.
