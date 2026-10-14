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

Neither generator registers itself with the application, HTTP/MCP surface,
Qualification Registry, or authority ledgers. The core wheel must contain no
`aigc_lite_casepacks` code; the optional wheel must contain the certificate
checkers without embedding `app` or adding entry points.
