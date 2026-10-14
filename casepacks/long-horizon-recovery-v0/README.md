# Long-Horizon Recovery Contract v0

This optional research casepack develops a replayable **progress-state and
recovery-control contract** over existing `aigc-lite` Protocol, Run, Step,
Artifact, Receipt, and scheduler facts. It is an unpublished `0.1.0.dev0`
candidate. It does not add a core database table, install an HTTP/MCP route,
register a verifier or tool automatically, or execute a proposed recovery.
The `aigc_lite_casepacks` parent remains a PEP 420 namespace, with no parent
`__init__.py`, so the distribution owns only its child package.

The four versioned JSON documents are **Artifact contracts**, not four new
authority-bearing entities:

| Document | What it records | What it cannot establish |
| --- | --- | --- |
| `StateSnapshot.v0` | Frozen goal identity, ledger-backed observations, resource and environment versions, source references, observation time, and pending/indeterminate operations. | That an old observation is still current or that a model summary is a source fact. |
| `ProgressAssessment.v0` | A non-monotonic vector of host-reported verification milestones, resources, goal-relevant evidence, unresolved conditions, and budget, with source-bound before/after comparison. | That activity volume, search count, or generated prose is evidence or progress; that the host's verifier identity is independently authenticated. |
| `RecoveryDecision.v0` | A `PROPOSED` control response of `CONTINUE`, `REOBSERVE`, `REPLAN`, `REASSESS`, `WAIT_FOR_EVIDENCE`, `PAUSE`, or `STOP`, plus reasons and required host rechecks. | Authorization to call a tool, consume a grant, dispatch an external action, alter a frozen goal, or retry an indeterminate operation. |
| `CompactionReceipt.v0` | Which original facts and constraints survived a context reduction, which were omitted, and the hashes needed to return to the underlying ledger. | New facts, qualification, current-use binding, or action authority. |

The input has a `goal` (`LongHorizonGoal.v0`), a `ledger`
(`LongHorizonLedger.v0`), two `checkpoints`, and an optional
`compaction_view`. The goal contains the frozen Protocol projection and a
versioned target/recovery policy. The ledger contains one Run, ordered Steps,
Receipts, Artifacts, and timestamped scheduler-firing **events**; a mutable
latest-state row is not an event history. If the host cannot export immutable
firing transitions, historical scheduler recovery cannot be established by
this casepack. Each checkpoint gives a Step
sequence cutoff and an assessment timestamp. This is an export projection,
not a claim that every field exists natively in the core tables. In
particular, the exporter must bind `run.protocol_version` and
`run.goal_sha256` to the intended frozen goal. The casepack recomputes the
domain-separated goal digest and checks those projected values. Raw
`Protocol.content_hash` is not recomputed by the current Protocol service,
so its mere presence cannot establish this goal identity.

The checker rejects cross-workspace/run references, conflicting environment
versions for one revision, and an environment revision that moves backwards
across accepted observations. An observation also has its own timestamp and
freshness limit. `Step.kind=verification` or `reconciliation` identifies a
role in this **host projection**; the string does not prove the caller had a
trusted verifier or reconciliation identity. Likewise, a projected
`ExecutionReceipt.metadata` fact or well-formed `output_digest` is not by
itself authenticated. The host exporter must verify the original
output-to-metadata/fact mapping and its authorization to label a Step before
freezing the projection. The casepack checks exact externally pinned
projection bytes and deterministic relationships, not the truthfulness of
the exporting host, the environment, or an external institution. Snapshot
`authority_effects_of_replay` describes what this checker did **not** create;
it does not claim that the workspace has no pre-existing qualification,
binding, or grant. Protocol
`completion_predicate` and free-text stop conditions remain frozen source
constraints, not expressions this checker can evaluate; recorded milestones
are therefore never by themselves treated as proof of goal completion.

