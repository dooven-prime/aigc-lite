# Review Workbench

The Review Workbench records actionable, evidence-linked suggestions about an
existing execution. It is an advisory ledger, not another trust score or an
authority gate.

```text
frozen Run/Step/Artifact/Citation snapshot
                    |
                    v
          versioned ReviewProfile
                    |
                    v
       deterministic ReviewRun + Findings
                    |
                    +-- no Claim mutation
                    +-- no Qualification verdict
                    +-- no CurrentUse binding
                    +-- no AuthorizationGrant
```

## Profile registry

Profiles are host-owned code registrations with a stable ID, integer version,
rule declarations, and a content hash. Duplicate IDs fail during registry
construction. The first profile is `execution.integrity.v1`, scoped to one
persisted `agent_run`.

It performs six bounded checks:

| rule | purpose |
|---|---|
| `execution.terminal_completion` | Run status and `completed_at` agree |
| `execution.step_sequence` | Step numbers start at one and remain contiguous |
| `execution.status_alignment` | Run and Step terminal states are coherent |
| `execution.error_provenance` | failed/cancelled states carry stable error codes |
| `execution.tool_provenance` | Tool source, provider, risk and local cancellation mode are recorded |
| `execution.output_materialization` | reusable tool output is not stranded only in Step text |

The last rule is a P3 advisory. A plain tool result does not become incorrect
merely because it has no Artifact or Citation.

## Frozen input and finding ledger

Each execution creates a new immutable `ReviewRun`; rerunning a profile creates
another historical observation rather than editing the previous one. The input
snapshot contains Run identity/state, Step state and metadata, linked Artifact
content hashes, and Citation identities. Step input/output bodies are not copied:
only presence flags and SHA-256 digests are frozen.

Each `ReviewFinding` records:

- the exact profile ID/version/hash and reviewed subject digest;
- deterministic rule ID, severity, origin, title and suggestion;
- ordered evidence references to the Run and relevant Step;
- an immutable finding hash and initial `open` state.

`accepted` is not equivalent to `resolved`. A later disposition workflow must
remain append-only, and closure should point to a fresh execution or other
verification evidence. Agent-generated findings will use a distinct
`agent_suggestion` origin when introduced; they cannot impersonate deterministic
rules.

## HTTP surface

- `GET /api/reviews/profiles` lists registered profiles.
- `POST /api/reviews/runs/{run_id}` runs a review; workspace admin only.
- `GET /api/reviews` lists review runs, optionally by subject/profile.
- `GET /api/reviews/{review_run_id}` returns the frozen review and findings.
- `GET /api/review-findings` filters findings by review, subject, severity or status.
- `GET /api/review-findings/{finding_id}` returns one workspace-owned finding.

```json
POST /api/reviews/runs/agent-run-id
{
  "profile_id": "execution.integrity.v1"
}
```

Creating a review writes an audit event. Reads and writes are workspace scoped.
No endpoint in this slice changes Run history, qualification state, canonical
knowledge bindings, or execution authority.
