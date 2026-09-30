# Conversation imports

Conversation import is a candidate-memory ingestion path, not a knowledge
admission path. It accepts versioned external export formats through the
`ConversationImportRegistry` and preserves the source graph without flattening
branches into a synthetic linear chat.

Built-in importers:

- `chatgpt.export.v1`: ChatGPT `conversations.json` mappings;
- `deepseek.export.v1`: DeepSeek mapping/fragment exports, including search
  results and THINK/RESPONSE fragments.

## Preview and commit

An administrator must preview the exact bytes before committing them:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  -F importer_id=chatgpt.export.v1 \
  -F file=@conversations.json \
  http://127.0.0.1:8000/api/conversation-imports/preview
```

The response contains `source_content_hash`, `preview_hash`, counts, warnings,
and a bounded sample. Preview creates no Artifact, ImportBatch, Conversation,
or Message rows. Commit uploads the source again and must provide the reviewed
hash:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  -F importer_id=chatgpt.export.v1 \
  -F expected_preview_hash=$PREVIEW_HASH \
  -F file=@conversations.json \
  http://127.0.0.1:8000/api/conversation-imports
```

The server reparses the bytes and fails closed if the normalized graph no
longer matches the preview. Recommitting identical bytes with the same importer
is idempotent and returns the existing batch.

## Stored closure

One transaction writes:

```text
Raw JSON Artifact (SHA-256 over uploaded UTF-8 bytes)
        |
        v
immutable ImportBatch
        |
        +-- imported conversations (external identity and original times)
        |
        `-- imported message graph
              external_id / parent_external_id
              role / content type / model
              original + normalized time
              citations / attachments / metadata
              canonical-path marker / record hash
```

Artifact, batch, graph records, and SQLite FTS synchronization commit or roll
back together. ImportBatch, imported Conversation, and imported Message rows
reject update and delete operations at the database layer. The original export
remains available through its `source_artifact_id`.

ChatGPT's `current_node` identifies the canonical path, but every preserved
branch remains in the import ledger and candidate search. When an export has no
usable `current_node`, the importer records a warning instead of silently
choosing one branch.

## Epistemic boundary

All imports have `admission_state=candidate`. The FTS/lexical search result kind
is `conversation_message` and includes `import_batch_id`, `conversation_id`, and
the source `artifact_id`.

Import does not create or mutate ClaimRevision, QualificationReceipt,
CurrentUseBinding, or AuthorizationGrant. Consequently
`workspace_search`/`GET /api/search` can find imported records, while
qualified-only retrieval cannot return them without a separate, explicit Claim
and Qualification workflow.
