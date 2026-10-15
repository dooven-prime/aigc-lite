# Native Goal Binding v0

This is a creation-time identity contract, **not** a Goal approval or an
authority gate. An HTTP caller may attach `goal_binding` to `/api/chat` or
`/api/chat/stream`; the caller supplies a complete versioned Goal envelope
and the ID/version of an existing frozen Protocol in its workspace. The
application checks that the Goal's Protocol snapshot exactly matches the
bound semantic fields of the stored frozen row. The repository repeats that
check in the transaction that
creates the Run, canonicalizes the complete Goal, and computes the digests.
The caller cannot submit a digest or bind a Goal to a pre-existing Run.

The stored Run has `goal_contract`, `goal_protocol_id`,
`goal_protocol_version`, `goal_protocol_sha256`, `goal_sha256`, and
`goal_json`. The six fields are either all absent or all present. Database
triggers prohibit changing them after insertion. Historical Runs and new
Runs without an explicit Goal remain `NATIVE_UNBOUND`; migration does not
guess or backfill their goals. A stream freezes the Goal before the first
response pull and before any model request. A never-started stream keeps its
binding as a historical statement of intent, not evidence of execution.
The Run list exposes the identity fields but omits the full `goal_json` body;
workspace-scoped Run detail retains the exact envelope for deliberate review.

`goal_json` is the complete, sorted, compact UTF-8 JSON envelope, with a
32 KiB bound and a nesting limit. `goal_sha256` is SHA-256 over the bytes
`aigc-lite.native-goal.v0\n` followed by that canonical JSON. The Protocol
digest uses `aigc-lite.native-goal-protocol.v0\n` and the canonical stored
Protocol fields carried in the Goal (`id`, workspace, version, frozen status,
purpose, scope, completion predicate, stop conditions, and budget). This is
**not** a hash of the entire Protocol database row: name, profile, source URI,
timestamps, and the caller-supplied `content_hash` are outside that bound
semantic projection. The latter is *not* treated as proof of source bytes.
The projection separately records the complete Protocol row hash at export
time; it is an observation of that row then, not a creation-time pin. A Goal
envelope has the keys `contract`, `protocol`, `target`, and `policy`; the
application only
checks JSON shape, nonempty target/policy, and exact frozen Protocol identity.
Domain-specific target and policy semantics remain the casepack's job.
In particular, `profile` is currently classification metadata for this
identity contract. A future domain checker that selects rules by `profile`
must bind and revalidate it explicitly; `NATIVE_BOUND` alone cannot establish
which qualification rules apply.

The read-only Host Ledger Projection emits `HostLedgerProjection.v1` for a
bound Run. It rechecks the stored Goal and Protocol source row in one database
snapshot and reports `NATIVE_BOUND` only when the bound semantic fields,
digests, and references match. A missing Protocol or a change to the bound
semantic fields gives `INCONSISTENT` and an indeterminate effective status.
Unbound Runs retain the earlier v0 projection. The
Long-Horizon Recovery replay can compare a separately pinned Goal against
the v1 binding; its output remains a `PROPOSED` recovery diagnosis.

The binding does **not** mean the Goal was chosen by an independent authority,
its domain contract is valid, milestones were achieved, artifacts became
verified evidence, or a proposed recovery action may execute. In particular,
`verified_evidence` remains empty until a domain verifier and currentness
check establish it, and every future action must re-read current policy and
authorization. A caller with workspace access can request a Run against a
frozen Protocol; that request is attributed to the Run, not promoted into
epistemic or execution authority.
The caller-supplied `target` and `policy` are historical intent, never the
effective Tool policy: they cannot relax current scopes, Grant conditions,
budgets, stop rules, or external-dispatch checks.
