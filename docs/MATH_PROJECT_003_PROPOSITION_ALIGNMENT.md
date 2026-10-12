# Math 003 proposition alignment and replay boundary

This is a correspondence register, not a mathematical review verdict. The
frozen `aigc-lite` case remains the Riemann-zeta `7/8` subclaim at OpenAI/math
commit `adc7f1241b42e322a6451854ab7e4b4c146bf78a`. Nothing in this table
changes its source snapshot, ClaimRevision, challenge, or qualification policy.

| Object | Source commit and exact location | Statement / scope | Lean declaration | Review coverage and permitted relation |
| --- | --- | --- | --- | --- |
| Frozen zeta case | `adc7f124…`; `preprints/The-Quasi-Riemann-Hypothesis-September-30-2026/paper.pdf`, printed p. 4, Theorem 1.1; PDF SHA-256 `8fe93046f8cf5ef1ba5969c89addc02d76311adc4ee907509ff9cd96f7ec99e7` | For every complex `s`, strict `Re(s)>7/8` implies the Riemann zeta function has no zero there. Only the zeta consequence, not the whole Hecke/Dirichlet theorem or applications. The principal pole at `s=1` is allowed. | `OAI.riemannZeta_ne_zero_of_seven_eighths_lt_re` in `lean/ComparatorChallenges/QuasiRiemannHypothesis.lean`, checked against `lean/OAI/NumberTheory/DirichletL/Nonvanishing.lean` by `QuasiRiemannHypothesis.json`; challenge SHA-256 `065f8c9a01d28db78c8b1bfc5083b535082a2d2aac2230d563f98b8caa812cc5` | The later RIME paper reading is **not** a same-snapshot expert review. Comparator/kernel and paper-to-challenge alignment remain unestablished for this frozen case. |
| Later main-paper reading | `fd4aeeb2ee4fc729c18d98444fed42fd0529eeeb`; `preprints/The-Quasi-Riemann-Hypothesis-September-30-2026/build/paper.tex` | The later source still discusses the stronger `7/8` route, but is a different source revision from the frozen PDF. | The selected zeta challenge/solution files have no diff between these two commits; this does **not** identify the paper bytes or prove semantics. | The proposition audit inspects the common-signal reduction and selected entry points, not all analytic antecedents or a replay. It may motivate a future, separately versioned semantic comparison; no support edge is created now. |
| Alternate main theorem | `fd4aeeb2…`; `preprints/The-Quasi-Riemann-Hypothesis-October-5-2026/build/paper2.tex`, `thm:main` (line 73); source SHA-256 `9fd2efe11c3694d7cf75ab49e614c5244b17c9cf1875fb168d869b27cf344e87` | Hecke/Dirichlet/zeta nonvanishing only in strict `Re(s)>11/12`. Since `7/8 < 11/12`, this is the weaker half-plane. | No `11/12` challenge is listed in the inspected `lean/docs/003.md` Comparator index. Do not substitute the `7/8` zeta declaration. | Paper reconstruction concerns this route; it cannot support the frozen `7/8` target by family membership. |
| Alternate mean square | Same `paper2.tex`, `thm:ms` (line 679), equation `eq:ms` | The quantified estimate is for every specified smooth weight and the declared row range; it is a premise of the `11/12` theorem, not the frozen zeta conclusion. | No corresponding declaration identified in the inspected Comparator index. | RIME's conditional review reconstructs mechanisms and names remaining object-level coverage. No end-to-end MS certification or machine replay is asserted. |
| Theta reflection input | Same `paper2.tex`, `lem:reflection` (line 1805), `eq:reflection`, with `lem:theta-realization` (line 1686) and uniformity lemma | The actual completed-sum/theta reflection and specified uniformity needed by the alternate mean-square route. | No corresponding declaration identified in the inspected Comparator index. | RIME's paper reconstruction covers this input relative to named classical results; it is not a kernel result, a review of the stronger `7/8` proof, or an independent-institution attestation. |

The RIME reading documents are internal and pinned here as **candidate review
sources**, not as an `EXPERT_REVIEW` VerificationAttempt:

- `rime-lite/docs/internal/active/OPENAI_MATH_003_PROPOSITION_AUDIT.md`,
  SHA-256 `c2af208cb66680a0c62a4017643c27595b53fbf011d0201da1a9a8a458c5779f`.
- `rime-lite/docs/internal/active/OPENAI_MATH_003_PROP31_CONDITIONAL_REVIEW.md`,
  SHA-256 `35365f72f37ab606072500d2f788cd09ea48bb3b7ae542a01ee3224849e9a139`.

Their local paths are not public source closure. A stored copy must preserve
the exact bytes and report its retrieval limitation. Source hashes authenticate
the bytes compared locally, not the authorship, correctness, Git tree, or
external trust domain. No `supports`, `entails`, qualification, current-use, or
execution-authority transition follows from importing these notes.

## Claim-specific evidence ladder

1. **Source identity:** the old 31-file snapshot and read-only patched
   dependency closure are checked by content hash. This does not prove the
   mathematical claim.
2. **Machine replay:** the original `7/8` Comparator challenge must run before
   the solution under the pinned source/toolchain/policy. A timeout leaves the
   statement comparison and kernel status undetermined. Cache integrity never
   substitutes for this run.
3. **Semantic alignment:** separately compare the printed Theorem 1.1 zeta
   consequence to the exact challenge quantifier, strict threshold, Mathlib
   `riemannZeta` definition, and the `s=1` pole convention. This is not supplied
   by Comparator success. The profile requires a same-claim/snapshot non-model
   `EXPERT_REVIEW`; this register does not impersonate that reviewer.
4. **Qualification and use:** the deterministic gate may evaluate the frozen
   evidence only after its declared criteria are met. Qualification alone does
   not create a knowledge `CurrentUseBinding` or authorization.
5. **External re-execution:** an operator other than the local builder must
   independently obtain the public pinned bytes and toolchain, execute the
   fixed challenge-first command, and publish an input/environment/result
   digest. A second machine is not by itself an independently authenticated
   organization.

Keep local replay and external re-execution as separate observations. A
passing terminal theorem would validate that formal statement under its
declared kernel assumptions; it would not certify every intermediate
theta/Poisson proof mechanism in the paper.
