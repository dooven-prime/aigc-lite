"""The optional MDE diagnostics do not enter the core distribution by accident."""

from __future__ import annotations

import ast
import hashlib
import tomllib
from importlib.resources import files
from pathlib import Path

from aigc_lite_casepacks.mde_v0 import (
    bio,
    bio_certificate,
    certificate_common,
    certificate_replay,
    citation,
    citation_certificate,
)


def test_casepack_has_its_own_distribution_and_packaged_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    core = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    package = tomllib.loads(
        (root / "casepacks/mde-v0/pyproject.toml").read_text(encoding="utf-8")
    )
    assert core["tool"]["setuptools"]["packages"]["find"]["include"] == ["app*"]
    assert package["project"]["name"] == "aigc-lite-mde-casepack"
    assert package["project"]["version"] == "0.2.0.dev0"
    assert package["project"]["dependencies"] == ["aigc-lite==0.7.0"]
    assert "entry-points" not in package["project"]
    assert package["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "aigc_lite_casepacks*"
    ]
    assert package["tool"]["setuptools"]["packages"]["find"]["namespaces"] is True
    assert not (root / "casepacks/mde-v0/src/aigc_lite_casepacks/__init__.py").exists()
    assert package["tool"]["setuptools"]["package-data"]["aigc_lite_casepacks.mde_v0"] == [
        "bio_lineage_contract_v0.json",
        "certificate_contract_v0.json",
    ]
    contract = files("aigc_lite_casepacks.mde_v0").joinpath(
        "bio_lineage_contract_v0.json"
    ).read_bytes()
    assert hashlib.sha256(contract).hexdigest() == bio.CHECKER_DEPENDENCY_SHA256[
        "bio_mde_contract"
    ]
    assert bio.diagnose_bio_mde.__module__ == "aigc_lite_casepacks.mde_v0.bio"
    assert citation.diagnose_citation_mde.__module__ == "aigc_lite_casepacks.mde_v0.citation"
    assert bio_certificate.certify_bio_refutation.__module__ == (
        "aigc_lite_casepacks.mde_v0.bio_certificate"
    )
    assert citation_certificate.certify_citation_positive.__module__ == (
        "aigc_lite_casepacks.mde_v0.citation_certificate"
    )
    assert certificate_common.finalize_certificate.__module__ == (
        "aigc_lite_casepacks.mde_v0.certificate_common"
    )
    assert certificate_replay.replay_input.__module__ == (
        "aigc_lite_casepacks.mde_v0.certificate_replay"
    )


def test_core_application_has_no_static_casepack_import() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            assert not any(
                item == "aigc_lite_casepacks" or item.startswith("aigc_lite_casepacks.")
                for item in imported
            ), path


def test_candidate_certificate_modules_have_no_authority_or_transport_imports() -> None:
    root = Path(__file__).resolve().parents[1] / (
        "casepacks/mde-v0/src/aigc_lite_casepacks/mde_v0"
    )
    for filename in (
        "bio_certificate.py", "citation_certificate.py", "certificate_common.py",
        "certificate_replay.py",
    ):
        path = root / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any(
            item == "app.main"
            or item.startswith("app.services")
            or item.startswith("app.adapters")
            or item.startswith("app.repositories")
            or item == "mcp"
            or item.startswith("mcp.")
            for item in imports
        ), path
