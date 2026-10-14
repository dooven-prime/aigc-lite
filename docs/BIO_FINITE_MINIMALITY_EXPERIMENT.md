# Bio finite refutation subset: a candidate research experiment

This experiment extends the [Bio MDE v0 diagnostic](BIO_MINIMAL_DISAMBIGUATING_EVIDENCE_CASE.md)
without changing the casepack, core Gate, L1 Profile, or v1 observation Case.
The executable check is
[`tests/test_bio_finite_minimality_experiment.py`](../tests/test_bio_finite_minimality_experiment.py).
It uses synthetic, test-signed records; it is not an assertion about any real
laboratory's samples.

## Frozen question within this experiment

Fix the exact three-sample L1 instrument and custodian bytes produced by one
test fixture. These bytes, including study, pair, batch, sample, time, issuer,
signature, and instrument binding fields, are the old observation `O`. Within
each test invocation they are identical in every counterworld. Let `S1`,
`S2`, `S3` be those recorded sample IDs. The declared finite world domain
is the 27 assignments of a canonical parent label in `{X,Y,Z}` to each
sample. The target `T` asks whether **all three** parent labels are pairwise
distinct.
No other observation, world, or target is substituted during an ablation.

The baseline synthetic signed lineage says `S1=X, S2=X, S3=Y`. Its complete
record gives the narrow MDE status `TARGET_FALSE_UNDER_ATTESTED_LINEAGE`.
Let `D_i` be the parent mapping for `Si`. A mathematical deletion is the
stable projection that forgets one coordinate `D_i` while retaining all
other asserted mappings, the old observation, the target, the issuer and
registry meaning, and the finite world domain. The valid signed **record**
for that projection must withdraw `complete_for_recorded_samples`; this is
a signed epistemic claim that is updated for a valid reduced record, but is
outside the mapping-coordinate units counted in this finite comparison.
Each reduced record is freshly signed by the synthetic test issuer. Merely
altering the original signed bytes would test signature integrity, not
information necessity.

| Retained parent mappings | Compatible finite worlds | Target values | MDE v0 status |
| --- | ---: | --- | --- |
| `S1=X, S2=X, S3=Y` | 1 | `{False}` | `TARGET_FALSE_UNDER_ATTESTED_LINEAGE` |
| `S2=X, S3=Y` (remove `S1`) | 3 | `{False, True}` | `UNRESOLVED` |
| `S1=X, S3=Y` (remove `S2`) | 3 | `{False, True}` | `UNRESOLVED` |
| `S1=X, S2=X` (remove `S3`) | 3 | `{False}` | `TARGET_FALSE_UNDER_ATTESTED_LINEAGE` |

For the first ablation, the two explicit worlds are `(X,X,Y)` and
`(Z,X,Y)`. For the second, they are `(X,X,Y)` and `(X,Z,Y)`. In each pair,
the test checks both worlds against the full old L1 observation and the same
remaining signed mappings, then checks that `T` differs. The negative
controls alter an old observation field or a retained mapping and confirm
the putative counterworld is rejected. Thus `UNRESOLVED` here is backed by
constructive finite counterexamples, not merely a checker abstention.

The two mappings `S1=X` and `S2=X` alone determine `T=False` in this finite
fibre; deleting either from that pair permits both target values. They form
an **inclusion-minimal refutation subset relative to these three coordinate
units, this old observation, this target, and this declared world domain**.
`S3=Y` is redundant for this refutation, so the full three-mapping record is
not inclusion-minimal. This is neither a globally minimal evidence scheme
nor domain-wide sufficiency: a different evidence type, such as one directly
attesting `S1` and `S2` share a parent, is outside the comparison.

## Source and authority limits

The experiment's test helper enumerates a finite *declared* domain; it does
not establish that `{X,Y,Z}` exhausts possible physical parent sources.
The old L1 records and lineage statements use ephemeral synthetic signing
keys. Signature validity and exact byte binding are tested, but neither
authenticates a real sample, an issuer's authority outside the test, or the
truth and completeness of its registry. Across test runs, generated signing
keys and record hashes differ;
the **logical verdict and counterworld construction** are reproducible, not
a cross-run identical byte artifact. The two worlds after ablation share the
new reduced record; they are not simultaneously compatible with the original
complete record, which has been removed from that evidence fibre.

This result concerns conditional information sufficiency for one narrow
parent-source-distinctness question. It does not establish biological or
statistical independence, experimental validity, a general minimum evidence
set, or a QualificationReceipt. No CurrentUseBinding or AuthorizationGrant
is created. The experiment remains candidate-only, separate from software
release and from any claim about real source provenance.

## Source-specific CI verification

The pushed source commit
[`a222dc00859eb354759f31755eeafc3db5215328`](https://github.com/dooven-prime/aigc-lite/commit/a222dc00859eb354759f31755eeafc3db5215328)
on `codex/bio-finite-minimality` was checked by
[GitHub Actions run 38550738327](https://github.com/dooven-prime/aigc-lite/actions/runs/38550738327)
on 2026-10-14. All five jobs completed successfully: `test`, `frontend`,
`wheel-smoke`, `postgres`, and `docker-smoke`. The `test` job reported
**495 passed, 7 skipped**. This is a CI result for that exact Bio source
commit, not for this later documentation-only commit or for another branch.
In particular, it does not validate the separate Long-Horizon Recovery
candidate-only contract, whose reported 64 focused tests are a distinct
result. Passing repository CI also does not authenticate real biological
source claims or expand the experiment's conditional scientific conclusion.

Run the focused check with:

```sh
python -m pytest -q tests/test_bio_finite_minimality_experiment.py
```
