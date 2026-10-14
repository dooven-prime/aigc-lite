"""MDE certificates remain candidate-only, outside application authority surfaces."""

from __future__ import annotations

import importlib
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

from app import main
from app.adapters.tools import workspace
from app.mcp import handle_rpc
from app.repository import SQLiteRepository
from app.services.qualification import create_default_verifier_registry

_AUTHORITY_TABLES = (
    "qualification_receipts",
    "current_use_bindings",
    "authorization_grants",
)


def _surface() -> dict:
    # Recompute OpenAPI so a newly registered route cannot hide behind its cache.
    main.app.openapi_schema = None
    return {
        "http_routes": sorted(
            (
                type(route).__name__,
                getattr(route, "path", None) or "",
                tuple(sorted(getattr(route, "methods", None) or ())),
            )
            for route in main.app.routes
        ),
        "openapi_paths": sorted(main.app.openapi()["paths"]),
        "legacy_mcp_tools": sorted(
            item["name"]
            for item in handle_rpc(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            )["result"]["tools"]
        ),
        "workspace_capabilities": sorted(item[0] for item in workspace._SPECS),
        "qualification_profiles": sorted(
            item["profile_id"]
            for item in create_default_verifier_registry().list_profiles()
        ),
    }


def _authority_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as db:
        return {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in _AUTHORITY_TABLES
        }


def test_importing_certificate_modules_does_not_register_authority_surfaces() -> None:
    """A fresh interpreter checks import effects, unaffected by pytest collection."""
    root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        """
        import json
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path.cwd() / "casepacks/mde-v0/src"))
        from app import main
        from app.adapters.tools import workspace
        from app.mcp import handle_rpc
        from app.services.qualification import create_default_verifier_registry

        def surface():
            main.app.openapi_schema = None
            return {
                "routes": sorted((type(route).__name__, getattr(route, "path", None) or "",
                                  tuple(sorted(getattr(route, "methods", None) or ())))
                                 for route in main.app.routes),
                "openapi": sorted(main.app.openapi()["paths"]),
                "mcp": sorted(item["name"] for item in handle_rpc({
                    "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
                })["result"]["tools"]),
                "workspace": sorted(item[0] for item in workspace._SPECS),
                "profiles": sorted(item["profile_id"] for item in
                                   create_default_verifier_registry().list_profiles()),
            }

        before = surface()
        from aigc_lite_casepacks.mde_v0 import (
            bio_certificate, certificate_common, citation_certificate,
        )
        assert bio_certificate.certify_bio_refutation
        assert citation_certificate.certify_citation_positive
        assert certificate_common.verify_certificate_hash
        after = surface()
        assert after == before, json.dumps({"before": before, "after": after})
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_generating_bio_and_citation_certificates_does_not_mutate_authority(
    tmp_path: Path,
) -> None:
    bio_case = importlib.import_module("test_bio_disambiguation_certificate")
    citation_case = importlib.import_module("test_citation_disambiguation_certificate")
    citation_mde = importlib.import_module("test_citation_minimal_disambiguating_evidence")
    citation_sufficiency = importlib.import_module("test_citation_information_sufficiency")

    bio_inputs = bio_case._inputs()
    citation_db, bundle, match_ids, _ = citation_sufficiency._candidate(tmp_path)
    identities, lineages = citation_mde._evidence(bundle, match_ids)
    transient_db = tmp_path / "certificate-authority.db"
    repository = SQLiteRepository(transient_db)
    repository.init()

    before_surface = _surface()
    before_transient = _authority_counts(transient_db)
    before_citation = _authority_counts(citation_db)

    bio_certificate = bio_case._cert(bio_inputs)
    citation_certificate = citation_case._cert(
        bundle, match_ids, identities, lineages[0]
    )

    for certificate in (bio_certificate, citation_certificate):
        assert certificate["candidate_only"] is True
        assert certificate["qualification_granted"] is False
        assert certificate["current_use_bound"] is False
        assert certificate["authorization_granted"] is False
    assert _surface() == before_surface
    assert _authority_counts(transient_db) == before_transient == dict.fromkeys(
        _AUTHORITY_TABLES, 0
    )
    assert _authority_counts(citation_db) == before_citation == dict.fromkeys(
        _AUTHORITY_TABLES, 0
    )
