# Candidate alignment record 001 — Lemma 8.6, equation (8.19)

Record version: `external.math.alignment.pilot-001.lemma-8-6.v1`
Status: **primary-source-checked local gap; full statement identity unresolved**.
Authority effect: **none**. This record is not a QualificationReceipt, a kernel certificate, or a verdict on the final Navier–Stokes theorem.

## Source identities and locations

Per-record source manifest: [`manifests/lemma-8-6-v1.json`](manifests/lemma-8-6-v1.json). The older packaged `source-lock-v0.json` remains the bounded **Lemma 8.6 detector** input lock, not a three-record source closure.

| Source | Frozen identity | Relevant location |
| --- | --- | --- |
| [OpenAI manuscript](https://cdn.openai.com/pdf/32d9f210-8b73-45e0-91bc-82a30aef8a9a/navier-stokes.pdf) | 2,959,204 bytes; SHA-256 `0e779481c4da40bd28d1e642e1d8ca57447d129610df28dfa5a11e9af8ae228f` | Physical PDF page 95 (zero-based index 94), §8.5, Lemma 8.6 and (8.19); page 96 proof |
| [OpenAI Lean declaration](https://github.com/openai/NavierStokesAndEuler/blob/f9e8bc5b38b6e212696e8a30e3e91517af887bbd/NavierStokes/SmoothFamilyTorusInverse.lean#L1059-L1080) | Git commit claim `f9e8bc5b38b6e212696e8a30e3e91517af887bbd`; `NavierStokes/SmoothFamilyTorusInverse.lean`, 57,334 bytes; SHA-256 `165c097077dc3687fd1d6f93c356059e5254be17c799950873f54d91a71e90c8` | `norm_derivativeWord_inverse_le`, lines 1059–1080 |
| [Published external review](https://arxiv.org/pdf/2610.08144v1) | arXiv:2610.08144v1; 1,710,171 bytes; SHA-256 `3282e2c4103b955d635173749e7bf887fef7af7dd39e1abdb750b951030c538b` | §3.1, consulted as a **post-hoc comparison**, not as source evidence for the detector |

The source SHA-256 values identify retrieved bytes. They do not authenticate the Git tree, publisher, theorem semantics, or a complete build closure. The published allegation was known before this audit, so primary-source confirmation must **not** be scored as an independent discovery. The casepack's bounded `audit.py` reads only the first two sources and compares the displayed derivative-order anchors; the rest of this record includes human source interpretation.

## Frozen statement fields

| Field | Manuscript Lemma 8.6 / (8.19) | Named Lean estimate |
| --- | --- | --- |
| Object | (N=v_t\cdot\partial_y) on the two-torus, with (v_t=(\sqrt2-1,1)); inverse on smooth zero-Haar-mean functions | `inverse d f` with a `Direction`, a smooth periodic parameterized source, and a derivative word `w`; `Direction.temporal` has the stated vector in [`TorusInverse.lean:190–201`](https://github.com/openai/NavierStokesAndEuler/blob/f9e8bc5b38b6e212696e8a30e3e91517af887bbd/NavierStokes/TorusInverse.lean#L190-L201) |
| Scope | Every integer (m\geq0), smooth zero-mean (F), and a corresponding constant (C_m) | Each `w : List Bool`, fixed slice parameters and point, subject to explicit `hzero`, `hfirst`, `hsecond` bounds by `C` |
| Claimed estimate | \(\lVert N^{-1}F\rVert_{C_y^m}\leq C_m\lVert F\rVert_{C_y^{m+4}}\) | Pointwise bound for `derivativeWord w (slice (inverse d f) p)`; `hfirst` and `hsecond` require `xJet (w.length + 5)` input bounds |
| Other content | The lemma also states existence, uniqueness, zero mean, and a Fourier representation | This one estimate declaration does not itself package all those claims; nearby [`inverse_zeroMean` and `inverse_solves`](https://github.com/openai/NavierStokesAndEuler/blob/f9e8bc5b38b6e212696e8a30e3e91517af887bbd/NavierStokes/SmoothFamilyTorusInverse.lean#L596-L616) are separate results |

The manuscript explicitly describes the four-extra-derivative loss in its page-96 proof. The Lean declaration is **not literally** a (C_y^m)-norm theorem: comparing (m) with `w.length` and taking a supremum over derivative words are further interpretation steps. Those steps cannot erase the two explicit `+ 5` hypotheses in the cited declaration.

## Checked direct proof-dependency slice

The named Lean proof calls [`SmoothFourierData.coefficient_seminorm_bound`](https://github.com/openai/NavierStokesAndEuler/blob/f9e8bc5b38b6e212696e8a30e3e91517af887bbd/NavierStokes/SmoothFourierData.lean#L368-L385) at line 1069 with (p=|w|+1). That helper asks for (p+4) input jets, hence (|w|+5). It then calls [`TorusInverse.inverse_derivativeWord_bound`](https://github.com/openai/NavierStokesAndEuler/blob/f9e8bc5b38b6e212696e8a30e3e91517af887bbd/NavierStokes/TorusInverse.lean#L502-L525) at line 1075. The final constant is `ParametricTorusInverse.mixedLossConstant`. This is a traced **direct source dependency slice**, not a complete transitive theorem/axiom audit.

At this commit, [`lean-toolchain`](https://github.com/openai/NavierStokesAndEuler/blob/f9e8bc5b38b6e212696e8a30e3e91517af887bbd/lean-toolchain) specifies `leanprover/lean4:v4.34.0-rc2`; [`lake-manifest.json`](https://github.com/openai/NavierStokesAndEuler/blob/f9e8bc5b38b6e212696e8a30e3e91517af887bbd/lake-manifest.json) pins mathlib `85e3a25e006c35636f0e53b0e9296caca2685bc0`. Those are source metadata, **not** a completed local kernel replay. The nearby `inverse_finiteJets` also requires `n+5`, but concerns joint jets and is not silently substituted for the manuscript's (C_y^m) statement.

## Local diagnosis and impact

`LOCAL_REQUIREMENT_ALIGNMENT = ALIGNMENT_GAP`: the cited estimate, as written, assumes one more input derivative than manuscript (8.19). Therefore this declaration alone does not directly prove the displayed `m+4` bound. Its different Fourier estimate may explain the extra derivative; a different method is not itself a statement error.

`FULL_STATEMENT_IDENTITY = UNRESOLVED`; `KERNEL_REPLAY = NOT_RUN`; `PROOF_METHOD_IDENTITY = NOT_REQUIRED_FOR_STATEMENT_ALIGNMENT`. We have not established whether another formal declaration or argument supplies `m+4`, whether the manuscript's `m+4` estimate is true, whether the downstream argument needs this exact bound, or whether the final theorem holds. The record must not propagate a local gap into a paper-level invalidation or current-use mutation.
