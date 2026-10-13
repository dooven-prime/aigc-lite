# rime-consumer-finite-replay-r2

This is an artifact-specific finite replay handoff, **not** an aigc-lite
software-version release or an independent mathematical validation. The
accepted ZIP is published byte-for-byte; its checker, examples, expectations,
and algorithm are not modified by this record.

## Immutable identities

| Item | Frozen identity |
| --- | --- |
| Delivery ZIP (`replay_packages/rime-consumer-r2.zip`, also a release asset) | SHA-256 `11f6c61f430e5adedbfc488b02765d146ca652afa277e4d952988e10702c84c6` |
| Package `manifest.json` | SHA-256 `fd75add881becc2e37ceb79ed5999200132d90ed958f2ddb66df637b30ae46f6` |
| Normative specification | `rime-lite` commit `9d4c735d7a5fc14334c33c94160931ff53837c24`, `contracts/consumer-replay/v1/` |
| Specification subtree | Git tree `0ebf591d1ebe54792b99ce6ad3810e194c55f6cf` |
| Checker source | `checker/` inside the pinned ZIP; readable mirror at `replay_packages/rime-consumer-v1-r2/checker/` in this repository's `rime-consumer-finite-replay-r2` tag; individual byte hashes are in the ZIP manifest |
| Host pre-execution verifier | `scripts/verify_rime_replay_package.py` at the same tag, SHA-256 `45bb97e46a92063f13511bff47f88fbf7d452a080c20a9037f7829c89948dfe2` |
| Cross-host report | SHA-256 `fc73737da1affc0be22c6ea405561d9977ddf564c22ca74b656216d32d3afc51` (canonical JSON recorded by the receipt) |
| Ubuntu execution receipt | `ubuntu-replay-receipt.json`, SHA-256 `caa0477be41718dc61b6eb5741a6c401f94c4fa817fad8907d29a48d1d787be3` |

The earlier aigc-lite API Case pins `rime-lite` commit `8dc2e615c5b011efa498bbd6fc4dec613c637030`.
Both commits have the same `contracts/consumer-replay/v1` Git tree above, but
their commit identities and historical attempts remain distinct. Neither
receipt is retroactively rebound to the other commit.

The **ZIP is the byte authority**, not the readable Git mirror. In particular,
Git's text line-ending normalization can alter the mirror's `manifest.json`
on checkout; never substitute its digest for the original ZIP member's digest.
The mirror is for source review. Execute only the out-of-band pinned ZIP after
host-side pre-execution validation.

## Observed result

The Ubuntu host reported Linux `7.0.0-31-generic`, CPython `3.12.3`, and
the same pinned checker. The receipt reports 26 package files, three positive
witnesses passing, seven negative controls matching their expected verdicts
and failure reasons, and `all_expected_match=true`:

| Witness | Expected and observed verdict | Distinguishing check |
| --- | --- | --- |
| `examples/witness.json` | `PASS` | unrelated `Carry(d)` fusion |
| `examples/witness_with_reports.json` | `PASS` | complete candidate reports |
| `examples/absorb_witness.json` | `PASS` | selected absorption |
| `negative_controls/bad_f_not_in_log.json` | `FAIL` | selected block never registered |
| `negative_controls/absorb_p.json` | `FAIL` | `Absorb(p)` is disabled |
| `negative_controls/carry_guard_wrong.json` | `FAIL` | `Carry` guard fails |
| `negative_controls/wrong_candidate_report.json` | `FAIL` | origin report mismatch |
| `negative_controls/q_too_large.json` | `INVALID_REQUEST` | size exceeds `[2,16]` |
| `negative_controls/invalid_letter.json` | `INVALID_REQUEST` | invalid prefix letter |
| `negative_controls/bad_command_kind.json` | `INVALID_REQUEST` | invalid command kind |

The full per-prefix states, actual reasons, and output summary are retained in
the separate Ubuntu receipt asset; the receipt is runtime evidence and is
**not committed to the source tree**. The ZIP and receipt are separate assets.
The receipt has a content digest, not a cryptographic signature or an
authenticated external verifier identity.

## Reproduction boundary

Before decompression, obtain the ZIP and manifest digests **outside the ZIP**
from this release record or another trusted configuration. A value read from
the package being checked is not an expected digest. From a separately
obtained, pinned source checkout, use the host-side ZIP validator:

```text
python scripts/verify_rime_replay_zip.py \
  --zip rime-consumer-r2.zip \
  --expected-zip-sha256 11f6c61f430e5adedbfc488b02765d146ca652afa277e4d952988e10702c84c6 \
  --expected-manifest-sha256 fd75add881becc2e37ceb79ed5999200132d90ed958f2ddb66df637b30ae46f6 \
  --destination /new/empty/path
```

This validates the ZIP digest, manifest digest, member names and types,
duplicates, size bounds, and exact package file inventory before extracting
to a path that did not previously exist. It never executes a ZIP member. The
ZIP contains a convenience copy of the host verifier outside its manifest;
**do not treat or execute that copy as independently trusted**. Use the
separately obtained, pinned checkout's `scripts/verify_rime_replay_package.py`
to check the extracted package and replay, writing a new receipt outside it.

The accurate claim is **externally reproducible finite witness replay using
the same pinned checker**. It is not an independently implemented semantic
oracle, an independent organization audit, an All-N consumer theorem, a
formalized checker-correctness proof, a complete OS sandbox, a
QualificationReceipt, current-use admission, or execution authorization.
No model or checker output can silently cross those boundaries.
