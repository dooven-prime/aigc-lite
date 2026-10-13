# Citation information sufficiency: two-root Case v1

This is a narrow, read-only counterworld Case over **two bibliographic target
candidates** represented by matches in one `CitationExtractionProposal.v1`
candidate Artifact. Its target is:

> Would the two selected bibliographic target candidates draw on disjoint
> primary evidence roots under a possible latent assignment?

That target is deliberately narrower than “two independent studies”. Distinct
data roots would not establish independent authors, methods, analysis, or
claim support. The existing citation service only anchors occurrences and
reference entries; it does not authenticate the candidates as real paper
identities or inspect their data provenance.

The test fixture creates one small PDF with two body markers, `[1]` and `[2]`,
and two formally distinct arXiv identifiers in its References section. The
fixture text says only that the reports address a question; it states nothing
about their data sources. The existing application service produces the
candidate preview and commits its exact bundle as an Artifact before this
diagnostic reads it. Both matches must be `reference_anchored`, both
occurrences `body_anchored`, and the selected target identifiers distinct.
These checks establish anchored bibliographic target candidates, not verified
external paper identities. No `ClaimRelationProposal` is used.
The checker pins the fixture PDF SHA-256
`b372b2ed0fa6a1d3b823da76480139c294cc32fb4de7729246fa4df508931d10`
and extracted page-text SHA-256
`901d420f16387cc4f2b0df88c3d475f03672536a936fb2df51faf27f99424456`;
a different PDF, including the test variant containing “Both reports reuse
dataset D1”, is rejected because its PDF SHA-256 differs from the frozen
fixture. This rejection does not demonstrate semantic recognition of a
shared-dataset disclosure.

The diagnostic then constructs two possible assignments behind the **same
frozen visible bundle**:

| Possible world | Latent primary evidence roots assigned to candidates | Target |
|---|---|---|
| Distinct roots | Report A uses D1; report B uses D2 | True |
| Shared root | Both reports use D1 | False |

Projection discards only those synthetic latent assignments. Both worlds
project to the same submitted citation bundle and digest. Therefore the
recorded citation relation alone does not decide root disjointness. The output
is `primary_evidence_root_disjointness=UNRESOLVED`, not a negative finding
about any real paper. An explicit same-root **declared control** is instead
`REFUTED_IN_DECLARED_CONTROL`; the control is not a verified provenance record.
An explicit different-root control is merely `NOT_REFUTED_BY_CONTROL`.
The diagnostic digest is computed from the supplied JSON object; the pure
diagnostic does not independently authenticate its Artifact database identity
or re-fetch the PDF. The integration test supplies a bundle returned by the
existing citation commit path and checks its stored content hash separately.

This observation-equivalence result is scoped to the frozen, lineage-silent
fixture and the citation extraction contract. For arbitrary bibliographic
targets, full text or external records may reveal data provenance; this
checker does not parse such disclosures or authenticate primary-source
lineage. The citation Artifact records a PDF SHA-256 but does not embed the
original PDF bytes or prove the source URI's identity. Mechanically anchored
bibliography entries are not verified external work identities, evidence for
the same proposition, or independent validation.

The regression checks that citation commit plus diagnostic creates no
`QualificationReceipt`, `CurrentUseBinding`, `AuthorizationGrant`, or accepted
research relation. The Case adds no QualificationProfile, API, schema change,
or general-purpose independence Gate. It supplies a second, cross-domain
example of information insufficiency; it does not by itself justify a generic
`InformationSufficiency` qualification contract.

The [cross-domain comparison](INFORMATION_SUFFICIENCY_CROSS_DOMAIN_COMPARISON.md)
compares only the structure of the two frozen counterworld Cases.

The [Citation case lock](../contracts/information-sufficiency/citation/case-lock.json)
pins the diagnostic and candidate-bundle extractor sources, tests, this
contract, generated-PDF fixture, observation projection, latent-world
constraints, and target property. It records the `pypdf` version actually
loaded for the freeze; the PDF and extracted-text digests are checked again
during replay. The lock excludes its own bytes; the publishing Git commit is
its external revision identity, not proof of real paper identity.
