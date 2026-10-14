# Minimal disambiguating evidence: two bounded research Cases

The frozen [Bio](BIO_INFORMATION_SUFFICIENCY_CASE.md) and
[Citation](CITATION_INFORMATION_SUFFICIENCY_CASE.md) v1 Cases establish a
specific information gap: two admissible worlds can agree on every defined
observation while disagreeing about the narrow target property. They do not
identify which world occurred.

For an observation projection `O`, target `T`, and declared world set `W`, a
candidate addition `D` is disambiguating only if admissible worlds that agree
on both `O` and `D` can no longer disagree on `T`. The v0 check conditions on
an enrolled issuer's authenticated *assertion* about `D`; it does not prove
that assertion matches the physical or publication history. Minimality is
relative to the frozen `W`, `O`, and `T`, not a claim about all possible
scientific observations or the fewest bytes of data.

The present tests establish **conditional sufficiency**, not global
minimality. An information-deletion control must produce a new, validly
signed reduced statement. Merely removing bytes from an existing signed
record tests tamper detection, not whether the removed information was
necessary. A return to `UNRESOLVED` after valid re-signing establishes at
most irreducibility with respect to that declared information unit.

The two new diagnostics use separate, versioned source contracts. Bio needs an
authenticated, exact-record-bound mapping from recorded samples to canonical
biological parent sources. Citation first needs authenticated identities for
the two bibliographic target candidates, then authenticated primary-evidence
root and derivation records for those works. A different sample label, paper
identifier, dataset accession, or signature by itself is not an independence
proof. Host-controlled trust roots are never learned from a submitted record.

The domain contracts and their executable examples are documented separately:
[Bio MDE](BIO_MINIMAL_DISAMBIGUATING_EVIDENCE_CASE.md) and
[Citation MDE](CITATION_MINIMAL_DISAMBIGUATING_EVIDENCE.md). Neither diagnostic
is part of the application Qualification Gate. Both live in the separately
installable [`MDE v0 casepack`](../casepacks/mde-v0/README.md), not the core
wheel; installation alone does not register a capability or authority path.
The optional [candidate disambiguation certificate](DISAMBIGUATION_CERTIFICATE_V0.md)
combines a recomputed conditional decision, lawful one-step ablations, and
explicit unauthenticated assumptions without changing that boundary.

There is an asymmetry in the required coverage. One authenticated common
parent or common primary root can refute the respective *narrow* distinctness
target. A positive result requires complete coverage of every relevant sample
or primary-evidence source, plus canonicalization of splits, aliases, and
derivatives. Missing, conflicting, wrongly bound, or unauthenticated lineage
remains unresolved. The diagnostics must record their exact input identities,
checker identity, issuer identities, coverage, and limitations so the result
can be re-examined.

Historical re-examination also needs the exact host-side trust configuration:
issuer and key IDs, role or registry scope, public-key fingerprints, and a
canonical fingerprint of the enrolled key set used by the diagnostic. These
non-secret identifiers distinguish a changed key under the same issuer name.
A report fingerprint alone is not a signed keyring snapshot or a revocation
history; an independent replay must obtain the historical enrollment and
revocation policy from its own trusted source and compare them explicitly.

These are candidate-only research diagnostics, not QualificationProfiles or
QualificationGate decisions. Even a diagnostic result conditional on signed
lineage does not authenticate the real-world event behind the issuer's
statement, establish experimental QC or statistical independence, prove
claim support, or enter current knowledge. They create no
`QualificationReceipt`, `CurrentUseBinding`, or `AuthorizationGrant`.

The acceptance tests for each domain must preserve the v1 visible observation
unchanged, distinguish the two declared latent assignments only when the new
source record is correctly bound and authenticated, and restore ambiguity when
an essential binding, identity, coverage, or lineage component is removed.
This is a test of *what additional evidence would decide the scoped target
under an explicit source contract*, not a claim that the synthetic fixture is
an independently verified laboratory or publication history.
