# Retrospective in-vitro paired observation — v0

This is a deliberately narrow, data-only research Case. It accepts a **declared
completed** nonclinical in-vitro experiment as bounded, matched measurement
pairs. The server freezes the submitted JSON as an Artifact, constructs an exact
dataset-scoped `ClaimRevision`, replays paired differences without floating-point
rounding, records a Run/Step and VerificationAttempt, then invokes the
server-owned `bio.in_vitro.observed_pair_delta.v0` Qualification Gate.

`ADMITTED` means only that the observed mean treated-minus-control difference
in **these submitted bytes** meets the submitted direction and positive
threshold. It is not a population inference, causal claim, assay validation,
drug safety/efficacy result, regulatory conclusion, or permission to run another
experiment. The profile's integer version is `1` because the existing ledger
requires positive versions; `v0` in the ID marks this experimental domain
contract. A future statistical-inference contract must use a new profile and
policy version rather than silently changing this meaning.

## Input and result

`GET /api/research/bio-in-vitro/contract` exposes the schema summary, profile
hash, checker source-file digest, and limitations. The source digest is included
in the profile snapshot; a changed checker must not silently reuse its old
current-use qualification. This is byte identity, not an independent audit of
checker correctness. Workspace admins may submit
one witness to `POST /api/research/bio-in-vitro/witnesses`:

```json
{
  "contract_version": "bio.in-vitro.paired-observation.v0",
  "study": {
    "study_id": "retrospective-assay-1",
    "completed_at": "2026-09-01",
    "biological_model": "cell-line-X",
    "assay": "endpoint-assay",
    "endpoint": "signal",
    "unit": "arbitrary-units",
    "timepoint": "24h"
  },
  "analysis": {"direction": "increase", "minimum_mean_delta": "0.5"},
  "pairs": [
    {"pair_id": "pair-1", "batch_id": "batch-1", "control": "1.0", "treated": "1.8"},
    {"pair_id": "pair-2", "batch_id": "batch-1", "control": "2.0", "treated": "2.6"},
    {"pair_id": "pair-3", "batch_id": "batch-2", "control": "3.0", "treated": "3.4"}
  ]
}
```

Only the exact field set is accepted. Measurements and threshold are bounded
decimal strings; 3–64 pair IDs must be unique. The result contains each pair's
difference, an exact rational mean and sample variance, observed range, sign counts, declared
batch count, and limitations. A well-formed witness whose mean misses the
threshold is retained with a `FAILED` attempt and `BLOCKED` evaluation, not
discarded. A malformed or future-dated submission is rejected before ledger
writes. The server constructs the Claim statement; callers cannot submit a
free-form clinical or safety assertion through this endpoint.

This is **not** a preregistered statistical test: the threshold is frozen when
the data are submitted, and v0 cannot show it was selected before the outcome
was known. It reports sample dispersion but no confidence interval or p-value.
Distinct `pair_id`/`batch_id` strings do not establish biological independence.
The original instrument export, chain of custody, assay QC, exclusions,
measurement uncertainty, and completion date are not independently
authenticated. `external_reality` and `independent_validation` therefore remain
`undetermined` even when the exact arithmetic qualifies.

The submit endpoint does **not** create a `CurrentUseBinding`, knowledge
admission, `AuthorizationGrant`, schedule, or wet-lab tool call. To qualify a
broader biological or statistical claim, create a separate, domain-owned
contract with authenticated source records, frozen analysis/QC rules, explicit
uncertainty assumptions, and a different evidence threshold. Do not reinterpret
historical v0 receipts as if they passed that contract.
The v0 endpoint does not monitor later distribution drift. A later change in
the data-generating conditions would call for a new evaluation; it would not
retroactively prove the original observation false.

## Record provenance contrast test

These are separate questions, not one trust score:

| Layer | This slice establishes |
| --- | --- |
| L0 arithmetic | Exact calculation over submitted paired values (`observed_pair_delta.v0`). |
| L1 record origin | Exact binding to two enrolled signers' record bytes (`provenance.v0` test only). |
| L2 experimental validity | Not checked: QC, exclusions, complete audit trail, sample identity and replication. |
| L3 statistical inference | Not checked: population uncertainty, bias, multiplicity and generalization. |
| L4 biological/clinical claim | Not checked: mechanism, efficacy, safety or clinical relevance. |

`bio.observation.provenance.v0` is a separate, intentionally narrower
Qualification Profile used by `tests/test_bio_observation_provenance.py`. Its
claim is only that the *same submitted measurements* are exactly bound to an
instrument-export record and a custodian sample-mapping record signed by two different,
host-enrolled Ed25519 keys. The checker reopens the frozen Artifacts, verifies
their content hashes and signatures, compares every measurement, pair ID and
study ID, checks the sample mapping, and requires a server-origin replay
attempt. The keyring fingerprint and checker source-file digest are in the
Profile snapshot; a changed trust root or checker cannot silently inherit an
old current-use qualification.

The positive and manual-only fixtures have identical numeric pairs. Both
qualify under the arithmetic Profile. Only the enrolled-record fixture can
qualify for this **record-binding** Profile; manual JSON remains `UNRESOLVED`
even though it has an arithmetic Receipt. Neither result proves the experiment
occurred, the signer or instrument was well controlled, the sample labels were
correct, the audit trail is complete, QC passed, or the readings are independent
biological replicates. A signed mapping may honestly show repeated readings of
one sample. `external_reality` remains `undetermined` and neither path creates
knowledge admission or execution authorization.

This is currently an internal qualification-contract test, not a public lab
record-ingestion API. The default server has **no enrolled provenance keys**
and therefore fails closed. The test injects ephemeral host-owned public keys;
submitted evidence cannot register its own trust root. Production enrollment,
key rotation/revocation, instrument attestation, audit-trail retention, QC and
biological replication are deliberately outside v0. In particular, a record
signature must never be renamed into an "experiment qualified" claim.

### Frozen L1 boundary: repetition is not independence

The adversarial regressions intentionally leave `sample_id` non-unique in
`bio.observation.provenance.v0`:

- Three correctly signed measurement pairs may all map to `Sample-001`. L1
  record binding remains `ADMITTED`; the separate candidate assertion of
  three independent *biological* replicates is `NOT_APPLICABLE` to this Profile
  and receives no QualificationReceipt.
- The same signed instrument and custodian bytes may be attached to two
  Research Cases. Each can independently obtain a narrow L1 record-binding
  Receipt, but the bytes have the same content hashes. A candidate assertion
  of two independent experiments likewise receives no qualification.

This freezes the L1 meaning: signed records and exact cross-record matching,
not biological-replicate counts or experiment-lineage independence. It would
be incorrect to "fix" L1 by requiring unique sample IDs or rejecting all
cross-Case reuse. A future L2 contract needs source-run/experiment identity,
sample lineage, technical-versus-biological replicate rules, QC and an
explicit independence criterion; it must use its own versioned Profile.

The FDA's [AI credibility draft guidance](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/considerations-use-artificial-intelligence-support-regulatory-decision-making-drug-and-biological)
emphasizes a particular context of use. That document is draft, and this
research prototype does not establish regulatory compliance or model
credibility for a submission.
