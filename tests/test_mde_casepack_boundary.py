"""The optional MDE diagnostics do not enter the core distribution by accident."""

from __future__ import annotations

import ast
import hashlib
import tomllib
from importlib.resources import files
from pathlib import Path

from aigc_lite_casepacks.mde_v0 import bio, citation


def test_casepack_has_its_own_distribution_and_packaged_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    core = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    package = tomllib.loads(
        (root / "casepacks/mde-v0/pyproject.toml").read_text(encoding="utf-8")
    )
    assert core["tool"]["setuptools"]["packages"]["find"]["include"] == ["app*"]
    assert package["project"]["name"] == "aigc-lite-mde-casepack"
    assert package["project"]["dependencies"] == ["aigc-lite==0.7.0"]
    assert package["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "aigc_lite_casepacks*"
    ]
    assert package["tool"]["setuptools"]["packages"]["find"]["namespaces"] is True
    assert not (root / "casepacks/mde-v0/src/aigc_lite_casepacks/__init__.py").exists()
    assert package["tool"]["setuptools"]["package-data"]["aigc_lite_casepacks.mde_v0"] == [
        "bio_lineage_contract_v0.json"
    ]
    contract = files("aigc_lite_casepacks.mde_v0").joinpath(
        "bio_lineage_contract_v0.json"
    ).read_bytes()
    assert hashlib.sha256(contract).hexdigest() == bio.CHECKER_DEPENDENCY_SHA256[
        "bio_mde_contract"
    ]
    assert bio.diagnose_bio_mde.__module__ == "aigc_lite_casepacks.mde_v0.bio"
    assert citation.diagnose_citation_mde.__module__ == "aigc_lite_casepacks.mde_v0.citation"


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
