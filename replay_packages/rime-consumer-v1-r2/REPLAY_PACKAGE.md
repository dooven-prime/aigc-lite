# Consumer replay checker/package revision r2

This is a new executable package revision for the unchanged RIME
`contracts/consumer-replay/v1` specification. The normative Markdown, schema,
and original `examples/witness.json` are byte copies from the RIME source at
commit `9d4c735d7a5fc14334c33c94160931ff53837c24`. The prior package at
`/home/dooven/文档/replay-packages/rime-consumer-v1/` and its results are
historical; this revision does not replace them or retroactively validate them.

The earlier package accepted a negative control for the wrong reason, had a
broken ordered-port comparison, did not compare all report fields or command
guards, and could execute package code before any host-side byte check. This
revision compares full per-prefix state, exact ordered origin and absorption
records, candidate reports, and expected negative-control reasons.

`manifest.json` binds every package file except itself. Its SHA-256 must be
obtained independently and supplied to the host-owned
`scripts/verify_rime_replay_package.py` before package execution. The host
script writes a receipt **outside** this immutable package. Directly running
`checker/run.py` is diagnostic and does not establish pre-execution trust.

This remains a same-checker, cross-host finite replay, not an independent
implementation, proof of checker correctness, Git tree/signature
authentication, general consumer theorem, QualificationReceipt, or knowledge
admission. The Python subprocess is not a complete OS sandbox or network
enforcer; run only reviewed, pinned code in an isolated environment.
