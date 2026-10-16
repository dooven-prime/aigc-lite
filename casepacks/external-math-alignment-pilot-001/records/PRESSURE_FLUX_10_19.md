# Candidate alignment record 002 — pressure-flux estimate (10.19)

Record version: `external.math.alignment.pilot-001.pressure-flux-10-19.v1`
Status: **primary-source-checked local gap; global implication unresolved**.
Authority effect: **none**. This is a source comparison, not an invalidation decision or a Lean kernel receipt.

## Source identities and locations

Per-record source manifest: [`manifests/pressure-flux-10-19-v1.json`](manifests/pressure-flux-10-19-v1.json). It records selected source bytes and human audit scope; the packaged Lemma 8.6 detector lock does not cover this record.

| Source | Frozen byte identity | Checked location |
| --- | --- | --- |
| [OpenAI manuscript](https://cdn.openai.com/pdf/32d9f210-8b73-45e0-91bc-82a30aef8a9a/navier-stokes.pdf) | 2,959,204 bytes; SHA-256 `0e779481c4da40bd28d1e642e1d8ca57447d129610df28dfa5a11e9af8ae228f` | Physical/printed PDF pages 122–123, definitions, (10.17), and (10.19) |
| [PressureFlux.lean](https://github.com/openai/NavierStokesAndEuler/blob/f9e8bc5b38b6e212696e8a30e3e91517af887bbd/NavierStokes/R3/PressureFlux.lean#L576-L598) | Commit claim `f9e8bc5b38b6e212696e8a30e3e91517af887bbd`; 34,598 bytes; SHA-256 `0adffc7879e8e5e226e5d4b2d721139a4ff12a04b4c9b4ebc12cf0d0f597fc2a` | `exists_uniform_actual_pressure_flux_bound`, lines 576–598 |
| [ComparisonSetup.lean](https://github.com/openai/NavierStokesAndEuler/blob/f9e8bc5b38b6e212696e8a30e3e91517af887bbd/NavierStokes/R3/ComparisonSetup.lean#L44-L51) | 1,985 bytes; SHA-256 `2c1335d9da753d25c73b2b102b0c7686648c8928ddfac416d87284bb9b20fbde` | Definitions of the formal `A` and `B` |
| [ComparisonCutoffs.lean](https://github.com/openai/NavierStokesAndEuler/blob/f9e8bc5b38b6e212696e8a30e3e91517af887bbd/NavierStokes/R3/ComparisonCutoffs.lean#L31-L38) | 11,984 bytes; SHA-256 `a4fba9b793ad51dca007cc6d8f6adf30f06b4f79c9c9811384ec299da230ab6b` | Cutoff weight `χ_R=φ_R^8` |
| [Published external review](https://arxiv.org/pdf/2610.08144v1) | arXiv:2610.08144v1; 1,710,171 bytes; SHA-256 `3282e2c4103b955d635173749e7bf887fef7af7dd39e1abdb750b951030c538b` | `3.2, known-answer comparison, **not** a primary-source substitute |

The discrepancy was publicly alleged before this check. This record **rechecks the named PDF and pinned Lean source bytes directly**, but it is not an independent *discovery*, an organizationally independent verification, or a blind result. Hashes prove byte identity of retrieved files, not publisher authentication, full Git-tree integrity, or theorem validity.

## Objects, assumptions, quantifiers, and conclusions

The manuscript defines \(A_R=(\int\chi_R|\nabla w|^2)^{1/2}\) and \(B_R=\|\phi_R^4w\|_6\), where \(\chi_R=\phi_R^8\). In its Lemma 10.5 setting, \(u,p\) are the constructed smooth fields, \(v,P\) are a smooth finite-energy competitor under the same force and initial data, \(w=v-u\), and \(\pi=P-p\). For \(R\geq1\) and almost every \(t\in(0,T)\), manuscript (10.19) bounds the pressure flux by

\[
\left|\int \pi w\cdot\nabla\chi_R\right|
\leq C_T R^{-1}\left[(B_R+1)B_R^{1/2}
  +R^{-3/4}B_R^{3/4}\right].
\]

The pinned Lean theorem `exists_uniform_actual_pressure_flux_bound` gives, in the corresponding notation, a bound of the form

\[
\left|\int (p-q)(u-v)\cdot\nabla\chi_R\right|
\leq C_P\left[
 (B_R^{1/2}+1)\left(\frac{A_R}{R}+\frac{1}{R^2}\right)
 +R^{-7/4}B_R^{3/4}\right],
\]

for every interior \(t\in(0,T)\) and \(R\geq1\), under `PressureRecovery.Hypotheses` plus explicit uniform bounds on the velocity difference, `u`, and the tensor difference. Mapping Lean \((u,p)\) to the constructed pair and \((v,q)\) to the competitor reverses **both** differences relative to manuscript \((\pi,w)\); their product is unchanged. The formal `A`, `B`, and cutoff definitions were checked against the above notation. The detailed equivalence of all formal and manuscript hypotheses remains **unresolved**. The all-\(t\) formal quantifier is stronger than the manuscript's almost-everywhere qualifier and is not the problematic direction.

The consequential local difference is the explicit \(A_R\) term in the Lean right-hand side. Manuscript (10.17) states \(B_R\leq C(A_R+R^{-1}\|w\|_2)\); it bounds \(B_R\) **from above** by \(A_R\)-based quantities, not \(A_R\) by \(B_R\). Thus (10.17) alone cannot remove \(A_R\) from the Lean estimate to recover manuscript (10.19). The second \(R^{-7/4}B_R^{3/4}\) term has the same exponent after distributing manuscript's outer \(R^{-1}\).

## Proof-route and downstream dependency slice

The manuscript's page-122–123 argument uses an \(L^{3/2}\) Riesz-transform route with a commutator/localization estimate. The pinned formal source instead reaches `exists_uniform_actual_pressure_flux_bound` through `exists_uniform_canonicalCutoffFlux_bound` (lines 546–572), `canonicalCutoffFlux_bound` (516–543), and `cutoff_pressurePair_bound` (496–513). Its local estimate `localized_pair_bound` (315–353) relies on `riesz_rTest_bound` (289–305), using an \(L^6\) Riesz test derived via Sobolev and an \(L^2\)-derivative bound; another branch supplies a commutator bound (463–493). These are **different methods**. The method difference is recorded separately and is not, by itself, a statement gap.

The exact formal theorem is consumed by [`WholeSpaceUniqueness.lean:60–68`](https://github.com/openai/NavierStokesAndEuler/blob/f9e8bc5b38b6e212696e8a30e3e91517af887bbd/NavierStokes/R3/WholeSpaceUniqueness.lean#L60-L68) (5,893 bytes; SHA-256 `c5db96b5a524d852cecc34947be821e1ccab983febdb3fd71f398cabf5e46dcf). [`WholeSpaceComparisonClosure.lean:25–52,90–103`](https://github.com/openai/NavierStokesAndEuler/blob/f9e8bc5b38b6e212696e8a30e3e91517af887bbd/NavierStokes/R3/WholeSpaceComparisonClosure.lean#L90-L103) (8,409 bytes; SHA-256 `a341c40686a2ab0f4b5916b2813814a176438210df63350b2d88b308ce106b3d) defines an \(A_R\)-based pressure envelope and uses a \(B_R\)-by-\(A_R\) relation in its energy closure. This is a **source-level dependency trace**, not a complete transitive axiomatic or semantic audit; it also shows why one cannot infer that a different \(A_R\)-based formal route necessarily fails downstream.

## Local diagnosis and impact

`LOCAL_STATEMENT_ALIGNMENT = ALIGNMENT_GAP`: this Lean declaration does not directly furnish the manuscript's \(B_R\)-only (10.19). `PROOF_METHOD_ALIGNMENT = DIFFERENT_ROUTE`. `FULL_HYPOTHESIS_ALIGNMENT = UNRESOLVED`. `KERNEL_REPLAY = NOT_RUN`. Whether another bound supplies manuscript (10.19), whether that bound is true, whether the formal alternative proves the intended final theorem, and whether the Clay target is met are **not decided**. No current-use, qualification, invalidation, or authorization state changes follow from this candidate record.
