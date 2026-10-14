# Candidate-only MDE Disambiguation Certificate v0

This research artifact is a deterministic explanation of a **conditional**
decision by the optional `casepacks/mde-v0` checkers. It is not a
QualificationReceipt, knowledge admission, authorization, or a new core API.
The first two certificate generators are deliberately asymmetric: a Bio
counterexample to parent-source distinctness and a Citation positive result
for distinct primary-evidence roots. They do not share a real-world source
authenticity standard.

## Mathematical reading

For the Case-declared possible worlds `W`, visible observation `O`, added
source declaration `D`, and frozen Boolean target `T`, define

```text
W[o,d] = { w in W : O(w) = o and D(w) = d }
```

Disambiguation requires both `W[o,d]` to be nonempty and the set of `T(w)`
over it to have exactly one value. It does **not** require all compatible
worlds to be the same. An inconsistent declaration that leaves no compatible
world is not a successful proof by vacuity.

The v0 implementations do not enumerate all physical experiments or actual
publication histories. They check finite, domain-specific symbolic
constraints: consistent sample-to-parent assignments for Bio, or an acyclic
and closed signed primary-root graph for Citation. A certificate must expose
that finite compatibility witness and its decision rule. It cannot turn this
conditional model into a claim of exhaustive real-world coverage.
The machine-readable versioned contract is packaged as
`certificate_contract_v0.json`; its raw-byte SHA-256 is part of each
certificate body before the certificate identity is calculated.

## Certificate identity and replay

The generators take raw evidence and host-enrolled verification keys, then
**rerun** the existing MDE diagnostic. They never accept a submitted status,
report, or certificate hash as the verdict. The versioned JSON body is encoded
with sorted keys, UTF-8, compact separators, and no non-finite numbers. Its
SHA-256 is domain-separated by
`aigc-lite.mde.disambiguation-certificate.v0` and excludes the digest field
itself. No timestamp, random ID, or local file path enters the body. Repeating
the same inputs and ordered ablations yields the same JSON and digest.

The certificate records the frozen Case and target, hashes of the submitted
inputs and checker/dependency files, the host trust-configuration fingerprint,
the baseline diagnostic and its finite decision witness, each *executed*
ablation's input hash and new diagnostic, and the assumptions left unverified. The JSON
hash only checks the certificate's identity. Independent replay still needs
the exact raw records/bundle, source/checker bytes, historical host key
enrollment and revocation context, and any signed reduced records. The
certificate alone does not supply those bytes or attest the signer.
`encode_certificate` and `parse_certificate` reject noncanonical bytes,
duplicate JSON keys, and a mismatched digest; they do not replace domain
replay from source inputs.

### Frozen synthetic cross-host replay input

The research branch freezes two **synthetic**, public-key-only input files in
`casepacks/mde-v0/fixtures/certificate-replay-v0/`. Their companion
`replay-lock.json` records the raw input SHA-256, expected canonical certificate
JSON SHA-256, domain-separated certificate-field digest, checker source digest,
and the `v0.7.0` core wheel identity. The lock is a comparison target, **not a
trust anchor by itself**: an external operator must pin the exact research
source commit, lock bytes, core wheel, and casepack wheel through a channel
independent of the candidate input. No private signing key is in the fixtures.
The released MDE casepack `0.1.0` does not contain certificate code; this work
is the unpublished `0.2.0.dev0` research candidate.

From a clean environment outside the source checkout, install the pinned
`aigc-lite` `0.7.0` wheel and a casepack wheel built from the exact research
commit. Verify their SHA-256 before installation. For each input, invoke the
module with **both expected digests supplied outside the input file**:

```text
python -m aigc_lite_casepacks.mde_v0.certificate_replay INPUT_JSON \
  --expected-input-sha256 INPUT_SHA256 \
  --expected-certificate-json-sha256 CERTIFICATE_JSON_SHA256
```

On success stdout is exactly the regenerated canonical certificate JSON,
without a trailing newline. A wrong input hash, mismatched regenerated output,
malformed JSON, or failed domain baseline exits nonzero with empty stdout.
Compare stdout bytes or their SHA-256 across hosts. Do not substitute the
`certificate_sha256` field: it uses the domain-separated body rule and is
intentionally different from the raw JSON file hash. Same checker, inputs,
keys and output on another host establish **cross-host reproducibility**, not
an independently implemented verifier or organizational independence.
The CLI has bounded input structure and ablation count, but it is not an OS
sandbox; execute untrusted submissions under a separate wall-clock and memory
budget. The frozen cross-host controls use only synthetic public keys.

