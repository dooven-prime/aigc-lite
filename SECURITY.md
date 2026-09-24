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
