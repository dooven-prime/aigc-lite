# Minimal disambiguating evidence: definitions and two bounded Cases

The frozen [Bio](BIO_INFORMATION_SUFFICIENCY_CASE.md) and
[Citation](CITATION_INFORMATION_SUFFICIENCY_CASE.md) v1 Cases establish a
specific information gap: two admissible worlds can agree on every defined
observation while disagreeing about the narrow target property. They do not
identify which world occurred.

## Observation and counterexample

Fix a declared set of admissible worlds `W`, an observation map
`O: W -> V`, and a binary target `T: W -> {0,1}`. For a visible value `o`,
the possible target values are

$$
I_T(o)=\{T(w):w\in W,\ O(w)=o\}.
$$

The target is determined by the observation on the declared domain exactly
when every nonempty observation fibre has a singleton `I_T(o)`. A
counterexample requires two **individually admissible** worlds `w_0,w_1` with
`O(w_0)=O(w_1)` and `T(w_0) != T(w_1)`. It rules out an exact deterministic
`f` satisfying $T=f\circ O$ on `W`; it neither identifies the actual world nor
rules out a refined observation, restricted domain, relation-valued output,
or useful probabilistic prediction. An equal digest is not by itself a
field-by-field observation-equivalence check.

The frozen v1 Bio and Citation tests construct one such pair each, for their
own `W`, `O`, and `T`. They do not establish that every real laboratory or
publication record has that ambiguity.

## Added information and conditional decisions

Let `D=(D_u)_{u in U}` be a declared family of additional information units.
On a fixed old fibre `O^{-1}(o)`, `D` is disambiguating if

$$
O(w)=O(w')=o\ \land\ D(w)=D(w')\quad\Longrightarrow\quad T(w)=T(w')
$$

for every admissible `w,w'`. A domain-wide assertion requires the same
implication for **every** nonempty old fibre, not just one selected fixture.
Equivalently, every nonempty joint fibre of `(O,D)` has one target value.
Writing $I_T(o,d)=\{T(w):O(w)=o,\ D(w)=d\}$, this says
$|I_T(o,d)|=1$ whenever the joint fibre is nonempty.
The definition is relative to the declared world class, observation fields,
target, and meaning of the added units; it is not a statement about every
possible scientific measurement.

The v0 programs receive a signed **record asserting** `D`, not direct access
to `D(w)` in the physical or publication world. They check enrolled keys,
record binding, consistency, and declared coverage. Interpreting a verified
record as true, complete, and canonically resolved requires a further source
assumption `A`. Under that assumption, a nonempty set of worlds compatible
with the old observation and the record may have target set `{0}` or `{1}`;
the diagnostic then reports `TARGET_FALSE_UNDER_ATTESTED_LINEAGE` or
`TARGET_TRUE_UNDER_ATTESTED_LINEAGE`. The v0 programs do not enumerate `W` or
prove that this compatible set is nonempty. Detectable signature or binding
failures, record conflicts, and missing required coverage yield `UNRESOLVED`;
an undetected empty fibre is still an unverified model or source assumption,
never a demonstrated success. These statuses are conditional diagnostics,
not Qualification Gate verdicts. A signature authenticates the issuer's
statement, not assumption `A` or the underlying event.

One authenticated common parent or common primary root can refute the
respective *narrow* distinctness target without complete coverage. A positive
result needs complete coverage of all relevant samples or used sources and
canonical resolution of aliases, splits, and derivatives. This explains why
the two domains have different concrete `D` contracts while sharing the
fibre criterion.

## Minimality boundary

Suppose `U` is a finite, fixed set of independently removable information
units, and `D_S` is the stable projection to units `S subseteq U`. Inclusion
minimality on a declared old fibre would require both full disambiguation by
`D_U` and, for **each** `u in U`, an admissible counterworld pair in that
same fibre `O^{-1}(o)` with equal `D_{U\setminus\{u\}}` but opposite `T`.
Under these stable-projection assumptions, one-unit deletions suffice to rule
out every proper subset.
They do not prove minimum byte length, minimum record count, uniqueness, or
necessity among different kinds of measurements.

The present tests do **not** prove that minimality condition. Their reduced
records are validly re-signed: removing a Bio sample mapping or completeness
claim, or removing Citation coverage or a derivation parent, makes the v0
checker return `UNRESOLVED`. This establishes operational non-decision under
the reduced contract. It is not, by itself, a counterworld pair for every
deletion, and it cannot exclude a different sufficient source contract.
Removing bytes without re-signing tests signature integrity instead of
information necessity. Nor may an ablation silently change `W`, `T`, issuer
authority, or the meaning of retained fields and still count as a minimality
test.

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

Missing, conflicting, wrongly bound, or unauthenticated lineage remains
unresolved. The diagnostics must record their exact input identities, checker
identity, issuer identities, coverage, and limitations so the result can be
re-examined.

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

The acceptance tests for each domain preserve the v1 visible observation,
distinguish the declared latent assignments only under correctly bound and
authenticated new records, and restore `UNRESOLVED` when a required binding,
identity, coverage, or lineage component is removed. This tests the v0
diagnostic's conditional decision rule, not global minimality or an
independently verified laboratory or publication history.
