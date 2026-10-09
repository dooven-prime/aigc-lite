# Research Explorer and import boundaries

`GET /api/research/explorer` is a read-only projection over existing
`research_cases` across profiles. It does not parse uploaded data, create
claims, run a verifier, or change qualification/current-use state. The
older `GET /api/research-registry` remains Frontier-only for compatibility.

`GET /api/research/importers` returns versioned adapter descriptors. A
descriptor declares its source format, destination and dedicated endpoints;
it is not a generic upload capability. Importer ID and version must match a
server-registered adapter. New external formats require an explicit adapter
and tests for its source/authority boundary.

| Adapter | Destination | Existing workflow |
| --- | --- | --- |
| `frontier.registry@1` | Research ClaimRevision candidates | `POST /api/research-registry/import/frontier` structurally validates `claim-registry.json` and optionally freezes a source ledger. Declared source digests are not independently fetched. |
| `openai.math@1` | Catalogue candidates only | Preview and commit at `/api/research/math-release-imports`; exact-commit source snapshots remain outside the ClaimRevision table. |

The Research imports UI exposes these boundaries separately. The Explorer
shows a mathematical theorem only after its exact statement has been frozen
as a `math.theorem` ClaimRevision; an imported result family alone will not
appear there. Neither adapter emits QualificationReceipts, KnowledgeAdmission
Receipts, CurrentUseBindings or AuthorizationGrants. RIME-like project data
would need its own mapping and validation contract; this release does not
attempt to infer claims from arbitrary files.
