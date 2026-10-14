"""Check that built core and MDE wheels preserve the optional package boundary."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZipFile

_CASEPACK_PREFIX = "aigc_lite_casepacks/"
_REQUIRED_CASEPACK_MEMBERS = {
    "aigc_lite_casepacks/mde_v0/__init__.py",
    "aigc_lite_casepacks/mde_v0/bio.py",
    "aigc_lite_casepacks/mde_v0/bio_certificate.py",
    "aigc_lite_casepacks/mde_v0/certificate_common.py",
    "aigc_lite_casepacks/mde_v0/citation.py",
    "aigc_lite_casepacks/mde_v0/citation_certificate.py",
    "aigc_lite_casepacks/mde_v0/bio_lineage_contract_v0.json",
    "aigc_lite_casepacks/mde_v0/certificate_contract_v0.json",
}


def check_wheels(core_wheel: Path, casepack_wheel: Path) -> None:
    """Fail if the main wheel owns a Case or the optional wheel owns the namespace."""
    with ZipFile(core_wheel) as archive:
        core_members = set(archive.namelist())
    with ZipFile(casepack_wheel) as archive:
        casepack_members = set(archive.namelist())

    leaked = sorted(name for name in core_members if name.startswith(_CASEPACK_PREFIX))
    if leaked:
        raise ValueError(f"Core wheel contains casepack members: {leaked}")
    if "aigc_lite_casepacks/__init__.py" in casepack_members:
        raise ValueError("Casepack wheel claims the shared PEP 420 namespace")
    missing = sorted(_REQUIRED_CASEPACK_MEMBERS - casepack_members)
    if missing:
        raise ValueError(f"Casepack wheel is missing versioned members: {missing}")
    embedded_core = sorted(name for name in casepack_members if name.startswith("app/"))
    if embedded_core:
        raise ValueError(f"Casepack wheel embeds core modules: {embedded_core}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("core_wheel", type=Path)
    parser.add_argument("casepack_wheel", type=Path)
    args = parser.parse_args()
    check_wheels(args.core_wheel, args.casepack_wheel)
    print("Core/casepack wheel boundary verified")


if __name__ == "__main__":
    main()
