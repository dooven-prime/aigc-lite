# Candidate positive control 003 — same statement, different proof route

Record version: `external.math.alignment.pilot-001.trace-square-control.v1`
Status: **known-answer development control; fresh Lean replay incomplete**.
Authority effect: **none**. This is not a blind score or a qualification of an OpenAI Navier–Stokes claim.

## Exact source and why this is only a control

Per-record source manifest: [`manifests/trace-square-control-v1.json`](manifests/trace-square-control-v1.json). Its only hashed source is the published review PDF; the pictured Lean code has no separate source-file identity or fresh kernel result.

The source is [Bastounis–Circelli–Hansen, *Navier–Stokes lost in translation*, arXiv:2610.08144v1](https://arxiv.org/pdf/2610.08144v1), 1,710,171 bytes, SHA-256 `3282e2c4103b955d635173749e7bf887fef7af7dd39e1abdb750b951030c538b`. Physical PDF page 3 contains Example 2.2's natural-language statement and Figure 2's visible Lean code; page 4 gives the natural-language proof and explains the route difference. This is **not** an OpenAI `NavierStokesAndEuler` manuscript/Lean pair. The same published paper supplies both the pair and its interpretation, so it cannot count as review-withheld discovery or evidence that this auditor found an unseen alignment.

The manuscript statement is: for any finite real symmetric \(n\times n\) matrix \(A\), \(\operatorname{tr}(A^2)\geq0\). Figure 2 visibly names `trace_sq_nonneg_of_isSymm` and states it for `n : ℕ`, `A : Matrix (Fin n) (Fin n) ℝ`, `A.IsSymm`, and `0 ≤ Matrix.trace (A ^ 2)`. These source-level object, domain, hypothesis, and conclusion fields match. We did not independently authenticate the screenshot's chat provenance or extract an independently committed Lean file from it.

## Two paths to that result

- The natural-language proof diagonalizes the symmetric real matrix in an orthonormal eigenbasis and obtains \(\operatorname{tr}(A^2)=\sum_i\lambda_i^2\geq0\).
- The Figure 2 Lean proof does not diagonalize. It expands the diagonal of \(A^2\), uses \(a_{ji}=a_{ij}\), and obtains \(\operatorname{tr}(A^2)=\sum_{i,j}a_{ij}^2\geq0\).

The second identity follows directly from finite matrix multiplication and symmetry; it checks the mathematical reason that the route can differ without changing the stated result. It does **not** prove that the exact pictured Lean snippet has passed a fresh kernel run. The paper reports that its snippet compiled. A local Lean 4.33.0/Mathlib replay was attempted, but Lake failed while resolving the `aesop` dependency **before** the theorem was checked. Record `KERNEL_REPLAY = NOT_COMPLETED_DEPENDENCY_SETUP`, not `PASSED` or `FAILED_THEOREM`.

## Expected diagnostic behavior

| Axis | Candidate finding |
| --- | --- |
| Source-level statement fields | `ALIGNED_UNDER_CHECKED_SCOPE` — same finite real symmetric-matrix claim |
| Proof-method alignment | `DIFFERENT_ROUTE` — diagonalization versus direct sum of squares; the elementary algebra supports both routes, but the pictured code has not been freshly compiled here |
| Fresh verification of pictured code | `UNRESOLVED` pending an exact-source Mathlib build |
| Blind-evaluation eligibility | `INELIGIBLE_KNOWN_ANSWER` |
| Qualification/current-use/execution authority | `NONE` |

A statement-alignment detector must **not** label this pair `ALIGNMENT_GAP` merely because the derivations differ. The local [curated-field comparator](../src/aigc_lite_casepacks/external_math_alignment_v0/control.py) and [regression](../tests/test_positive_control.py) check precisely that limited rule: identical human-curated object/domain/hypothesis/conclusion fields plus different method tags yield `ALIGNED_UNDER_CHECKED_SCOPE` and `DIFFERENT_ROUTE`. They do **not** extract those fields from the PDF screenshot or verify their semantic mapping. Conversely, similarity of the final inequalities cannot certify an unrelated formalization's assumptions, definitions, toolchain, or kernel outcome. For a stronger positive control, freeze a primary manuscript/formal-source pair outside this review paper and replay the exact formal source in a closed environment before scoring it.
