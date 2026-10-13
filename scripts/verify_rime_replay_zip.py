"""Validate and safely unpack the fixed RIME r2 delivery before any execution.

Both expected digests must come from a source outside the ZIP. This command
does not execute the bundled checker or trust the bundled host verifier.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import stat
import sys
import unicodedata
import zipfile
from pathlib import Path

PACKAGE_ROOT = "rime-consumer-v1-r2"
MANIFEST_MEMBER = f"{PACKAGE_ROOT}/manifest.json"
BUNDLED_VERIFIER = "verify_rime_replay_package.py"
MAX_ZIP_BYTES = 60_000_000
MAX_MEMBER_BYTES = 10_000_000
MAX_TOTAL_BYTES = 50_000_000
MAX_MEMBERS = 64
COPY_CHUNK_BYTES = 65536


class UnsafeDeliveryZipError(ValueError):
    """The delivery container cannot be safely materialized."""


def _hash_stream(stream) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(COPY_CHUNK_BYTES), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _check_digest(value: str, label: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise UnsafeDeliveryZipError(f"{label} must be a lowercase SHA-256 digest")


def _member_parts(info: zipfile.ZipInfo) -> tuple[str, ...]:
    name = info.filename
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or "\x00" in name
        or unicodedata.normalize("NFC", name) != name
        or any(ord(char) < 32 for char in name)
    ):
        raise UnsafeDeliveryZipError(f"unsafe ZIP member name: {name!r}")
    clean = name[:-1] if info.is_dir() else name
    parts = tuple(clean.split("/"))
    if any(part in ("", ".", "..") or ":" in part for part in parts):
        raise UnsafeDeliveryZipError(f"unsafe ZIP member path: {name!r}")
    if parts[0] != PACKAGE_ROOT and parts != (BUNDLED_VERIFIER,):
        raise UnsafeDeliveryZipError(f"unexpected ZIP root: {name!r}")
    if parts == (PACKAGE_ROOT,) and not info.is_dir():
        raise UnsafeDeliveryZipError("package root must be a directory")
    mode_type = stat.S_IFMT(info.external_attr >> 16)
    if mode_type == stat.S_IFLNK or mode_type not in (
        0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG
    ):
        raise UnsafeDeliveryZipError(f"ZIP link or special member: {name!r}")
    if info.flag_bits & 1:
        raise UnsafeDeliveryZipError(f"encrypted ZIP member: {name!r}")
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise UnsafeDeliveryZipError(f"unsupported compression: {name!r}")
    return parts


def _inspect(archive: zipfile.ZipFile, expected_manifest_sha256: str) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    if not infos or len(infos) > MAX_MEMBERS:
        raise UnsafeDeliveryZipError("ZIP member count exceeds the delivery contract")
    seen: set[str] = set()
    file_names: set[str] = set()
    file_parts: set[tuple[str, ...]] = set()
    total = 0
    for info in infos:
        parts = _member_parts(info)
        key = "/".join(parts).casefold()
        if key in seen:
            raise UnsafeDeliveryZipError(f"duplicate ZIP path: {info.filename!r}")
        seen.add(key)
        if not info.is_dir():
            if info.file_size > MAX_MEMBER_BYTES:
                raise UnsafeDeliveryZipError(f"oversized ZIP member: {info.filename!r}")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise UnsafeDeliveryZipError("ZIP expanded size exceeds the delivery contract")
            file_names.add(info.filename)
            file_parts.add(parts)
    for parts in file_parts:
        if any(parts[:index] in file_parts for index in range(1, len(parts))):
            raise UnsafeDeliveryZipError("ZIP file is used as a parent directory")
    if MANIFEST_MEMBER not in file_names or BUNDLED_VERIFIER not in file_names:
        raise UnsafeDeliveryZipError("required delivery members are missing")
    manifest_info = archive.getinfo(MANIFEST_MEMBER)
    with archive.open(manifest_info) as stream:
        raw = stream.read(MAX_MEMBER_BYTES + 1)
    if len(raw) > MAX_MEMBER_BYTES:
        raise UnsafeDeliveryZipError("oversized package manifest")
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_manifest_sha256):
        raise UnsafeDeliveryZipError("manifest differs from the out-of-band digest")
    try:
        manifest = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise UnsafeDeliveryZipError("invalid package manifest") from exc
    if manifest.get("schema") != "rime.consumer-replay.package-manifest.v2":
        raise UnsafeDeliveryZipError("unexpected package manifest schema")
    entries = manifest.get("files")
    if not isinstance(entries, list) or len(entries) != 26:
        raise UnsafeDeliveryZipError("unexpected package file inventory")
    expected_files = {f"{PACKAGE_ROOT}/{entry.get('path')}" for entry in entries if isinstance(entry, dict)}
    if len(expected_files) != 26 or file_names != expected_files | {MANIFEST_MEMBER, BUNDLED_VERIFIER}:
        raise UnsafeDeliveryZipError("ZIP files differ from the pinned package inventory")
    permitted_dirs = {PACKAGE_ROOT}
    for filename in expected_files | {MANIFEST_MEMBER}:
        parts = filename.split("/")
        permitted_dirs.update("/".join(parts[:index]) for index in range(1, len(parts)))
    for info in infos:
        if info.is_dir() and info.filename.rstrip("/") not in permitted_dirs:
            raise UnsafeDeliveryZipError(f"unexpected ZIP directory: {info.filename!r}")
    return infos


def safely_extract_delivery(
    archive_path: Path,
    destination: Path,
    expected_zip_sha256: str,
    expected_manifest_sha256: str,
) -> dict[str, object]:
    """Extract only after fixed-byte and ZIP-container validation.

    The destination must not exist. No member is executed by this function.
    """
    _check_digest(expected_zip_sha256, "ZIP digest")
    _check_digest(expected_manifest_sha256, "manifest digest")
    if archive_path.is_symlink() or not archive_path.is_file():
        raise UnsafeDeliveryZipError("ZIP must be a regular file")
    if archive_path.stat().st_size > MAX_ZIP_BYTES:
        raise UnsafeDeliveryZipError("ZIP byte size exceeds the delivery contract")
    if destination.exists() or destination.is_symlink():
        raise UnsafeDeliveryZipError("destination must not already exist")
    if not destination.parent.is_dir():
        raise UnsafeDeliveryZipError("destination parent does not exist")
    with archive_path.open("rb") as source:
        actual = _hash_stream(source)
        if not hmac.compare_digest(actual, expected_zip_sha256):
            raise UnsafeDeliveryZipError("ZIP differs from the out-of-band digest")
        source.seek(0)
        try:
            with zipfile.ZipFile(source) as archive:
                infos = _inspect(archive, expected_manifest_sha256)
                destination.mkdir()
                expanded = 0
                for info in infos:
                    target = destination.joinpath(*_member_parts(info))
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    member_bytes = 0
                    with archive.open(info) as reader, target.open("xb") as writer:
                        for chunk in iter(lambda: reader.read(COPY_CHUNK_BYTES), b""):
                            member_bytes += len(chunk)
                            expanded += len(chunk)
                            if member_bytes > MAX_MEMBER_BYTES or expanded > MAX_TOTAL_BYTES:
                                raise UnsafeDeliveryZipError("ZIP decompression exceeded bounds")
                            writer.write(chunk)
                    if member_bytes != info.file_size:
                        raise UnsafeDeliveryZipError(f"ZIP member size changed: {info.filename!r}")
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise UnsafeDeliveryZipError(f"ZIP extraction failed: {exc}") from exc
    return {
        "zip_sha256": actual,
        "manifest_sha256": expected_manifest_sha256,
        "package_file_count": 26,
        "package_path": str(destination / PACKAGE_ROOT),
        "bundled_verifier_trusted": False,
        "executed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", dest="archive_path", required=True, type=Path)
    parser.add_argument("--expected-zip-sha256", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = safely_extract_delivery(
            args.archive_path, args.destination,
            args.expected_zip_sha256, args.expected_manifest_sha256,
        )
    except UnsafeDeliveryZipError as exc:
        print(f"DELIVERY_REJECTED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
