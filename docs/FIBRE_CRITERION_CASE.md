# Fibre Criterion: small Lean delivery candidate

This is a separate, finite formal-delivery case. It is **not** Math Project 003,
a numbered RIME paper result, a general image/kernel theorem, or a
QualificationReceipt. The source owner is the local RIME candidate at
`rime-lite/contracts/formal-cases/fibre-criterion/v1/`; this repository does
not copy or silently rewrite its Lean source.

The candidate `case-lock.json` has SHA-256
`f4273da83620c8e581418aff8a9c485e476809694df325752afed911e1217705`.
That digest is an **out-of-band local expected value for this candidate**, not
yet a public Git identity. The bound `FibreCriterion.lean` bytes have SHA-256
`8faff7f358827a405e198c8b0c134758d43e3e5eedc05bd80dc7e3336f90485c`.
The lock also binds `AxiomAudit.lean`, `lean-toolchain`, `.gitattributes`, the
seven declaration names, Lean 4.33.0 release commit, and per-theorem expected
axioms. The compiler must report no extra or missing axiom for any declaration.

Run the operator-side verifier against an independently supplied copy of the
candidate package. Supply a Lean 4.33.0 executable from a separately checked
toolchain; the local `elan` alias may not point to an installed binary:

```text
python scripts/verify_fibre_criterion_case.py CASE_DIRECTORY \
  --lean LEAN_EXECUTABLE \
  --expected-lock-sha256 f4273da83620c8e581418aff8a9c485e476809694df325752afed911e1217705
```

The verifier compares the externally supplied lock digest **before** reading
its file inventory, checks every listed byte/size, rejects symlinked inputs and
unexpected adjacent Lean modules, then copies the two pinned Lean sources to
an ephemeral directory. With a minimal environment it runs `lean --trust=0`
to compile `FibreCriterion.lean` and run `AxiomAudit.lean`, compares all seven
reported axiom sets, and emits a JSON local-replay record. This does not use a
shell, write to the source package, execute arbitrary witness scripts, or touch
the qualification/current-use/authorization ledgers. The record includes
input, binary, and output digests; its `receipt_sha256` is a content checksum,
not a signature from an independent authority.

Successful local replay supports only this conclusion: the seven declarations
in those exact source bytes compiled under the recorded Lean release and
matched the frozen axiom audit. The factorization theorem **assumes
surjectivity**. The matrix claims concern only the file's local two-dimensional
column-pair representation. No paper-to-formal statement comparison, general
image/kernel theorem, independent organization, OS sandbox, public source
commit, or Math 003 replay follows. The Lean executable's version string and
binary hash are observed, not independently authenticated by this script.

Before an external delivery, the RIME package needs its own public immutable
source identity and an out-of-band digest distributed independently of the
package. A separate operator should obtain the package and trusted Lean
toolchain, rerun it, preserve raw outputs, and report what was independently
checked. Only a later, separately authorized application workflow could store
its evidence or evaluate a domain-specific QualificationProfile; replay here
does neither automatically.
