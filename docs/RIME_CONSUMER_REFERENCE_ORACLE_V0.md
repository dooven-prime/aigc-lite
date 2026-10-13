# RIME finite origin oracle v0

This is a deliberately narrow, separately written implementation for the
finite event-anchored consumer property. It does **not** import the r2 log,
forest, UFE, transition, or report code. Its source is
`reference_oracles/rime_consumer_origin_v0.py`; the comparison harness may
load both programs to compare their outputs.

The checked observable is the fixed registered event's origin, the complete
chronological absorption list involving that event's carrier, the current
carrier position/block, and the full partition needed to interpret subsequent
commands. The oracle derives all of these by a direct finite partition
push-forward from the singleton seed. It does not certify the frozen
specification, the correctness of either implementation, or an All-N theorem.

The test corpus is frozen as `reference_oracles/finite_corpus_v0.json`,
SHA-256 `2e94f60860496764612cf4a21356649dbdeef54ac443da009052f50a65830595`.
It has four additional n=3/n=4 finite inputs, including selected absorption
and a forbidden carry. The comparison also runs the accepted r2 package's
three positives and seven negative controls. The observed v0 comparison is
14/14 verdict and per-prefix field agreements; its full deterministic report
had SHA-256 `4f5c5412e8e232d0106fdf93a6ef3e948da7c16846e026a599c523234ab8bfcf`.
On disagreement, the comparator reports the first differing prefix and the
raw states from both paths. A regression test injects a false r2 carrier
position and checks that this failure record is produced.

Run from the pinned aigc-lite source checkout:

```text
python scripts/compare_rime_reference_oracle.py --output /external/path/comparison.json
python -m pytest tests/test_rime_reference_oracle.py -q
```

The comparison harness executes checked-in Python code. The separate r2 ZIP
delivery must first pass the out-of-band ZIP and package pre-execution checks
described in `docs/releases/rime-consumer-finite-replay-r2.md`; this harness
does not itself establish a new software-isolation boundary. The two source
paths were authored and run within the same project/trust domain. Thus the
result is *finite heterogeneous implementation agreement*, not independent
organization validation or mathematical qualification. It does not change any
ClaimRevision, QualificationReceipt, CurrentUseBinding, or AuthorizationGrant.
