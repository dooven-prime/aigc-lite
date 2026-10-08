# One-claim qualification slice: OpenAI math family 003

This slice is deliberately limited to the Riemann zeta subclaim of Theorem 1.1 in
the pinned [paper](https://github.com/openai/math/blob/adc7f1241b42e322a6451854ab7e4b4c146bf78a/preprints/The-Quasi-Riemann-Hypothesis-September-30-2026/paper.pdf),
page 4: for every complex (s), if ℜ(s)>7/8, then ζ((s)) is nonzero.
It does **not** inherit the paper's Dirichlet, Hecke, or application claims.
The boundary ℜ(s)=7/8 is excluded. The pole at (s=1), and the exact
relationship between the paper's meromorphic zeta and Mathlib's `riemannZeta`,
remain explicit subjects of the paper-to-challenge semantic review.

## Frozen inputs

`OpenAIMathCase003Source` has one fixed repository, commit
`adc7f1241b42e322a6451854ab7e4b4c146bf78a`, and a 31-file allowlist:
the PDF, family-003 scope note, Comparator challenge and JSON policy, Lean
solution module, `lean-toolchain`, `lake-manifest.json`, `lakefile.lean`, and
all 23 compatibility patches consumed by the upstream Lakefile.
Each raw response has an expected SHA-256. The PDF is stored as bounded
base64 chunk Artifacts; the other source bytes are stored as base64 Artifacts.
The snapshot manifest records each file's raw hash, size, and chunk identities.
The gate reconstructs and rehashes these bytes; it does not trust metadata alone.

This is byte pinning, **not** an independently authenticated Git tree or verified
commit signature. The snapshot says so. The release catalogue itself remains
candidate-only.

## Bounded Linux preparation

The project can be prepared without fetching the whole 122k-file `lean/` tree.
Run `python scripts/prepare_math_case_003_source.py CHECKOUT` on Linux; it uses
the fixed public Git origin, a partial clone, and a sparse checkout of the
31 pinned files plus `lean/OAI/NumberTheory/DirichletL/`. It is idempotent
for an existing clean checkout at the same commit and refuses a different
origin, commit, or source bytes. The latter directory contains
the local imports needed by the selected solution; its external imports are
Mathlib, `PrimeNumberTheoremAnd`, and `RellichKondrachov`. Keep the actual
upstream `lakefile.lean` and manifest unchanged. A clean sparse checkout at the
fixed commit is acceptable; replacing it with a reconstructed Git repository
or a modified minimal Lake project is not.

`python scripts/prepare_math_case_003_dependencies.py CHECKOUT` validates the
commit, all 31 source hashes, and the 42-entry manifest before it clones any
dependency. It accepts only pinned Git revisions at public GitHub HTTPS URLs,
does not execute Lake, and refuses dirty or wrong-revision preexisting package
checkouts. `--package NAME` limits a first pass; `--dry-run` lists the plan.
Each package receives an empty `.lake` mountpoint for ephemeral builds. The
upstream Lakefile applies compatibility patches during configuration, so a
replay must not mount the original dependency checkouts writable. The preferred
path pre-applies exactly the 23 pinned patches **outside** the replay sandbox,
seals all 42 package copies under a SHA-256 content address, and verifies the
source-byte tree (including symlink target strings, excluding Git metadata and
empty build mountpoints), revisions, origins, patch
reversibility, and manifest address before **and after** replay:

```bash
python scripts/prepare_math_case_003_patched_closure.py prepare CHECKOUT CLOSURE_STORE
python scripts/prepare_math_case_003_patched_closure.py verify CHECKOUT CLOSURE_STORE/DIGEST
aigc-lite math-case-003 replay ... --sandbox-profile bwrap-v1 \
  --patched-closure-root CLOSURE_STORE/DIGEST
```

Bubblewrap mounts every patched package read-only. The upstream Lakefile's
reverse-patch checks still run, but cannot change package source. The earlier
disposable writable scratch mode remains for diagnostics via
`--dependency-scratch-root`; it never sets `patch_closure_verified=true` and
therefore cannot qualify a claim. The CLI replay is bounded to 3,600 seconds
by default; an operator may set `--timeout-seconds` between 60 and 7,200.

The closure attests local source bytes and pinned patch application, not
unsigned upstream Git history or sandbox correctness. The gate still requires
an actual passing replay and separate semantic review; closure preparation
alone changes no Claim state.

Cold build output can optionally be retained in a fresh, empty build-work
directory, then sealed as separate content-addressed **compiler cache
evidence**:

```bash
python scripts/math_case_003_build_cache.py prepare --project CHECKOUT --work NEW_WORK
# run bounded diagnostics/replay with --build-work-root NEW_WORK
python scripts/math_case_003_build_cache.py capture --project CHECKOUT \
  --closure CLOSURE_STORE/DIGEST --work NEW_WORK --store CACHE_STORE \
  --lake LAKE --lean LEAN --comparator COMPARATOR \
  --sandbox-policy-sha256 POLICY_SHA256
python scripts/math_case_003_build_cache.py verify --project CHECKOUT \
  --closure CLOSURE_STORE/DIGEST --entry CACHE_STORE/CACHE_DIGEST \
  --lake LAKE --lean LEAN --comparator COMPARATOR \
  --sandbox-policy-sha256 POLICY_SHA256
```

The cache key binds source commit, pinned file set, patched dependency digest,
binary hashes, and the operator-supplied sandbox policy hash from the separate
Bubblewrap audit. Verification rehashes every output byte, but does not
independently reconstruct that policy attestation. This first version audits
**capture and integrity only**; it does
not preload `.olean` into qualification replay. Cache validity is not kernel
validity. A warm-cache acceleration path needs a separate poisoning and
challenge-first-order audit before influencing qualification.

The local `bwrap-v1` profile binds source and tool binaries read-only, hides the
host home and Windows mounts, starts with an empty environment and network
namespace, and mounts the project and per-package `.lake` build directories as
tmpfs by default. The optional build-work root is a fresh writable capture
directory; legacy scratch mode adds a disposable writable source copy. The
replay receipt hashes
both the Bubblewrap executable and **all**
fixed policy arguments. Before attempting a replay, run
`scripts/audit_math_case_003_sandbox.py` with the checkout and tool paths;
its probes check namespace separation, hidden host paths, no network route,
read-only source, and non-persistent build writes. This is a boundary smoke,
not a proof of kernel or hypervisor isolation.

The tool preparation tested Comparator commit
`d03acab154d269c06e60e4de7e4cc85deebff94b` and its pinned lean4export
`076e8e57707e813375e8f9da8bf989799ace9680`, built with Lean 4.34.1,
and landrun commit `811cfff51ceaf3d9843708aa6d22e9b84ccac8b4`.
Comparator's own project pin is Lean 4.34.0, so this local cross-patch build is
an explicitly recorded compatibility choice, **not** an official 4.34.1 tool
release. Its small positive and statement-mismatch examples ran inside the
outer sandbox; that is not the case-003 replay. On WSL kernel 5.15, landrun
cannot enforce its newer network restrictions: the external Bubblewrap network
namespace is mandatory. For high-assurance publication, independently audit
the exact binary/policy hashes and re-run on a separately administered host.

## Qualification path

1. `aigc-lite math-case-003 snapshot --workspace WORKSPACE` downloads the exact
   source bytes, freezes a single `ClaimRevision`, and records a source-hash
   VerificationAttempt linking the two. The source-snapshot criterion can now
   be satisfied, but this grants no mathematical qualification.
2. A deployer prepares a clean checkout at the pinned commit and the entire
   Lake dependency graph at the revisions in the pinned manifest. The local
   backend checks every package checkout, including Mathlib
   `d13f23b723b8a846827a245b89c10fc7d3f11612`.
3. A **deployer-owned external sandbox** must contain the entire `lake env`
   invocation, not just Comparator's own solution subprocess. The backend
   refuses to run on Windows or without an explicit sandbox wrapper. In that
   boundary, it invokes Comparator without first compiling the solution.
   Comparator builds and exports the challenge first, then the solution, and
   performs its own kernel replay; a failed invocation leaves the individual
   build/kernel stages undetermined. It uses the pinned
   [Comparator challenge](https://github.com/openai/math/blob/adc7f1241b42e322a6451854ab7e4b4c146bf78a/lean/ComparatorChallenges/QuasiRiemannHypothesis.json).
   Comparator's successful exit is required for exact declaration comparison,
   permitted axioms, and kernel acceptance. A mere successful Lean compile
   does not satisfy statement identity.
4. The run, step, source Artifacts, verification receipt Artifact, and
   VerificationAttempt are persisted. `aigc-lite math-case-003 evaluate
   --workspace WORKSPACE --claim-id ID` runs the deterministic
   `math.formal.project.v2` gate.
5. A separate, non-model `EXPERT_REVIEW` Attempt must bind the exact claim
   semantic hash and snapshot hash and cite the snapshot Artifact. Its
   `input_digest` is the canonical hash of
   `{"claim": CLAIM_SEMANTIC_HASH, "snapshot": SNAPSHOT_ARTIFACT_HASH}`.
   Until this review and a passing isolated replay exist, the verdict remains
   `UNRESOLVED` or `BLOCKED`; there is no receipt. Even `ADMITTED` creates no
   `CurrentUseBinding`: knowledge admission is a separate explicit action.

The [Comparator trust assumptions](https://github.com/leanprover/comparator)
matter: the challenge/import closure and the Lake project itself must be
controlled or sandboxed, and the verifier binaries and policy are part of the
trusted deployment. A wrapper's presence is an operator assertion about its
isolation; audit that policy before using an `ADMITTED` receipt as external
assurance. The checked local dependency revisions do not independently prove
remote Git tree authenticity. Multiple agents agreeing on the result are not
counted as independent validation; the evidence vector leaves that axis
`undetermined`.

The reviewer's organization or external trust domain is not certified by
workspace identity. An authenticated admin review is a policy-held approval,
not evidence of an independent institution. The reviewer must inspect the
pole-at-one convention and the paper-to-formal statement mapping; agreement
among model agents cannot substitute for this check.

## Negative regression and current status

`tests/test_math_project_slice.py` compiles a deliberately weaker theorem in a
real local Lean kernel: it adds an assumption and shadows `riemannZeta` with a
different local definition. The test then supplies a **simulated** failed
Comparator observation. The gate returns `BLOCKED`, `comparator_rejected`,
and `statement_identity=undetermined` (a nonzero exit alone does not prove
which check failed). It issues no QualificationReceipt or CurrentUseBinding.
This is a regression of
the authority boundary, not a claim that the upstream project has been
replayed.

The fixed WSL toolchain is Lean 4.34.1 with Comparator, `landrun`,
`lean4export`, and the `bwrap-v1` local boundary. A real cold-source replay
reached Mathlib compilation but exhausted the default 3,600-second wall-clock
budget before challenge/solution comparison. Its Comparator, statement
identity, and kernel results are therefore **undetermined**, not failed. No
passing case-003 Comparator or full-project kernel receipt has been issued.
The local Run/Receipt stays outside the repository and cannot be promoted by
this incomplete execution.
The subsequent content-addressed patched closure passed its local hash audit
and read-only Bubblewrap probes, but **no successful full case-003 replay**
has yet been established from it.
A separate 45-second diagnostic cold build reached early challenge compilation,
timed out as expected, and produced a sealed cache snapshot of 3,303 files.
The snapshot re-audited successfully; it is neither a Comparator pass nor a
Qualification Receipt.
