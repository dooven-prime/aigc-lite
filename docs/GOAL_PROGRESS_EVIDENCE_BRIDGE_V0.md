# Goal Progress Evidence Bridge v0

This is a read-only, candidate-only interpretation of one narrow Goal metric:
**does an exact ClaimRevision have a qualification under a specified Profile
that remains selected for current knowledge use as of one database snapshot?**
It is neither a scientific truth certificate nor permission to continue a
Run, invoke a tool, publish, or recover automatically.

## Frozen host rule

The only v0 rule is `goal.current_knowledge_qualification.v0`. Its source
constant and `rule_sha256` live in
`app/services/goal_progress_evidence.py`. A natively bound Goal may include
one `policy.evidence_criteria` entry with exactly these fields:

```json
{
  "rule_id": "goal.current_knowledge_qualification.v0",
  "claim_revision_id": "<exact revision ID>",
  "claim_semantic_hash": "<64 lowercase hex digits>",
  "profile_id": "<registered profile ID>"
}
```

These caller-supplied values are **query parameters**, not the definition or
approval of the metric. The host rule, current registered QualificationProfile,
and knowledge admission policy determine what is checked. Goal `target` and
other caller `policy` fields cannot alter verification criteria, Tool scopes,
Grant conditions, budgets, stop rules, or external dispatch checks.
The host resolves the named Profile once for this evaluation and uses that
same frozen Profile object for the selected claim and every transitive hard
dependency. The result identifies the used QualificationProfile by
`profile_id`, `version`, `content_hash`, and `policy_version`; it also identifies
the host's default KnowledgeAdmissionPolicy by `policy_id`, `version`, and
`content_hash`. If the Profile cannot be resolved, its identity remains null
and the verdict is `UNRESOLVED`. These are identities of host-registered
contracts, not authentication of the upstream sources, signer, or scientific
observations represented by a receipt.

## Evaluation

The service first uses a workspace-scoped, read-only Run hint to identify the
criterion, then
reads the Run, frozen Protocol, selected ClaimRevision, current knowledge
binding, selected QualificationReceipt and Evaluation, selected
KnowledgeAdmissionReceipt, invalidation decisions, frozen closure sources and
artifacts, and transitive hard dependencies in one bounded read-only database
snapshot. The initial read is only a query hint; the service validates the
Goal and criterion again from the single snapshot. SQLite uses a read
transaction; PostgreSQL uses `REPEATABLE READ` plus `READ ONLY`.

`rule_sha256` hashes the host's **declared rule contract**, not the evaluator
implementation. The result separately includes
`implementation_source_sha256` for the actual installed bytes of
`app/services/goal_progress_evidence.py` and
`app/repositories/goal_progress_snapshot.py`, plus `app/repository.py` where
the SQLite/PostgreSQL read-only transactions are implemented. These hashes
identify selected implementation files, **not** the full Python dependency
closure. They do not prove the algorithm correct, certify the host filesystem,
or attest that code could not change after module import or between reading
and execution. If any source cannot be read within the bounded size, the bridge
fails closed as `UNRESOLVED`.

The pure assessment verifies, at minimum:

- Native Goal and Protocol identity; exact ClaimRevision semantic hash and
  current registered Profile/policy version and hash.
- `ADMITTED` QualificationReceipt bound to the exact Evaluation and frozen
  evidence closure, including their recorded hashes and source identities.
- Separate KnowledgeAdmissionReceipt bound to that same receipt and policy.
- Exact selected `knowledge` CurrentUseBinding; the stored word `current`
  alone never suffices.
- No applicable claim-level, selected-receipt, or **selected kernel proof
  attempt** invalidation. For proof attempts, the bridge resolves the unique
  kernel attempt selected by the receipt's `statement_identity` evidence
  artifact IDs against its frozen closure. It returns `UNRESOLVED` if that
  attempt is not uniquely identifiable. A decision targeting an older receipt
  also blocks a newer receipt if both selected the same invalidated attempt;
  merely retaining that attempt as unselected historical context in the newer
  closure does not block a genuinely repaired proof that selects a different
  attempt. Receipt-scoped revocation of an older, unselected receipt does not
  automatically revoke its replacement.
- Each frozen hard dependency still selects the same receipt, is current under
  this same check, and has no transitive taint or dependency cycle.

`SUPPORTED_AS_OF_SNAPSHOT` means the exact qualification was usable for the
knowledge scope in the recorded snapshot. `BLOCKED` records a known identity,
policy, integrity, state, or invalidation contradiction. `UNRESOLVED` records
missing/ambiguous evidence, unavailable rules, malformed or over-budget
snapshots. The response includes the host rule hash, exact criterion, the used
Profile and admission-policy identities, source row IDs and hashes, a snapshot
digest, and reason/limitation fields. The digest
identifies the database projection, not independent authentication of source
events or scientific truth.

No stored binding is refreshed by this service. In particular it must not
call `QualificationService.claim_status()`, `_refresh_binding()`,
`refresh_receipt_binding()`, `qualified_search()`, or assurance export, because
those paths can apply lazy taint updates. An already returned snapshot result
does not remain current after a concurrent later invalidation or replacement;
any future action must re-read current policy, Grant, and execution state via
the existing authority chain. Historical results are not rewritten.

## Deliberate non-claims

- Goal identity is not Goal approval.
- The caller may select a qualifying Claim that is irrelevant to its prose
  target. This bridge answers only the exact criterion named in the output;
  it does not establish that the whole Goal advanced or completed.
- Qualification and knowledge use are not proof of all scientific claims
  implied by the Goal's prose.
- A source row/hash does not authenticate an upstream scientific observation.
- The bridge checks the frozen Evaluation closure and applicable later
  invalidations; it does not rerun a domain verifier or independently reread
  every historical VerificationAttempt row.
- An attempt-level invalidation can taint a different historical Receipt that
  selected the same proof Attempt, even while its stored binding still says
  `current`. This bridge detects that case for its own read-only result; it
  does not repair the stored binding or make other retrieval paths equivalent.
- It relies on the host's existing database and Profile registration trust
  boundary. Historical QualificationReceipts are hash-bound records, not
  independently signed certificates; external provenance and real-world
  events require their own authentication and domain validation.
- Progress evidence is not a `RecoveryDecision`, `AuthorizationGrant`, or
  permission to retry an indeterminate external action.
- This bridge does not create a new core Receipt, Registry, database table, MCP
  capability, or automatic cross-Run recovery mechanism.
