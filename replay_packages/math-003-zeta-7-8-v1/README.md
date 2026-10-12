# Fixed Math 003 zeta `7/8` replay handoff

This handoff describes **one frozen target**, not all family 003 results. Read
[`manifest.json`](manifest.json) and
[`docs/MATH_PROJECT_003_PROPOSITION_ALIGNMENT.md`](../../docs/MATH_PROJECT_003_PROPOSITION_ALIGNMENT.md)
before running it. There is no expected `PASS` asserted by this package: the
outcome must be observed on each execution. The RIME `11/12` RTheta paper
review is a separate candidate and is not an input to this replay.

**Current limitation (2026-10-12):** no successful full-project replay or
external re-execution has yet been registered. The first local challenge build
stopped when ProofWidgets tried to write a `.hash` sidecar in a read-only
dependency. The narrow file-only output mount below resolved that fault: a
subsequent cold run built the frozen challenge and reached the OAI solution
dependencies, but exhausted its 7,200-second wall-clock budget before
`Nonvanishing.lean` finished. Statement comparison and kernel checking remain
undetermined; the local gate issued no qualification receipt. Its build outputs
were sealed only as diagnostic cache data. Never use cached artifacts as a
substitute for replay.

An external operator should use their own Linux host, clean directory, own
workspace ledger, and their own source retrieval. Copying our SQLite database,
build cache, precompiled `.olean`, run ID, or signed-looking JSON is **not** an
independent re-execution. In particular, a valid cache hash must not replace
Comparator's challenge-first build, solution build, statement comparison, and
kernel replay.

## Preparation

Obtain the `aigc-lite` source version containing this handoff and install its
Python dependencies in an isolated environment. Provide a fresh work area
outside the checkout and executable paths for Lean/Lake 4.34.1, Comparator at
commit `d03acab154d269c06e60e4de7e4cc85deebff94b` (with its pinned
`lean4export` at `076e8e57707e813375e8f9da8bf989799ace9680`), and `landrun`
at `811cfff51ceaf3d9843708aa6d22e9b84ccac8b4`. Comparator's own
`lean-toolchain` names 4.34.0: using its source with Lean 4.34.1 is a recorded
local compatibility choice, **not** an upstream binary release. Record the
actual source revisions and SHA-256 of every executable you used. Inspect the
deployer-owned Bubblewrap policy before treating it as an isolation boundary.
The handoff manifest also fixes the official Lean 4.34.1 Linux release-archive
URL and SHA-256; retrieve and hash those bytes independently before unpacking.
A matching archive hash establishes byte identity with this handoff's pin, not
GitHub release-signature verification or the semantic correctness of Lean.
For example, in a new private `TOOLS` directory, fetch the archive URL from
the manifest, verify its SHA-256 **before** extraction, and unpack it with
`tar --zstd -xf` (or a trusted equivalent). Fetch Comparator and Landrun from the
URLs below, detach at the manifest commits, and build them using that Lean
toolchain and an installed Go toolchain:

```sh
git clone https://github.com/leanprover/comparator.git "$TOOLS/comparator"
git -C "$TOOLS/comparator" checkout --detach d03acab154d269c06e60e4de7e4cc85deebff94b
git clone https://github.com/Zouuup/landrun.git "$TOOLS/landrun"
git -C "$TOOLS/landrun" checkout --detach 811cfff51ceaf3d9843708aa6d22e9b84ccac8b4
go -C "$TOOLS/landrun" build -o "$TOOLS/bin/landrun" ./cmd/landrun
```

With the verified Lean `bin` first on `PATH`, build Comparator's default
target and its manifest-pinned `lean4export`, then check both source revisions
and record all resulting binary hashes. Do not run `lake update`: that could
advance `lean4export` beyond the frozen manifest revision. A compiler-version
or build-environment difference may produce different executable bytes, so a
cross-host binary mismatch needs investigation, not automatic rejection or
automatic acceptance.
Run the CLI from that exact source revision (`python -m app.cli` in the checkout,
or a clean installation of it); an older installed `aigc-lite` entry point
may silently lack the handoff options. Record the checkout commit and verify
that `math-case-003 replay --help` lists `--patched-closure-root` and
`--build-work-root` before starting a costly build.

With `SRC` a **new** checkout directory, `STORE` a **new** closure store, and
`BUILD` a **new** build-work directory:

```sh
python scripts/prepare_math_case_003_source.py "$SRC"
python scripts/prepare_math_case_003_dependencies.py "$SRC"
python scripts/prepare_math_case_003_patched_closure.py prepare "$SRC" "$STORE"
python scripts/math_case_003_build_cache.py prepare --project "$SRC" --work "$BUILD"
```

The first two commands fetch the exact public commit and all 42 manifest-pinned
dependencies; the third pre-applies the 23 pinned patches to a separately
content-addressed, read-only tree. It prints `CLOSURE_ROOT`. Verify it again
before and after execution with
`python scripts/prepare_math_case_003_patched_closure.py verify "$SRC" "$CLOSURE_ROOT"`.
The package manifest duplicates the 31 raw-file hashes and tests require exact
agreement with the adapter allowlist. Neither that agreement nor Git commit
name alone independently authenticates the remote Git tree.

