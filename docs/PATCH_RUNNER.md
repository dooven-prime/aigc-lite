# Controlled Patch Runner contract

**Status:** planned security contract; no source-mutation runner or public
patch API is implemented by the current release.

The future Patch Runner exists to turn a reproducible failure into a narrowly
bounded source repair. It is not a general-purpose autonomous coding shell.
Its governing rule is:

> A failure that cannot be independently reproduced from a clean, pinned base
> cannot confer source-modification authority.

The intended flow is:

```text
hostile failure candidate
  -> clean-worktree independent reproduction
  -> immutable FailureReceipt
  -> narrow path-bound modification grant
  -> patch protocol-owned source only
  -> replay the exact failure and the frozen regression set
  -> external accept or discard
```

## Authority separation

Candidate submission, failure reproduction, patch generation, validation, and
acceptance are separate roles. A single Agent execution must never control all
of them. In particular, the patching identity cannot modify or replace:

- the failing test or reproduction command;
- the validator, comparator, oracle, or failure predicate;
- the pinned baseline commit, dependency lock, toolchain, or environment
  manifest;
- the FailureReceipt, its evidence closure, or the receipt verifier;
- the regression selection policy or final acceptance decision.

Changing one of those control surfaces requires a separate proposal, separate
authorization, and separate evaluation. It cannot be bundled into the source
patch whose success it judges.

## Reproduction gate

Reproduction runs in a newly created clean worktree at an exact commit. The
runner freezes at least:

```text
repository identity and base commit
clean-tree assertion and submodule state
dependency lock and toolchain/environment digests
failure candidate and reproduction command digests
validator/oracle and expected-failure digests
declared network, filesystem, process and credential envelope
exit status, bounded logs and produced Artifact hashes
```

The gate returns `REPRODUCED`, `NOT_REPRODUCED`, or `INDETERMINATE`.
`NOT_REPRODUCED` and `INDETERMINATE` issue no modification capability. Flaky,
environment-dependent, network-dependent, or ambiguous failures remain
ineligible until a versioned reproduction policy explicitly closes that
uncertainty.

`FailureReceipt` should be implemented as a typed immutable Receipt over the
existing Run/Step/Artifact evidence closure, not as a new mutable truth flag.
It records what failed under which frozen conditions; it does not authorize a
patch by itself.

## Narrow modification grant

After a valid FailureReceipt, an external control-plane principal may issue a
short-lived grant bound to the exact receipt and base commit. The effective
write capability is the intersection of:

```text
allowed source path prefixes
- denied control-surface paths
- symlink, submodule and path-traversal escapes
+ maximum files and bytes changed
+ allowed commands/processes
+ filesystem/network/credential policy
+ deadline, call budget and cancellation mode
```

The deny set always wins. Test directories, fixtures used by the oracle,
validator code, baselines, manifests, receipts, CI policy, repository hooks,
and release/authorization policy are denied unless a separate maintenance
workflow—not the patch under judgment—owns their change.

The grant is not a workspace-wide `write` scope. It is a one-receipt,
one-base, one-worktree capability. Any base drift, unexpected generated file,
untracked control-surface change, or write outside the allowlist invalidates
the attempt.

## Replay and decision

Validation first replays the exact frozen failure. Only after it changes from
the declared failing state to the declared passing state may the runner execute
the frozen regression set. Both executions occur without granting the patching
Agent authority to choose tests or reinterpret outcomes.

The result is an immutable patch attempt containing the diff Artifact, exact
replay and regression evidence, enforcement receipts, limitations, and all
digests. A separate acceptance gate may then:

- accept the patch into a reviewable branch or proposal;
- discard the worktree and retain the historical evidence; or
- leave the attempt unresolved for an operator.

Acceptance does not directly deploy, release, change qualification, or widen
execution authority. Those remain separate gates.

## Required hostile tests before implementation

The implementation is not complete until adversarial tests demonstrate that:

- a non-reproducible or indeterminate failure cannot obtain a write grant;
- the patch cannot edit tests, validators, baselines, receipts, manifests, CI
  policy, or the failure predicate;
- path traversal, symlink replacement, submodule escape and generated-file
  tricks cannot cross the write envelope;
- the Agent cannot narrow the test selection or replace the original failure;
- a patch passing only its self-authored test is rejected;
- base-commit, lockfile, toolchain or receipt drift makes the attempt stale;
- timeout or uncertain cancellation leaves the worktree blocked rather than
  accepted;
- acceptance requires a principal outside the patch execution identity.

The preferred implementation boundary is an external sandbox/enforcer behind
the existing Tool Catalog, AuthorizationGrant and signed EnforcementReceipt
contracts. Core should coordinate immutable evidence and authority transitions,
not claim that an in-process Python path check is a sufficient sandbox.