Replaying the same canonical input with the same contract and checker source
should produce the same canonical output. Obtain the expected checker and
contract source identities, plus the **domain-separated canonical
JSON** goal, ledger and checkpoint digests, from a reviewed source or lock held outside
the submitted input. Do not derive an expected digest from the candidate
being checked and then call the match verification. These hashes establish
byte identity, not source authentication or semantic correctness of the goal.
An `invalidation` fact targets one Receipt, not a source-level dependency
closure. If a source loss affects several evidence Receipts, the host must
export each affected invalidation; this casepack does not infer the cascade.
When a replay loses a previously recorded milestone or scoped evidence after
such an explicit invalidation, its proposal is `REASSESS`, never ordinary
`CONTINUE`. The resulting unresolved invalidation stays in the snapshot and
compaction invariants across later checkpoints. Ordinary activity does not
clear it; a new valid host-projected verification Receipt for that exact goal
milestone or evidence criterion can. Invalidating an older superseded Receipt
does not revoke a newer current Receipt. This does not discover or authenticate
the source failure on its own, or independently verify a claimed repair.
Only the whole-ledger `replay()` path is the supported source of a recovery
proposal; a standalone snapshot self-hash is not an authenticated control
input. Internal proposal logic is not a public decision service. If a pinned
checkpoint predates any event in the pinned ledger, its snapshot is marked
`historical_only` and its recovery proposal is `PAUSE`, never a live
`CONTINUE`. Even the latest ledger checkpoint may lag the real world;
the independent host state refresh remains mandatory before any action.

The intended flow is:

```text
frozen goal / Protocol + host-exported Run / Step / Artifact / Receipt facts
    -> deterministic StateSnapshot and ProgressAssessment
    -> candidate RecoveryDecision (status PROPOSED)
    -> host re-reads current state and checks the existing Grant/Gate/policy
    -> only an independently authorized executor may take a later action
```

Compaction is a separate derived view, never a replacement for the original
ledger. The next action must not rely on a snapshot whose critical source
observation expired, was invalidated, or was superseded; it requires a fresh
host-side check. Missing or stale monitored resources also require
reobservation; an unchanged environment label cannot mask a newer revision.
The view's structured invariants must carry the frozen target, completion
predicate, stop conditions, and forbidden actions and match the goal exactly.
Free-text summary omissions cannot be detected by interpreting the summary;
the host must supply these structured fields and reload the frozen goal for
the next action.
The compaction view must retain the source Receipt for each current
uncertainty and preserve open uncertainty and stale-state invariants. A confirmed
loss can lower the progress vector. A pending
or indeterminate external dispatch or scheduler firing remains blocking, not
an invitation to retry. A candidate-only `DisambiguationCertificate.v0` can
show conditional information relevance but cannot by itself authenticate a
biological or bibliographic source. The replay counts goal evidence only
when the frozen criterion/target is bound to a host-projected verification
Receipt and a non-candidate `verified_evidence` Artifact; this casepack still
does not decide that the host-projected verification identity is genuine.

The frozen [contract asset](src/aigc_lite_casepacks/long_horizon_recovery_v0/contract_v0.json)
spells out the scope and invariants. It is deliberately *not* a generic
autonomous planner, a new Qualification Profile, or a release of the separate
MDE research certificate. Installation is explicit; core does not import this
casepack. Any promotion into core would require a separate cross-domain
review, versioning, and authority-boundary analysis.

For source-tree development, install this casepack explicitly:

```bash
python -m pip install -e ./casepacks/long-horizon-recovery-v0
```

The offline entry point accepts the pinned values as separate arguments. The
checker digest is SHA-256 of `aigc-lite.long-horizon-recovery.v0.checker\n`
followed by each sorted Python filename, a NUL byte, and that file's binary
SHA-256 digest; the contract digest is plain SHA-256 of `contract_v0.json`.
This binds the CLI and replay implementation together. The CLI computes its
own digest only as a consistency check: a compromised executable can falsify
that check, so an external verifier must obtain and compare the source or
wheel identity independently.

