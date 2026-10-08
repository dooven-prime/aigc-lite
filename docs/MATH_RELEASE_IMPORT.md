# OpenAI mathematics catalogue import

`OpenAIMathReleaseAdapter` imports the **catalogue**, not mathematical truth.
It fetches `CONTENTS.md` and `lean/formalization.yaml` only from
`openai/math` at an exact 40-character Git commit. The commit, both raw byte
hashes, the normalized manifest hash, and a preview hash are bound together.
The commit is a pinned upstream locator; this first version does not verify a
Git signed tag or independently reconstruct the Git tree.

An admin first calls `POST /api/research/math-release-imports/preview`:

```json
{"source_commit":"<exact 40-character commit SHA>"}
```

After inspecting the counts and hashes, the admin calls
`POST /api/research/math-release-imports` with that `source_commit` and the
returned `expected_preview_hash`. The service fetches and parses the pinned
sources again; a changed byte blocks commit. One database transaction writes
the two raw source Artifacts and one immutable candidate manifest. The same
commit and same bytes are idempotent; different bytes under a previously
imported commit fail closed. `GET /api/research/math-release-imports/{id}`
returns the summary; add `?include_families=true` for the full manifest.

The manifest preserves each result family ID, catalogue synopsis, manuscript
title/path/abstract, and any Lean documentation link from `CONTENTS.md`.
Those paths are **references**, not downloaded PDF or Lean proof Artifacts.
The raw formalization catalogue is retained but not interpreted as a kernel
certificate. A family synopsis is a `catalog_summary_not_frozen_theorem`
candidate, not a precise theorem `ClaimRevision`.

Import creates **zero** research ClaimRevisions, QualificationReceipts,
KnowledgeAdmissionReceipts, CurrentUseBindings, and AuthorizationGrants. It
does not call a verifier or a promotion gate. For a specific mathematical
claim, a separate workflow must freeze the exact statement and source bytes,
check statement identity, close dependencies, run the applicable kernel and
review checks, then explicitly admit a qualification for current knowledge use.
Family membership and a Lean link cannot shortcut any of those transitions.

The adapter is intentionally limited to the upstream catalogue format. If
OpenAI changes the format, parse/preview should fail rather than silently
dropping families or manuscripts. The first bounded PDF/Lean stage is now
[one case-003 theorem slice](MATH_PROJECT_003.md); it does not broaden this
catalogue import or qualify a whole result family.
