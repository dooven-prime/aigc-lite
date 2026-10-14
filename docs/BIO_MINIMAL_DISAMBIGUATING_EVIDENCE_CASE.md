# Bio minimal disambiguating evidence v0

The frozen [Bio information-sufficiency Case](BIO_INFORMATION_SUFFICIENCY_CASE.md)
leaves one narrow question unresolved: do all recorded measurement pairs have
different biological parent sources? Its two possible worlds share the same L1
instrument and sample-mapping observation. This candidate diagnostic adds a
separate, signed sample-to-parent statement. The v1 projection, counterworlds,
and case lock remain unchanged.

The shared fibre criterion, counterexample condition, and limits of
minimality are defined in [Minimal disambiguating evidence](MINIMAL_DISAMBIGUATING_EVIDENCE.md).

The packaged [source contract](../casepacks/mde-v0/src/aigc_lite_casepacks/mde_v0/bio_lineage_contract_v0.json)
is `bio.sample-lineage.mde.v0`. The caller supplies
the **original UTF-8 JSON bytes** of both L1 records, one or more lineage-record
byte strings, and three host-enrolled Ed25519 trust roots: an L1 instrument
issuer, a separately enrolled L1 sample custodian, and a lineage issuer with authority
for a named parent registry. Keys carried by evidence are never trusted. The
diagnostic verifies both L1 signatures, their study and pair/sample alignment,
and `custodian.payload.instrument_sha256 == SHA256(raw instrument bytes)`. Each
lineage envelope signs these exact two raw-byte hashes, the frozen v1 case ID
and target, a parent registry, a fixed canonical-parent semantics claim, a
completeness declaration, and its sample-to-parent entries. JSON duplicate keys
are rejected.

Different L1 issuer IDs and keys are required. This does not establish that the
two signers are organizationally independent.

The lineage payload has these exact fields:

```text
case_id, target, instrument_sha256, custodian_sha256,
parent_registry, lineage_semantics, complete_for_recorded_samples,
sample_parents: [{sample_id, canonical_parent_id}, ...]
```

`lineage_semantics` must be
`canonical_biological_parent_with_split_alias_resolution`. This is a signed
claim by the enrolled lineage issuer that aliquot splits and aliases resolve
to the same canonical parent ID in its registry. The code verifies signature,
scope, and consistency of that statement; it cannot independently inspect a
laboratory's complete physical history. If a real source cannot support that
claim, it should not issue a complete attestation.

| Verified addition | Narrow diagnostic status |
| --- | --- |
| Two recorded samples have the same signed canonical parent, even when other samples are missing | `TARGET_FALSE_UNDER_ATTESTED_LINEAGE` |
| Every recorded sample has a distinct canonical parent and a signed complete-coverage attestation | `TARGET_TRUE_UNDER_ATTESTED_LINEAGE` |
| Missing coverage, absent attestation, wrong binding, untrusted signature, conflicting mapping or registry | `UNRESOLVED` |

Several signed fragments may reveal a common parent. Their union cannot
replace a single complete-coverage attestation for a positive result. A
conflicting or malformed supplied record causes the whole candidate input to
remain unresolved. The result reports input byte hashes, issuer and registry,
coverage, checker source hash, and hashes of the v1 observation checker, L1
signature verifier, v1 case lock, and this packaged source contract. The
contract bytes are included in the optional casepack wheel. The reviewed
hashes let an installed wheel omit the repository's separate v1 case lock; a
source-tree test checks those constants against their original bytes. These
hashes identify this bounded code closure;
they are not a complete
freeze of Python, third-party packages, or runtime configuration.

The diagnostic checks types, count, individual byte sizes, and the combined
input size before hashing or parsing: up to 1,000,000 bytes for each L1 record,
100,000 bytes for each lineage record, 64 lineage records, and 3,000,000 input
bytes in total. JSON nesting is limited to 64 levels before decoding. Excess
input or pathological nesting returns `UNRESOLVED`; it does not reach a
signature check or target decision. Host key sets are limited to 64 enrolled
keys for each of the L1 and lineage sets; each host key issuer, key ID, role,
and registry label is limited to 256 characters.

For each accepted host trust configuration, the report lists sorted public-key
descriptors with `role`, `issuer_id`, `key_id`, `parent_registry` where relevant,
and SHA-256 of the public key. It also contains a SHA-256 fingerprint of those
descriptors and identifies the keys actually used to verify the submitted
records. No private-key bytes are accepted or reported. This fingerprint names
the configured public keys, not their revocation state at signing time or the
organizational independence of their holders. Identical public-key hashes
across roles are visible in the report and do not establish independent actors.

The information-ablation test starts from a signed complete positive example.
Removing a sample mapping while changing coverage to incomplete and re-signing
returns `UNRESOLVED` with the issuer assertion still authenticated. Keeping
all mappings but withdrawing the completeness claim and re-signing does the
same. A payload edit without re-signing instead fails signature verification.

The records in tests are **synthetic signed fixtures**. A valid signature
authenticates what an enrolled issuer asserted; it does not establish that the
named biological samples existed, that the true lineage was entered correctly,
or that the registry covered every alias or split. Even a positive narrow
diagnostic cannot establish quality control, statistical independence, causal
effects, or clinical validity. The code does not run the L1 Qualification Gate
or experiment replay. It creates no QualificationReceipt, CurrentUseBinding,
or AuthorizationGrant and does not qualify any broader claim.

The optional research casepack exports
`aigc_lite_casepacks.mde_v0.bio.diagnose_bio_mde`. It is separately
installed from [`casepacks/mde-v0`](../casepacks/mde-v0/README.md), and is a
pure, candidate-only diagnostic, with no HTTP or MCP route and no persistence.
Its result always has `qualification_granted: false`.
