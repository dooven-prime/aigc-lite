"""Host-owned pre-execution verifier for a pinned RIME finite replay package.

The package cannot authenticate its own Python before execution. This script
checks an out-of-band manifest digest and every file before starting it. It
does not supply an OS sandbox, signature, or independent mathematical review.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

SCHEMA = "rime.consumer-replay.package-manifest.v2"
RECEIPT_SCHEMA = "aigc-lite.rime-replay.host-receipt.v1"
MAX_FILE_BYTES = 10_000_000
MAX_PACKAGE_BYTES = 50_000_000


class PackageVerificationError(ValueError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_file_inventory(package: Path, manifest: dict) -> None:
    if manifest.get("schema") != SCHEMA:
        raise PackageVerificationError("unexpected manifest schema")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise PackageVerificationError("manifest files must be a nonempty list")
    names: set[str] = set()
    total = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise PackageVerificationError("non-object file entry")
        name = entry.get("path")
        digest = entry.get("sha256")
        size = entry.get("size")
        if (not isinstance(name, str) or not name or "\\" in name
                or PurePosixPath(name).is_absolute()
                or any(part in ("", ".", "..") for part in name.split("/"))
                or name == "manifest.json" or name in names
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or type(size) is not int or not 0 <= size <= MAX_FILE_BYTES):
            raise PackageVerificationError(f"invalid file entry: {name!r}")
        names.add(name)
        total += size
        if total > MAX_PACKAGE_BYTES:
            raise PackageVerificationError("package exceeds byte limit")
        path = package.joinpath(*name.split("/"))
        if path.is_symlink() or not path.is_file():
            raise PackageVerificationError(f"missing or symlink file: {name}")
        if path.stat().st_size != size or sha256_file(path) != digest:
            raise PackageVerificationError(f"size or SHA-256 mismatch: {name}")
    actual = {
        path.relative_to(package).as_posix()
        for path in package.rglob("*") if path.is_file() or path.is_symlink()
    }
    if actual != names | {"manifest.json"}:
        raise PackageVerificationError(
            f"file-set mismatch: missing={sorted(names - actual)}, "
            f"unlisted={sorted(actual - names - {'manifest.json'})}"
        )


def verify_before_execution(package: Path, expected_manifest_sha256: str) -> tuple[str, dict]:
    if re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256) is None:
        raise PackageVerificationError("expected manifest SHA-256 must be 64 lowercase hex chars")
    if package.is_symlink() or not package.is_dir():
        raise PackageVerificationError("package must be a real directory")
    manifest_path = package / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise PackageVerificationError("manifest missing or symlink")
    if manifest_path.stat().st_size > MAX_FILE_BYTES:
        raise PackageVerificationError("manifest exceeds byte limit")
    raw = manifest_path.read_bytes()
    digest = sha256_bytes(raw)
    if not hmac.compare_digest(digest, expected_manifest_sha256):
        raise PackageVerificationError("manifest digest differs from out-of-band pin")
    try:
        manifest = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise PackageVerificationError(f"invalid manifest JSON: {exc}") from exc
    validate_file_inventory(package, manifest)
    return digest, manifest


def run_replay(package: Path, digest: str, timeout: int) -> dict:
    env = {
        key: value for key, value in os.environ.items()
        if key in {"PATH", "SystemRoot", "WINDIR", "LANG", "LC_ALL", "TZ"}
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            [sys.executable, "-B", str(package / "checker" / "run.py"),
             "--package", str(package)],
            cwd=package, env=env, text=True, encoding="utf-8",
            capture_output=True, timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PackageVerificationError("replay timed out") from exc
    if completed.returncode != 0:
        raise PackageVerificationError(
            f"replay exited {completed.returncode}: {completed.stderr[:1000]} "
            f"{completed.stdout[:1000]}"
        )
    if len(completed.stdout) > 10_000_000:
        raise PackageVerificationError("replay report too large")
    try:
        report = json.loads(completed.stdout)
    except ValueError as exc:
        raise PackageVerificationError("replay did not emit one JSON report") from exc
    if (report.get("schema") != "rime.consumer-replay.cross-host-report.v2"
            or report.get("replay_kind") != "SAME_CHECKER_CROSS_HOST_REPLAY"
            or report.get("verification", {}).get("manifest_sha256") != digest
            or report.get("summary", {}).get("all_expected_match") is not True):
        raise PackageVerificationError("replay report lacks pinned successful closure")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True, help="new receipt outside package")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    package = args.package.absolute()
    output = args.out.absolute()
    if output == package or package in output.parents:
        parser.error("receipt must be outside the frozen package")
    if not 1 <= args.timeout <= 300:
        parser.error("timeout must be in [1, 300] seconds")
    try:
        digest, manifest = verify_before_execution(package, args.expected_manifest_sha256)
        report = run_replay(package, digest, args.timeout)
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "scope": "same-checker cross-host replay; no independent validation or qualification",
            "manifest_sha256": digest,
            "host_verifier_sha256": sha256_file(Path(__file__).resolve()),
            "frozen_spec_pin": manifest["frozen_spec_pin"],
            "report_sha256": sha256_bytes(
                json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ),
            "host": {"python": platform.python_version(), "platform": platform.platform()},
            "report": report,
        }
        if not output.parent.is_dir():
            raise PackageVerificationError("receipt parent directory does not exist")
        with output.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, sort_keys=True, indent=2, ensure_ascii=False)
            stream.write("\n")
        print(json.dumps({
            "receipt_path": str(output), "receipt_sha256": sha256_file(output),
            "manifest_sha256": digest, "all_expected_match": True,
            "scope": receipt["scope"],
        }, ensure_ascii=False))
        return 0
    except (OSError, PackageVerificationError, KeyError) as exc:
        print(f"REPLAY_UNRESOLVED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
