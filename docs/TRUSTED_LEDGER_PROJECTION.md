# Host Ledger Projection v0

`LedgerProjectionService.export_run(context, run_id)` reads one workspace Run and
its directly linked rows in one database snapshot. SQLite uses an explicit read
transaction; PostgreSQL uses a read-only, repeatable-read transaction. The
workspace-scoped HTTP read path is `GET /api/runs/{run_id}/ledger-projection`.
It applies the same authenticated workspace boundary as Run detail and does
not expose raw prompt, output, or metadata fields. The
result is `HostLedgerProjection.v0`, a deterministic JSON object whose
`sha256` covers its canonical body. `source.rows` holds native row identities
and full-row hashes; sensitive prompt/output and caller-writable metadata are
not copied into the view. The source digest also binds association rules and
omissions. Consumers must obtain the view from the host service and pin its
digest outside any untrusted candidate input. A self-consistent JSON/hash pair
submitted by an Agent does not prove it came from this repository.
The exporter streams database cursor rows into a bounded snapshot (at most
20,000 source rows and 16 MB of row fields), returning HTTP 413 rather than
materializing an oversized Run. It does not silently truncate the source.
An Artifact's `content_hash` here is the stored database field; this exporter
does not reopen and rehash the underlying bytes.

The projection covers Run, Step, Model Usage Receipt, Artifact, Execution
Receipt, External Dispatch, and consumed authorization-invocation records.
Research attempts and related invalidation/current-use rows are listed as
references, not certified domain facts. A stored current-use row is not a
freshness evaluation; the exporter does not call any method that updates a
binding. Ordinary Artifact bytes and ExecutionReceipt metadata do not become
`verified_evidence`. Scoped invalidation decisions retain their exact
claim/receipt/proof-attempt target; the projection does not re-evaluate their
current effect. All projected Artifacts are candidate-only for Recovery;
the verified-evidence set is deliberately empty until a domain-specific,
read-only verification and currentness contract exists.

The native ledger does not yet bind a Run to a frozen Goal/Protocol, record a
general Step completion time, or durably link scheduler firings to a Run.
These omissions are emitted explicitly. `stream_not_started` means the stream
request was not sent; a claimed stream means it **may** have been sent. A
recovered `stream_execution_indeterminate` stays indeterminate even though the
storage status is `failed`. `IN_FLIGHT` Model Usage means that the call has no
durable terminal record and needs reconciliation; it does not itself prove a
supplier request was sent. A terminal `UNKNOWN`
or `INVALID` usage status is an accounting uncertainty, **not** evidence that
the provider request is still executing; unknown tokens are never converted
to zero or inferred from a successful Run. The database does not enforce
cross-table Run/Step/Usage lifecycle consistency. If a terminal Run retains an
`IN_FLIGHT` call, or a receipt's terminal fields contradict its status or
model Step, `usage_lifecycle_conflict_attempt_ids` records the inconsistency
and `effective_status` is `indeterminate`. A consumed but unclaimed invocation
or indeterminate Dispatch is likewise a pending reconciliation item, not a
success or reusable grant. These execution/accounting signals do not certify
the scientific facts described by the ledger.

The separate Long-Horizon Recovery casepack may compare two checkpoints of a
host projection to study activity versus evidence progress. That is a
candidate-only diagnosis. Because the Run/Goal relationship is not native,
any supplied Goal association remains an explicit conditional assumption.
Neither this exporter nor its replay creates a QualificationReceipt,
CurrentUseBinding, AuthorizationGrant, or dispatches an action. Before any
real continuation, the application must re-read the current Run, evidence,
invalidation, Grant, and policy state through the respective authority gates.

This v0 is an application-process view, not an external attestation or an
independent trust domain. Cross-host replay of the same exported bytes checks
determinism and integrity; it does not certify the original host or the
scientific meaning of its records.
