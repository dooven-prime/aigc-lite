"""Probe the local bwrap-v1 replay boundary without running the math project.

This is a deployment smoke test, not a formal security audit or a proof receipt.
The verifier must still run in a separately administered environment for
high-assurance use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.kernel_verification.math_project_sandbox import (  # noqa: E402
    bubblewrap_math_project_args,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "project-root", "lake", "lean", "comparator-root", "comparator",
        "landrun", "lean4export",
    ):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--dependency-scratch-root", type=Path)
    parser.add_argument("--patched-closure-root", type=Path)
    parser.add_argument("--build-work-root", type=Path)
    args = parser.parse_args()
    bwrap = Path("/usr/bin/bwrap").resolve(strict=True)
    project = args.project_root.resolve(strict=True)
    policy = bubblewrap_math_project_args(
        project,
        lake_executable=args.lake,
        lean_executable=args.lean,
        comparator_root=args.comparator_root,
        comparator_executable=args.comparator,
        landrun_executable=args.landrun,
        lean4export_executable=args.lean4export,
        dependency_scratch_root=args.dependency_scratch_root,
        patched_closure_root=args.patched_closure_root,
        build_work_root=args.build_work_root,
    )

    def probe(*command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (str(bwrap), *policy, *command),
            cwd=project / "lean",
            capture_output=True,
            text=True,
            env={**os.environ, "AIGC_LITE_SANDBOX_SENTINEL": "must-not-cross"},
            timeout=15,
            check=False,
        )

    toolchain = probe(str(args.lean.resolve(strict=True)), "--version")
    lake = probe(str(args.lake.resolve(strict=True)), "--version")
    hidden_windows_c = probe("/usr/bin/test", "-e", "/mnt/c")
    hidden_windows_e = probe("/usr/bin/test", "-e", "/mnt/e")
    hidden_ssh = probe("/usr/bin/test", "-e", str(Path.home() / ".ssh"))
    hidden_docker = probe("/usr/bin/test", "-e", "/run/docker.sock")
    network_routes = probe("/usr/bin/cat", "/proc/net/route")
    pid_one = probe("/usr/bin/cat", "/proc/1/comm")
    environment = probe("/usr/bin/env")
    source_write = probe("/usr/bin/touch", str(project / "lean" / "lean-toolchain"))
    package_source_permission = probe(
        "/usr/bin/test", "-w",
        str(project / "lean" / ".lake" / "packages" / "mathlib"),
    )
    ephemeral_file = project / "lean" / ".lake" / ".aigc-lite-sandbox-probe"
    work_project_probe = (args.build_work_root / "project" / ".aigc-lite-sandbox-probe"
                          if args.build_work_root else None)
    if work_project_probe is not None and work_project_probe.exists():
        raise RuntimeError(f"Refusing a pre-existing build-work probe: {work_project_probe}")
    if ephemeral_file.exists():
        raise RuntimeError(f"Refusing a pre-existing probe file: {ephemeral_file}")
    ephemeral_write = probe("/usr/bin/touch", str(ephemeral_file))
    package_file = project / "lean" / ".lake" / "packages" / "mathlib" / ".lake" / ".aigc-lite-sandbox-probe"
    work_package_probe = (args.build_work_root / "packages" / "mathlib" / ".aigc-lite-sandbox-probe"
                          if args.build_work_root else None)
    if work_package_probe is not None and work_package_probe.exists():
        raise RuntimeError(f"Refusing a pre-existing build-work probe: {work_package_probe}")
    if package_file.exists():
        raise RuntimeError(f"Refusing a pre-existing probe file: {package_file}")
    package_write = probe("/usr/bin/touch", str(package_file))
    checks = {
        "pinned_lean_runs": toolchain.returncode == 0 and "4.34.1" in toolchain.stdout,
        "pinned_lake_runs": lake.returncode == 0 and "4.34.1" in lake.stdout,
        "windows_mounts_hidden": hidden_windows_c.returncode == 1
        and hidden_windows_e.returncode == 1,
        "ssh_directory_hidden": hidden_ssh.returncode == 1,
        "docker_socket_hidden": hidden_docker.returncode == 1,
        "no_network_route": network_routes.returncode == 0
        and len(network_routes.stdout.strip().splitlines()) == 1,
        "pid_namespace_separate": pid_one.returncode == 0
        and pid_one.stdout.strip() != "systemd",
        "environment_cleared": environment.returncode == 0
        and "AIGC_LITE_SANDBOX_SENTINEL" not in environment.stdout,
        "source_tree_read_only": source_write.returncode != 0,
        "package_source_permission_expected": package_source_permission.returncode
        == (0 if args.dependency_scratch_root else 1),
        "project_build_isolated": ephemeral_write.returncode == 0
        and not ephemeral_file.exists()
        and (work_project_probe is None or work_project_probe.is_file()),
        "package_build_isolated": package_write.returncode == 0
        and not package_file.exists()
        and (work_package_probe is None or work_package_probe.is_file()),
    }
    for owned_probe in (work_project_probe, work_package_probe):
        if owned_probe is not None and owned_probe.is_file() and not owned_probe.is_symlink():
            if owned_probe.stat().st_size != 0:
                raise RuntimeError(f"Build-work probe had unexpected content: {owned_probe}")
            owned_probe.unlink()
    policy_hash = hashlib.sha256(
        json.dumps(
            {
                "executable_sha256": hashlib.sha256(bwrap.read_bytes()).hexdigest(),
                "fixed_args": policy,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    print(json.dumps({"policy_hash": policy_hash, "checks": checks}, indent=2, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
