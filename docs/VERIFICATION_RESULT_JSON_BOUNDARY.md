# Verification result JSON boundary

`research.verification-result.v1` is the final-output contract for the existing
`VerificationRunner`. This document fixes its model-output parsing boundary; it
does not change the result fields, the meaning of `outcome`, or any qualification
or authorization decision.

The Runner accepts exactly one complete JSON document whose top level is an
object with the six existing contract fields. Leading and trailing whitespace
is allowed; Markdown fences, prose, extra JSON documents, and duplicate keys
at any level are not. The original schema and bounded field/list validation
still apply.

Before schema validation, the input is limited to 1,048,576 UTF-8 bytes and a
maximum decoded nesting depth of 32. Invalid UTF-8 encoding (including an
unpaired surrogate), escaped unpaired surrogates, Python's non-standard
`NaN`/`Infinity` constants, and numbers that overflow to non-finite floats are
rejected. The parser does not search for the first JSON object inside a larger
response.

Every rejected model result follows the existing `INVALID_OUTPUT` execution
path: a failed Run, an error VerificationAttempt and a blocked proposal-only
Promotion Gate evaluation. The result/error Artifact contains a fixed omission
marker instead of a raw-output excerpt because malformed or overly deep output
cannot be safely reparsed and structurally redacted. The MODEL Step remains the
execution record, but its storage redaction may use an omission marker instead
of raw content for malformed JSON-looking text, unrecordable deep JSON or
invalid Unicode; ordinary prose retains the existing text redaction. This is
not a guarantee that arbitrary free text contains no undisclosed secrets.
None of these records is an independent review
or a qualification/knowledge/authorization transition.