`BUILD` also contains a pristine `sidecars/proofwidgets/widget` facade with
empty mount placeholders and an empty `package-lock.json.hash`. The sandbox
binds the facade **read-only** over the package's widget directory, rebinds
every existing widget input from the audited closure as read-only, and binds
only the `.hash` file writable. Lake can write that sidecar without changing
the pinned lockfile or source or creating other widget files. The sandbox audit
must verify all three properties;
the post-run build-output manifest hashes the sidecar tree, but it is **not**
qualification evidence. A new attempt needs a new empty `BUILD` directory.

## Sandbox audit, snapshot, replay

Define `LAKE`, `LEAN`, `COMPARATOR_ROOT`, `COMPARATOR`, `LANDRUN`, and
`LEAN4EXPORT` as absolute executable/root paths on this host. Run the fixed
policy probe first:

```sh
python scripts/audit_math_case_003_sandbox.py \
  --project-root "$SRC" --lake "$LAKE" --lean "$LEAN" \
  --comparator-root "$COMPARATOR_ROOT" --comparator "$COMPARATOR" \
  --landrun "$LANDRUN" --lean4export "$LEAN4EXPORT" \
  --patched-closure-root "$CLOSURE_ROOT" --build-work-root "$BUILD"
```

All probe checks must be true. They are a boundary smoke, not proof of host
kernel isolation. Set `AIGC_LITE_DATA_DIR` to a fresh, private ledger directory
outside the repository. Then:

```sh
python -m app.cli math-case-003 snapshot --workspace independent-math-003
```

The command prints `claim_revision_id` and `source_snapshot_artifact_id`.
Copy those exact values into `CLAIM_ID` and `SNAPSHOT_ID` before continuing:

```sh
python -m app.cli math-case-003 replay --workspace independent-math-003 \
  --claim-id "$CLAIM_ID" --snapshot-id "$SNAPSHOT_ID" \
  --project-root "$SRC" --lake "$LAKE" --lean "$LEAN" \
  --comparator-root "$COMPARATOR_ROOT" --comparator "$COMPARATOR" \
  --landrun "$LANDRUN" --lean4export "$LEAN4EXPORT" \
  --sandbox-profile bwrap-v1 --patched-closure-root "$CLOSURE_ROOT" \
  --build-work-root "$BUILD" --timeout-seconds 7200
python -m app.cli math-case-003 evaluate --workspace independent-math-003 \
  --claim-id "$CLAIM_ID"
```

The replay must use a fresh build directory; repeating it requires another
empty directory. The CLI refuses a scratch dependency mode and a patched
closure at the same time. The Python process stores a Run, Step, receipt
Artifact, and VerificationAttempt; the receipt binds source, tool and sandbox
hashes, outputs, elapsed time, and diagnostic build milestones. Keep its
`run_id`, `verification_attempt_id`, and receipt Artifact in the private
ledger. Export the byte-checked receipt JSON with
`python -m app.cli math-case-003 receipt --workspace independent-math-003 --receipt-id ID`,
using the returned `verification_receipt_artifact_id` as `ID`. Record
`uname -a`, available memory/disk before and after, wall time,
CPU time, peak RSS, and any termination reason separately. Runtime logs and
databases must not be committed to this repository.

After the process terminates, seal and recheck **build outputs only** in a
private content-addressed store. Use `POLICY_SHA` from the sandbox audit and
an unused `BUILD_STORE` directory; `capture` moves `BUILD` into that store and
prints `cache_entry`:

```sh
python scripts/math_case_003_build_cache.py capture \
  --project "$SRC" --work "$BUILD" --store "$BUILD_STORE" \
  --closure "$CLOSURE_ROOT" --lake "$LAKE" --lean "$LEAN" \
  --comparator "$COMPARATOR" --sandbox-policy-sha256 "$POLICY_SHA"
python scripts/math_case_003_build_cache.py verify \
  --project "$SRC" --entry "$CACHE_ENTRY" \
  --closure "$CLOSURE_ROOT" --lake "$LAKE" --lean "$LEAN" \
  --comparator "$COMPARATOR" --sandbox-policy-sha256 "$POLICY_SHA"
```

The cache manifest includes the widget sidecar bytes. Its address demonstrates
output integrity only; it must never stand in for a fresh Comparator replay or
for an externally authenticated verifier.

## Interpretation and independent delivery

Comparator exit `0` is needed for its **formal** statement and kernel checks.
A timeout or nonzero exit is not a counterexample to the paper theorem, and
the diagnostic milestones are not stage-success certificates. Even a
successful Comparator result does not establish that the printed paper theorem
and Mathlib's total `riemannZeta`, particularly at `s=1`, mean exactly the
same thing. A same-claim/snapshot, non-model `EXPERT_REVIEW` is a different
criterion, held by the local qualification policy. Neither it nor a passing
replay automatically creates a current-use knowledge binding.

For a reviewable external report, publish the handoff-manifest hash, exact
source and closure hashes, executable hashes and source revisions, sandbox
policy hash, host/kernel details, wall/CPU/RSS measurements, the replay receipt
and its content hash, and the observed gate verdict. Preserve a separate
identifier for the external execution. Do **not** merge it into a local Run or
call it independent validation merely because the host differs: operator,
organization, trust-domain and signing identity need their own authentication.

This package can be retrieved and attempted by another operator once the
containing `aigc-lite` revision is published. Until an actual outside run is
returned and authenticated, `external_independent_reverification` remains
**not observed**.