The replay input contains `goal`, `ledger`, `checkpoints` with
`previous_sequence`, `current_sequence`, `previous_at`, `current_at`, and
optionally `compaction_view`. Keep the expected digests in an out-of-band
reviewed lock, then run:

```bash
python -m aigc_lite_casepacks.long_horizon_recovery_v0 \
  --input replay-input.json \
  --goal-sha256 "$PINNED_GOAL_SHA256" \
  --ledger-sha256 "$PINNED_LEDGER_SHA256" \
  --checkpoints-sha256 "$PINNED_CHECKPOINTS_SHA256" \
  --checker-sha256 "$PINNED_CHECKER_FILE_SHA256" \
  --contract-sha256 "$PINNED_CONTRACT_FILE_SHA256"
```

Set these variables from an out-of-band reviewed lock, not from
`replay-input.json`. An independently pinned result can additionally be
checked with `--expected-result-sha256 "$PINNED_RESULT_SHA256"`. On success,
stdout is one canonical JSON replay bundle containing the previous and current snapshots,
assessment, proposed decision, and compaction receipt. A failed input exits
nonzero without a positive bundle. Supplying an omitted compaction view builds
a lossless view for this replay; it is not evidence that an independently
produced model summary preserved the same invariants. `RecoveryDecision.v0`
is always `PROPOSED` and never calls the scheduler or an external tool.

These four document kinds may be stored as JSON Artifacts through the
existing `ArtifactService`, not as four new database objects. The service may
redact or reformat submitted content; after persistence, compare the stored
bytes and digest against the original replay output before treating the
Artifact as the same frozen document. An Artifact hash alone does not grant
qualification, current-use binding, or execution authority.

The package version identifies code distribution only, not an execution
environment pin. Preserve the actual dependency and checker versions loaded
for each replay. Cross-host replay with the same checker is reproducibility,
not independently implemented verification or independent trust-domain
validation.

## Deterministic fault-injection checks

The focused tests use synthetic, pinned-in-process goal and ledger exports.
Each replay is repeated against identical input and must yield the same
sealed output hash. Every RecoveryDecision remains `PROPOSED` and
`execution_authorized=false`.

| Injected condition | Required bounded response | What this does not establish |
| --- | --- | --- |
| A resource falls from three to zero; later safety-routine Steps consume the stagnation budget without goal evidence | `REPLAN` for the confirmed loss, then `REASSESS` for continued no-progress activity | That a real safety action is intrinsically useless; the host projection records only its lack of verified goal progress. |
| Many Bio research Steps and candidate summaries still lack authenticated parent lineage | No increase in `E`; `REASSESS` | That the host independently authenticated any laboratory source. |
| A host-exported Citation evidence Receipt is later invalidated | `E.lost_criteria` is recorded; `REASSESS` persists through later ordinary activity until a new valid Receipt replaces the criterion | That this checker detected source expiry or authenticated the invalidation. The host must export each affected Receipt. |
| The compacted view omits a structured completion predicate, a pending-operation marker, or a mandatory Step/Receipt source | `CompactionReceipt=REJECTED`, then `PAUSE` | That free-text summary omissions can be semantically detected. The structured goal and source references are checked instead. |
| An external action is indeterminate, or its underlying Grant has expired | Replay proposes `PAUSE` without retry; the separate host authorization test rejects the expired Grant before invocation or dispatch | That the casepack knows Grant validity or provides OS-level containment. Host authorization remains the final gate. |

The first four checks and the indeterminate-dispatch half of the fifth are in
`tests/test_long_horizon_recovery_fault_injection.py`. The expired-Grant
half is in `tests/test_authorization_dispatch_handoff.py`, where the real
Tool Catalog authorization path is exercised. These are bounded, internal
contract regressions, not an independently pinned external acceptance run.