The frozen fixture expectations are deliberately inspectable at each unit:

| Synthetic Case | Signed one-unit reductions | Relative outcome | Unrun/omission controls |
| --- | --- | --- | --- |
| Bio parent-source refutation | Remove the mapping for Sample-1, Sample-2 or Sample-3, each with a lawful new signature | Sample-1 and Sample-2 `NECESSARY_IN_THIS_BUNDLE`; Sample-3 `REDUNDANT_IN_THIS_BUNDLE` | None of the three is inferred from signature tampering |
| Citation positive conditional result | Withdraw alias completeness; withdraw each work-coverage assertion; remove each of two edges; remove one unused primary node, each lawfully re-signed | Alias, both coverage units and both edges `NECESSARY_IN_THIS_BUNDLE`; unused node `REDUNDANT_IN_THIS_BUNDLE` | Omitting either identity record or the whole lineage record remains `UNTESTED`, since that omission supplies no authenticated reduced issuer statement |

Each certificate includes the exact reduced-input digest, authenticated
diagnostic and outcome for every executed unit. The issuer-reported origin of
a reduced statement is **not** independently authenticated merely because its
bytes carry a valid signature. These synthetic reductions were generated with
test signing keys; they do not assert that a real laboratory or publisher ever
issued such counterfactual statements.

## One-step information ablations

Every ablation is judged against the **same** frozen target, visible
observation, and host trust configuration. Removing an entire signed record
can be simulated by omitting that record. This shows a schema or authentication
dependency, but cannot count as `NECESSARY_IN_THIS_BUNDLE` when the omission
leaves no authenticated reduced statement; it is `UNTESTED` as an information
ablation. Removing information *inside* a signed
record requires a separately validly signed reduced statement; tampering with
the existing signature tests authentication failure, not information need.
The checker does not hold a signing key or re-sign on the issuer's behalf.

- `NECESSARY_IN_THIS_BUNDLE`: the lawful reduced evidence remains authenticated
  and consistent, but the target becomes `UNRESOLVED`.
- `REDUNDANT_IN_THIS_BUNDLE`: the lawful reduction keeps the **same** scoped
  target truth, not merely another decided result.
- `UNTESTED`: no lawful reduced evidence was supplied or the unit was not
  examined.
- `INVALID_ABLATION`: the change breaks the observation/target/trust binding,
  signature, field-delta contract, or finite model consistency. It is never
  counted as evidence necessity.

A signed reduction proves only that the enrolled key signed its bytes. A
caller-supplied label such as `synthetic_test_resigning` is descriptive,
**not** independent authentication that a real issuer historically authorized
the reduced declaration. The test suite controls synthetic private keys; a
production checker must not invent or silently re-sign source statements.
One-step irreducibility for declared units is not global minimality across all
possible evidence designs.

## Domain-specific non-claims

- Bio: a signed common canonical parent can refute the narrow
  parent-source-distinctness target without complete coverage. The issuer's
  parent mapping, split/alias resolution, sample identity, and physical chain
  of custody remain claims, not independently observed facts. This does not
  decide statistical or broader biological independence.
- Citation: a positive result needs two bound work identities, complete
  per-work source paths, disjoint primary roots, and a complete alias index.
  The signed metadata/source-record digests are not fetched here. Canonical
  work identity, graph completeness, publication history, and actual data
  provenance remain issuer claims. Different keys do not establish different
  organizations or independent research.

In both Cases, a host-supplied public-key configuration is frozen by hash for
replay, but the key-holder's identity, historical enrollment/revocation state,
and trust-domain independence are not established by that hash. The Bio
certificate does not independently observe sample custody, physical parentage
or QC. The Citation certificate does not fetch underlying metadata/source
records or independently reconstruct publication and data histories. No
finite-model consistency check enumerates all real-world possible worlds, and
one-step deletion is not a globally minimal evidence proof.

Neither generator registers itself with the application, HTTP/MCP surface,
Qualification Registry, or authority ledgers. The core wheel must contain no
`aigc_lite_casepacks` code; the optional wheel must contain the certificate
checkers without embedding `app` or adding entry points.
