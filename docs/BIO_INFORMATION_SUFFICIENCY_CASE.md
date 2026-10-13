# Bio information sufficiency: two-world Case v1

The target is deliberately narrow: **do these recorded measurement pairs
originate from distinct biological parent sources?** This Case does not test
drug efficacy, experimental QC, statistical generalization, or whether any
laboratory event actually occurred.

The existing `bio.observation.provenance.v0` Profile authenticates exact
instrument and custodian record bytes through host-enrolled keys. Its sample
mapping contains `pair_id`, `sample_id`, `collected_at`, and `assayed_at`, but
no parent culture/source lineage. Distinct sample IDs are therefore not a
contractual statement that their biological origins are distinct. This Case
does **not** change the L1 Profile or its issued record-binding receipts.
The read-only diagnostic accepts only the frozen L1 envelope, payload, study,
pair, and sample field shapes; an extra `parent_lineage` field at any of these
levels is rejected rather than silently treated as unobserved. It does not
authenticate signatures itself; that remains the separate L1 Gate's task.

`app/profiles/bio_information_sufficiency.py` constructs two *possible* worlds
for the same visible records:

| Possible world | Hidden parent sources | Target |
|---|---|---|
| Different cultures | One distinct parent per sample | True |
| One culture, multiple aliquots | The same parent for every sample | False |

Both worlds project to precisely the same existing sample fields and share
the very same instrument and custodian record bytes. The diagnostic records
their common visible-data digest and projected-sample digest, plus opposite
target values and the diagnostic source-file digest. It does not assert which
world actually occurred. These are canonical-JSON content digests of the
supplied envelopes and projection, not new authentication of stored Artifact
bytes. This is a finite, constructive information-insufficiency result relative to the stated
possible-world set, observation projection, and target. It is analogous to
the distinction between observed information and target identifiability in
[statistical work](https://link.springer.com/article/10.1007/s00362-023-01414-3),
**not** a proof of a statistical
identifiability theorem or of real-world sample dependence.

The integration regression uses the real L1 Gate with two enrolled signing
roles and exact record-binding replay. Its expected joint output is:

```text
record_binding (L1 Gate)       ADMITTED
parent_source_distinctness     UNRESOLVED (read-only sufficiency diagnostic)
L1 Gate on broader claim       NOT_APPLICABLE
broader QualificationReceipt  absent
CurrentUseBinding              absent
AuthorizationGrant             absent
```

`UNRESOLVED` here is a diagnostic about missing information, **not** a new
QualificationEvaluation. It never writes a receipt or current-use binding.
An explicit same-parent **synthetic control** returns
`REFUTED_IN_DECLARED_CONTROL`; this is different from missing lineage. That
control does not authenticate a real lineage record. Even an explicit list of
distinct parent labels merely produces `NOT_REFUTED_BY_CONTROL`, not a grant.
Even authenticated distinct parent origins would not by themselves establish
statistical independence or experimental validity.

The next genuine L2 Profile would need a versioned biological-source and
sample-lineage contract, host-owned provenance roots, relevant QC, and an
explicit unit-of-independence definition. This Case deliberately stops before
that boundary. The separate [Citation information-sufficiency Case](CITATION_INFORMATION_SUFFICIENCY_CASE.md)
tests a related counterworld structure over bibliographic target candidates.
The [cross-domain comparison](INFORMATION_SUFFICIENCY_CROSS_DOMAIN_COMPARISON.md)
does not treat either Case as real-world independence verification.

The [Bio case lock](../contracts/information-sufficiency/bio/case-lock.json)
pins the diagnostic source, L1 record-binding implementation, test and fixture
sources, this contract, the unsigned input template, observation projection,
latent-world constraints, and target property. Fresh test signatures are not
frozen credentials. The lock excludes its own bytes; the publishing Git commit
provides its external revision identity.
