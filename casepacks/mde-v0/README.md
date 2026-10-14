# MDE research casepack v0

This is a separately installable, candidate-only package. The main
`aigc-lite` wheel does not include `aigc_lite_casepacks` and the application
does not discover or register this package automatically. It imports the
frozen Bio/Citation v1 observation helpers from `app`, never the reverse.
This is an in-tree development package, not a published compatibility promise:
the tagged `v0.6.0` core predates those helpers even though the current core
metadata still says `0.6.0`. Install it only alongside this checkout's core
wheel/source. Before separately publishing the casepack, release a core
version containing the required helpers and raise its dependency floor to
that exact version; the present `>=0.6,<0.7` range is insufficient as a
standalone compatibility guarantee.
The shared `aigc_lite_casepacks` parent is a PEP 420 namespace (no parent
`__init__.py`), so future independently installed casepacks need not claim
ownership of the same top-level file. This distribution owns only its
`mde_v0` child package and its contract asset.
Only the two new MDE v0 diagnostics move in this change. Previously frozen
Bio/Citation/Math v1 source and existing core integrations stay in place;
their historical source hashes are not rewritten. Any later migration needs
its own versioned contract, compatibility plan, and package release. A
candidate-only pattern does not become a new core authority primitive merely
because it appears in two Cases: core promotion requires at least two
independent domains to demonstrate a common gap in existing primitives and a
reviewed, deterministic, domain-neutral contract.

From the repository root, install the core and this optional casepack:

```bash
python -m pip install -e ".[dev]"
python -m pip install -e ./casepacks/mde-v0
python -m pytest -q tests/test_bio_minimal_disambiguating_evidence.py tests/test_citation_minimal_disambiguating_evidence.py
```

Versioned import paths are
`aigc_lite_casepacks.mde_v0.bio.diagnose_bio_mde` and
`aigc_lite_casepacks.mde_v0.citation.diagnose_citation_mde`. These functions
are pure research diagnostics with host-supplied trust roots. They have no
HTTP/MCP route or persistence and cannot create a QualificationReceipt,
CurrentUseBinding, or AuthorizationGrant. See the [MDE contract](../../docs/MINIMAL_DISAMBIGUATING_EVIDENCE.md)
for the narrow claims and limitations.

The package version identifies this distribution, not a frozen execution
environment. Reports include the checker source and direct-dependency hashes;
the separately supplied trust-root snapshot and dependency environment must
also be retained for reproducible historical verification.
The Bio lineage contract JSON is included in this wheel and checked against
the reviewed digest in tests. Earlier frozen v1 Case locks are not bundled;
retrieve their fixed bytes from the stated public source when rechecking the
full research provenance.

After building both wheels, run
`python casepacks/mde-v0/check_wheels.py <core.whl> <casepack.whl>` from the
repository root. It rejects a core wheel containing casepack code, a casepack
wheel that owns the shared namespace `__init__.py`, and missing versioned
modules or Bio contract bytes. Wheel builders can retain stale `build/lib`
files, so inspect the *built wheel*, not only the source tree.
