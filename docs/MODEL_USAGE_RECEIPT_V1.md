# ModelUsageReceipt.v1 (candidate implementation)

This contract records one **host-observed provider attempt** per model turn. It
is an accounting and reconciliation record, not a provider invoice, a money
budget, a qualification receipt, or execution authority.

## Identity and lifecycle

Before the provider request, the host allocates `model_call_id`, `attempt_id`,
and a Model Step ID. It inserts the `IN_FLIGHT` receipt and `RUNNING` Step in
one transaction. An ordinary serial Agent loop currently makes one provider
attempt per model call; the two IDs are separate so a future explicit retry
can keep the logical call while giving each network attempt a distinct ID.

The receipt binds workspace, nullable principal, nullable API-key identity,
HTTP request, Run, Step, iteration, selected model, and non-secret provider
configuration ID. A scheduler/internal invocation may have no user principal.
For configured bearer API keys, the API-key identity is a host-secret-keyed,
domain-separated HMAC of the token; the raw key is never stored. Its identifier
changes if `AIGC_LITE_AUTH_SECRET` rotates. Session-authenticated users retain
their user principal and do not receive a fabricated API-key identity.

Finalization updates the Model Step and receipt atomically. Replaying the same
finalization hash is idempotent; a conflicting second finalization fails. A
process crash can leave `IN_FLIGHT`, which requires reconciliation rather than
zero-filling. Stream cancellation/timeouts retain `CANCELLED_OR_TIMEOUT` at
the attempt level; the Run records the more specific terminal state when
known. The provider request ID is captured as soon as observed in streaming
events, even if `[DONE]` is never received. `output_bytes` measures observed
UTF-8 output and helps distinguish no response from partial output.

## Usage and pricing semantics

- `REPORTED`: both token counts are exact nonnegative integers from the
  provider, including an explicit zero. The request may still fail or truncate.
- `UNKNOWN`: missing or partial usage. Missing fields remain `null`.
- `INVALID`: malformed or conflicting usage reports. Counts are `null` and
  do not contribute to the aggregate.
- `IN_FLIGHT`: the provider attempt has not been durably finalized.

The price snapshot whitelists only rate, unit, source, configuration ID, and
whether rates are known; no credential or endpoint secret is included. Old
workspace model rows with default zero prices are **not** retroactively
asserted free: the `0026` migration marks their price unconfirmed. Newly
submitted workspace prices count as known only when both rates were explicit.
Deployer-default pricing is known only when both price environment variables
were explicitly configured. Known zero prices produce zero estimated cost;
unknown usage or unconfirmed pricing produces `estimated_cost = null`.
Amounts in individual receipts are canonical Decimal strings, not vendor bills.

`GET /api/runs/{run_id}/model-usage` is a workspace-scoped, read-only
projection, requiring the Run to exist in that workspace. `/api/usage` combines
historical `usage_records` with new receipt attempts. Production calls using
the built-in Agent observer no longer write the legacy table, preventing dual
counting; the compatibility path for injected Agent runners retains the old
callback behavior. Aggregates include only `REPORTED` token counts and expose
unknown/invalid attempt counts. The aggregate cost remains the legacy UI's
floating-point presentation of the **known estimated subtotal**, not an
assertion of total spend or a vendor bill. `unpriced_reported_requests` exposes
reported calls excluded from that subtotal because pricing was unconfirmed;
unknown usage is counted separately. Individual receipt estimates retain
decimal strings. Historical legacy rows lack per-call identity and are not
relabeled as receipts.

## Boundaries and deferred work

This is application-level observation: it does not prove that the vendor bill
matches the receipt, or that a request which failed before usage arrived had
zero cost. It does not enforce a cross-Run dollar budget. It does not reconcile
stale `IN_FLIGHT` attempts automatically or authorize retry of a possibly
executed request. Vendor usage framing differs; unsupported or absent final
usage remains `UNKNOWN`, not zero. Per-attempt model/provider identities are
server selections, not claims supplied by model output. This change does not
alter Qualification, CurrentUseBinding, AuthorizationGrant, or MCP authority.
