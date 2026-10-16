# External Mathematical Claim Alignment Audit — Pilot 001

Status: **candidate-only development case**, not a blind evaluation, a qualification receipt, a Lean kernel replay, or a claim that the Navier–Stokes problem is resolved. This is a separately installable research casepack. It is not registered by the core application and adds no HTTP/MCP authority route or database object.

The first narrow question is whether one pinned formal declaration directly supplies the input-regularity order displayed in one pinned manuscript estimate. OpenAI's Section 8, Lemma 8.6, equation (8.19), displays an (m+4) input order for (C_y^m) output. At commit `f9e8bc5b38b6e212696e8a30e3e91517af887bbd`, `norm_derivativeWord_inverse_le` has two `xJet (w.length + 5)` hypotheses. The Lean declaration is expressed per derivative word; the (C_y^{m+5}) norm comparison is an interpretation, not source text. The local candidate result can identify an order gap. Full statement identity, the validity of an alternative proof, and the Clay target remain unresolved.

Three separately scoped source records are now available. Only the Lemma 8.6 derivative-order check is implemented by the bounded source-reading CLI below. The pressure-flux record is a **human primary-source comparison**; the positive control adds only a pure regression for method/statement separation on human-curated fields. Neither is a new source-reading verifier, a core authority transition, or a qualification outcome.

| Record | Source-level finding | Verification boundary |
| --- | --- | --- |
| [Lemma 8.6 / (8.19)](records/LEMMA_8_6.md) | Named Lean estimate needs `+5` jets where the displayed manuscript estimate asks for `+4` input derivatives | Local alignment gap; full statement and kernel outcome unresolved |
| [Pressure flux / (10.19)](records/PRESSURE_FLUX_10_19.md) | Named Lean estimate has an `A_R` term absent from manuscript's `B_R`-only right-hand side | Primary-source local gap; a different formal proof route may still close downstream obligations |
| [Trace-square positive control](records/POSITIVE_CONTROL_TRACE_SQUARE.md) | Same finite real symmetric-matrix statement; diagonalization and sum-of-squares routes differ | Known-answer development control outside the OpenAI corpus; fresh Lean build not completed |

The first two alleged gaps were already public when this work began. Checking the original PDF and pinned Lean bytes is useful corroboration, **not** an independent discovery or a blind benchmark result. The positive control likewise cannot enter a review-withheld score.

## Frozen inputs and roles

The packaged [`source-lock-v0.json`](src/aigc_lite_casepacks/external_math_alignment_v0/source-lock-v0.json) records retrieved byte counts and SHA-256 hashes. Its current SHA-256 is `b02544e8ffcadd86ce5720186302fb5e3a4d3827e33197c32b9b7d72103fa6d7`. An operator must supply this expected digest from a separately reviewed channel; the copy printed here is a reproducibility aid, **not** independent authentication of this still-unpublished casepack. Reading the expected answer out of the candidate package would not establish trust. The [`contract-v0.json`](src/aigc_lite_casepacks/external_math_alignment_v0/contract-v0.json) fixes local verdict semantics and explicit non-claims.

| Source | Fixed location | Role |
| --- | --- | --- |
| [OpenAI manuscript PDF](https://cdn.openai.com/pdf/32d9f210-8b73-45e0-91bc-82a30aef8a9a/navier-stokes.pdf) | 166 pages; SHA-256 `0e779481c4da40bd28d1e642e1d8ca57447d129610df28dfa5a11e9af8ae228f`; PDF page index 94 (printed 95), (8.19) | Development detector input |
| [OpenAI Lean file](https://github.com/openai/NavierStokesAndEuler/blob/f9e8bc5b38b6e212696e8a30e3e91517af887bbd/NavierStokes/SmoothFamilyTorusInverse.lean#L1059-L1080) | Pinned commit, path, declaration and source SHA-256 `165c097077dc3687fd1d6f93c356059e5254be17c799950873f54d91a71e90c8` | Development detector input |
| [Bastounis–Circelli–Hansen review, arXiv v1](https://arxiv.org/pdf/2610.08144v1) | 25 pages; SHA-256 `3282e2c4103b955d635173749e7bf887fef7af7dd39e1abdb750b951030c538b`; Example 3.1, PDF pages 4–5 | Post-hoc known-answer comparison only; never detector input |
| [Clay official problem statement](https://www.claymath.org/wp-content/uploads/2022/06/navierstokes.pdf) | Official URL recorded; retrieval here timed out, so no verified bytes/hash | Context only; source closure not claimed |

These are file-byte identities, not authentication of a whole Git tree, signed release, publisher, mathematical interpretation, or Clay compliance. The operator retains the downloaded originals outside the repository. The detector does **not** fetch arbitrary URLs or execute supplied code.

## Running the local development check

Install the optional package into a disposable Python environment, then obtain the two input files from the fixed source URLs. Confirm their hashes outside the package before using them. The CLI requires the operator-reviewed lock digest and rejects mismatched file size/hash. It does not use the known-answer review paper. The following command is a **local development smoke only**: the digest copied from this README is not an independent trust root.

```bash
python -m pip install -e ./casepacks/external-math-alignment-pilot-001
python -m aigc_lite_casepacks.external_math_alignment_v0 \
  --manuscript-pdf /path/to/navier-stokes.pdf \
  --formal-source /path/to/SmoothFamilyTorusInverse.lean \
  --expected-lock-sha256 b02544e8ffcadd86ce5720186302fb5e3a4d3827e33197c32b9b7d72103fa6d7
```

The source parser is deliberately narrow. It confirms only the locked formula and named declaration anchors and abstains when they are absent or ambiguous; it is not a general PDF-to-Lean semantic equivalence engine. A gap means that this *particular formal declaration* does not directly establish the manuscript's displayed order. It does not prove the manuscript estimate false, the final theorem false, or that no other formal proof exists. Conversely, equal derivative counts alone would not prove full statement alignment.

No `QualificationReceipt`, `CurrentUseBinding`, `AuthorizationGrant`, or knowledge search entry is issued. Any later claim qualification would require its own frozen ClaimRevision, statement-identity review, actual kernel evidence, applicable Profile, and independent knowledge-admission decision. See [`EVALUATION_PROTOCOL.md`](EVALUATION_PROTOCOL.md) for the still-unrun three-arm, review-withheld comparison. The public Lemma 8.6 pair is excluded from its blind score.
