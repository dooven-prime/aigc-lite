# Citation MDE v0: authenticated identity and primary-root lineage

This diagnostic extends only the frozen two-citation [v1 Case](CITATION_INFORMATION_SUFFICIENCY_CASE.md).
Its question remains whether the works denoted by the two selected candidate
matches have disjoint **primary evidence roots**. This is narrower than
independent studies, independent analysis, or support for a scientific claim.
The v1 visible bundle and Case lock are unchanged.

The shared fibre criterion, counterexample condition, and limits of
minimality are defined in [Minimal disambiguating evidence](MINIMAL_DISAMBIGUATING_EVIDENCE.md).

The additional information has two separately authenticated layers:

1. An enrolled bibliographic identity issuer signs each exact
   `candidate_bundle_sha256`, `match_id`, and `candidate_identifier`, together
   with a `canonical_work_id` and source metadata digest. This binds the
   extracted reference candidate to the issuer's claimed work identity.
2. A different enrolled provenance issuer signs the same bundle and ordered
   match IDs, the two canonical work IDs, each work's used source nodes and
   coverage flag, and a graph of `alias_of` and `derived_from` edges ending at
   primary nodes. It also signs whether its cross-work alias index is complete.

Both layers use role-separated Ed25519 signatures and host-owned public-key
enrollments. No key embedded in a submitted record is trusted. The provenance
issuer must have a different enrolled issuer ID and public key from every
identity issuer. This is a check for separate signers, not proof of separate
organizations or independent data collection. A valid signature authenticates
only that signer's assertion. The metadata and source-record digests are
format-checked, but their underlying bytes are not fetched or verified here.
The test keys and records are synthetic; they do not authenticate real arXiv
works or real datasets. A deployment would need independently governed
enrollment, source acquisition, freshness and revocation checks before using
real records.

The report records SHA-256 fingerprints of the **actual enrolled public-key
bytes** used for each identity match and for lineage, with each role, issuer
ID, and key ID. It also hashes a sorted descriptor of the entire supplied host
trust configuration, including unused enrolled keys. Names alone are not key
identities: rotating a public key under the same issuer and key ID changes
both fingerprints. Historical replay must retain the corresponding host trust
configuration snapshot. These hashes do not establish the signer's revocation
state at a past time or organizational independence.

The graph canonicalizes signed aliases, follows every signed derivation parent,
and collects terminal primary nodes. Distinct dataset labels can therefore
resolve to the same primary root. One authenticated root found in both works
refutes disjointness even when other sources remain uncovered. A positive
disjointness finding requires nonempty root sets for both works, every used
source path resolved to a primary node, complete source coverage for both
works, and a complete cross-work alias index attested by the provenance
issuer. Without those completeness statements, two different known root IDs
cannot exclude an undisclosed common root or alias.

The diagnostic checks the candidate bundle and attestation object graph before
reading the frozen bundle or serializing signed JSON. These inputs must be
acyclic JSON data with no repeated container references, at most 24 nested
levels and 4,096 nodes, and at most 1 MiB of canonical JSON. The two selected
match IDs are bounded to 256 characters each. Host trust maps have a separate
limit of 128 enrolled keys per role and require 32-byte public keys. Oversized,
deeply nested, non-JSON, or invalid UTF-8 inputs return `UNRESOLVED` with a
resource/input reason.
Removing a coverage statement or a derivation parent and then signing the
changed record with the enrolled key remains a valid signature, but the
positive result returns to `UNRESOLVED`. Merely editing signed bytes without
re-signing fails signature verification instead.

`diagnose_citation_mde` returns one of `UNRESOLVED`,
`TARGET_FALSE_UNDER_ATTESTED_LINEAGE`, or
`TARGET_TRUE_UNDER_ATTESTED_LINEAGE`. Missing or conflicting records, wrong
target identity, wrong bundle or match binding, invalid signatures, untrusted
keys, malformed or cyclic graphs, and incomplete evidence for a positive
finding fail closed to `UNRESOLVED`. The result records the bundle digest,
selected match IDs, issuer IDs, root sets, coverage, and checker source digest.
It also records signed-envelope digests and the issuer-declared source-record
digests for later re-examination; those source bytes remain unverified here.
The report includes raw-file digests of the frozen v1 checker and case lock,
which are direct semantic dependencies of this diagnostic. They are not a
complete dependency-tree or environment identity. The caller must separately
confirm that the supplied bundle is the committed
Artifact content it intends to inspect; the pure diagnostic does not query the
database or fetch the PDF or source records.

Even a positive finding is conditional on the issuer's coverage and alias
completeness assertions; the diagnostic does not independently measure them.
This v0 diagnostic creates no QualificationProfile, Gate,
`QualificationReceipt`, `CurrentUseBinding`, `AuthorizationGrant`, API, or
accepted research relation. `qualification_granted` is always `false`.
It is separately installed from [`casepacks/mde-v0`](../casepacks/mde-v0/README.md)
and exports `aigc_lite_casepacks.mde_v0.citation.diagnose_citation_mde`;
the core application does not import or register it.
