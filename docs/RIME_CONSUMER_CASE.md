# RIME Event-Anchored Consumer — finite replay Case

This is a narrow, data-only research Case. The external program or Agent supplies
a finite witness; the server reconstructs the seed state and checks every prefix
with three routes: event log, fusion forest, and placed forward-UFE. The checker
does not execute uploaded code. A passing `VerificationAttempt` says only that
**this witness** agrees under the pinned checker and source contract. It does
not issue a `QualificationReceipt`, create a `CurrentUseBinding`, prove All-N
adequacy, or close old B0.

## Frozen mapping and source closure

The upstream package is [`rime.consumer-replay.v1`](https://github.com/dooven-prime/rime-lite/tree/8dc2e615c5b011efa498bbd6fc4dec613c637030/contracts/consumer-replay/v1),
at commit `8dc2e615c5b011efa498bbd6fc4dec613c637030` and root
`contracts/consumer-replay/v1`. The *aigc-lite* request/replay envelope has
the distinct revision `aigc-lite.rime.consumer-replay.v2`. Its source identity
pins `manifest.json` separately to SHA-256
`0db53c0235a2add4189df1e8fcb5aa63da1443bc7451ab865313e842583b0eca`.
The manifest excludes its own bytes; it lists every other file in the
published package:

| File under `contracts/consumer-replay/v1` | Role | SHA-256 |
| --- | --- | --- |
| `.gitattributes` | byte materialization | `6966d0bc0169072d3f2c6341ebfd973e3a9c98c98c3941ca8459f077850f934d` |
| `README.md` | scope and Case decisions | `76525a11d3cd814c84d319af683c715065b7e8056fc5bb2a87f6f5cb1938908c` |
| `candidate-reports.schema.json` | optional report structure | `90b8c9ca530e59e4321a7ebc11dba0efed7b36f4faec3bbeff28a0339f4a8076` |
| `CONSUMER_SPEC.md` | independent log oracle | `184a507d1ad54004fec0b195db952ce1a18d949f6264f6c2f07b2a56b77bf0e1` |
| `FOREST_MODEL.md` | placed forest representation | `7ee153f68238a436d973f254bb16857ba78e6a853a85e127cb5eee9e66535c48` |
| `UFE_ADAPTER.md` | placed forward-UFE representation | `bbd47dbaca39c2828f3e9a92fd536adb26730c63be3bf92c9a6def4c374fd35c` |
| `examples/witness.json` | finite data witness | `af7ecdc1c4d4dd38a6236f58684ec8fe7d3e7e3d00baa2a7d381b98859b3115d` |

The `GET /api/research/rime-consumer/contract` response exposes that entire
inventory, the manifest digest, and the current *aigc-lite* checker source-file
SHA-256. Each v2 witness must echo the exact `contract` identity returned by
the endpoint; a changed source or checker identity is rejected before
persistence. We checked the published file bytes against the manifest at the
fixed commit while updating this binding. The running server does **not**
re-fetch them or verify a release signature on each submission. A digest
binds bytes, not the correctness of the specification or checker.

`environment` specifies finite `Q`, successor permutation `p`, rank-`n-1`
binary-kernel map `d`, ordered kernel ports, collision image, atoms `omega`,
injective `iota`, and UFE atom enumeration. `prefix` is a `p`/`d` word from
the singleton seed. `selected_block` must match the fresh block of an actual
fusion event in that prefix; an uploaded registration flag or state snapshot
is never trusted. `commands` contain only `Carry`/`Absorb` and `p`/`d`.
Optional `candidate_reports` must contain one complete report after the seed
prefix and after every command. The pinned JSON schema requires a complete
*placed* partition (`[{"q": 0, "block": [0]}, ...]`) and a placed carrier
(`{"q": 0, "block": [0]}`), not just unlocated blocks. Structural report
errors are rejected as `422`; a well-shaped but incorrect report produces a
failed attempt. The checker compares the **whole partition**,
origin event, chronological absorption list, and current carrier. The UFE route
retains ordered union operands and placements; it reconstructs event reports
from union history rather than copying a forest or log report.
The result also records packet placement at each prefix and checks that all
three routes agree on it, not just on the unordered partition.

The contract imposes operational bounds (`|Q| <= 16`, prefix and continuation
length each `<= 128`, JSON size `<= 200000` bytes). These are service limits,
not a mathematical bounded-length theorem claim.

## Run a positive witness

Authenticate as a workspace admin, then obtain the contract:

```http
GET /api/research/rime-consumer/contract
```

From the `aigc-lite` checkout, generate a matching JSON witness with
`python scripts/rime_consumer_example.py`. The upstream package also includes
its own finite `examples/witness.json`; the local generator adds the required
*aigc-lite* contract/checker identity. Submit the generated object to:

```http
POST /api/research/rime-consumer/witnesses
Content-Type: application/json
Authorization: Bearer <workspace session>
```

The example registers `F={0,6}` after `dpp`. Its subsequent `Carry(d)`
triggers an unrelated fusion: the complete partition and UFE union history
change, while `F`'s absorption list stays empty. The response contains the
Case, ClaimRevision, Run, witness Artifact, result Artifact, and
VerificationAttempt IDs. The Run has a Step for each seed prefix and observed
continuation prefix. The result Artifact contains the first failure position
or the per-prefix reports and explicit limitations.

Registration of a nonexistent block, `Absorb(p)`, incorrect candidate
reports, or a mixed source/checker identity cannot be mistaken for a passing
finite replay. Swapping the ordered kernel ports changes event addresses; a
candidate report that still describes the old address fails semantic comparison.
Invalid contract/environment/report shapes return `422`; well-formed but false domain claims produce a `failed`
VerificationAttempt. Both remain candidate evidence. A checker execution
that correctly detects a bad witness may have a `succeeded` Run with a
`failed` verification outcome; the axes are intentionally separate.

To re-submit the **same mathematical witness** under v2, optionally set
`prior_case_id` to a previous Case in the same workspace. The server reads the
prior witness Artifact, verifies its recorded content identity, compares
`environment`, `prefix`, `selected_block`, and `commands`, and records a
read-only link to the prior Case, Claim, and attempt. The new submission gets
its own witness Artifact, Run, ClaimRevision, and VerificationAttempt. The
contract/checker identity and optional candidate report are not part of the
mathematical-witness comparison, and neither result inherits the other's
outcome or authority.

In particular, the older Case's claimed `c211517...` source paths under
`papers/unnumbered/` were **not materialized at that commit**. Its finite
replay result remains a historical result, with the source closure marked
`unmaterialized_at_declared_commit` in a v2 link. Publishing the new package
today does not retroactively put files in the old commit or repair that old
source claim. Only a new v2 replay can reference the retrievable source above.

This route does not authenticate an external verifier organization, establish
cross-trust-domain independence, or prove that its new executable UFE decoder
is itself formally verified. Promoting or admitting knowledge requires a
separate, explicitly authorized policy path; nothing here invokes it.
