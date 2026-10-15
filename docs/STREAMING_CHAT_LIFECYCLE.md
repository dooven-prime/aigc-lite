# Streaming chat Run lifecycle

`/api/chat/stream` returns `X-Run-Id` while preserving lazy execution: merely
creating the HTTP response does not send a model request or invoke a tool.
The Run and its start deadline are inserted atomically as `running/pending`.
The first pull of the response body conditionally claims it as
`running/claimed` and starts exactly one worker. A competing pull, cancellation,
or expired deadline cannot start another worker.

| Durable state | Recovery interpretation |
| --- | --- |
| `pending` past its start deadline | `failed/stream_not_started`; no ModelUsageReceipt is created. |
| `claimed` past its lease | `failed/stream_execution_indeterminate`; a request **may** have been sent. Any `IN_FLIGHT` usage is finalized as `UNKNOWN`, never zero or `NOT_SENT`. |
| `closed` | Terminal Run and Agent Step are written by one conditional transaction. A successful assistant message is committed with the Run. |

Explicit close or cancellation before the first pull closes the pending Run
without starting a worker. After claim, cancellation is persisted before the
process-local fast-path cancellation; another worker observes that intent.
Accepted cancellation fences new model attempts and Tool Step writes. A
ModelUsageReceipt already finalized as `REPORTED` remains reported even if a
later cancellation wins the Run transition. The Run's terminal status and an
individual supplier call's usage status are separate facts.

Startup and periodic reconciliation run independently of the optional task
scheduler. Legacy Run rows have no stream state: they are not retrospectively
classified as `pending`, because their dispatch history is unknown. The
first pull also closes an expired pending Run immediately; a worker encountering
an expired claimed lease closes that specific Run before returning an SSE error.
If a competing process already closed it, the SSE error follows the durable
terminal decision rather than a late provider exception. The
start deadline, claim grace, reconciliation interval, and cancel polling
interval are configurable through `AIGC_LITE_STREAM_*` settings.

This is a ledger and application-level cancellation boundary, not proof that
an upstream model request or external tool side effect was physically stopped.
In particular a blocking or non-cooperative tool may outlive the worker's
lease. External side effects still require their own execution and recovery
contracts; an indeterminate Run must not be retried automatically.
