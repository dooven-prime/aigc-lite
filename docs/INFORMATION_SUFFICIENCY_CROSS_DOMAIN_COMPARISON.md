# Bio and Citation information sufficiency: cross-domain comparison

The two frozen Cases make the same limited logical move: hold every defined
visible field fixed, vary only a latent source assignment, and obtain opposite
answers to a narrow target. Each then reports `UNRESOLVED` for that target.
This is a constructive counterexample to deciding the target from those
observations alone, not a finding about what happened in the laboratory or
which sources a bibliographic target actually used.

| Case | Frozen visible observation | Latent assignment | Narrow target |
|---|---|---|---|
| [Bio](BIO_INFORMATION_SUFFICIENCY_CASE.md) | Exact L1 instrument and custodian record bytes, including sample mapping, study, pair, batch, measurement, and time fields | Biological parent source per sample | Every recorded measurement pair has a distinct parent source |
| [Citation](CITATION_INFORMATION_SUFFICIENCY_CASE.md) | Candidate-only citation bundle from one pinned PDF, including source snapshot, body and reference anchors, match IDs, and formally distinct target identifiers | Primary evidence root set per bibliographic target candidate | The two root sets are disjoint |

The proposed `observation_equivalence_invariant` is a deterministic check on
each Case's two constructed worlds. It requires (1) a complete field-by-field
equality of both worlds' defined observation projections, not only equal
projection hashes; (2) each world independently satisfying that Case's input
and latent-world constraints; and (3) opposite target truth values. Bio must
retain the original pair-to-sample mapping, study and pair values, batch IDs,
and collection and assay times while changing only parent-source labels.
Citation must retain the pinned PDF and page-text identity, source URI and
extractor metadata, exact body and reference anchors, bibliographic match
fields, and candidate-only state while changing only latent root sets. Equal
hashes are useful recorded fingerprints; they are not a substitute for these
structural assertions.

The explicit same-parent or shared-root controls answer a different question:
the declared synthetic assignment refutes the narrow target. They do not
authenticate lineage. In the Citation test, a second PDF that says “Both
reports reuse dataset D1” is rejected by the pinned PDF hash. That test does
not show that the diagnostic understands the sentence.

The domains do not share a real-world verification standard. Bio's L1 Gate
authenticates record binding only; biological independence would require its
own source-lineage and experimental criteria. Citation extraction anchors
bibliographic target candidates only; actual evidence-root provenance would
require separately authenticated sources and an explicit research question.
Both Cases remain candidate-only research diagnostics. This comparison
introduces no L2 Profile, Qualification Gate, current-use binding, or
automatic experiment decision.
