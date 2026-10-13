"""Package-local diagnostic integrity check.

This runs *inside* the package and is not a trusted pre-execution bootstrap.
Use the host-owned scripts/verify_rime_replay_package.py for that boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_package(package_dir: Path) -> dict:
    root = package_dir.resolve()
    result = {
        "ok": False, "package_dir": str(root), "manifest_sha256": None,
        "file_count": 0, "mismatches": [], "missing": [],
        "unlisted": [], "invalid": [],
    }
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        result["missing"].append("manifest.json")
        return result
    result["manifest_sha256"] = _sha256(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        result["invalid"].append(f"manifest JSON: {exc}")
        return result
    if manifest.get("schema") != "rime.consumer-replay.package-manifest.v2":
        result["invalid"].append("unexpected manifest schema")
        return result
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        result["invalid"].append("files must be a nonempty list")
        return result
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            result["invalid"].append("non-object file entry")
            continue
        rel = entry.get("path")
        sha = entry.get("sha256")
        size = entry.get("size")
        if (not isinstance(rel, str) or not rel or "\\" in rel
                or PurePosixPath(rel).is_absolute()
                or any(part in ("", ".", "..") for part in rel.split("/"))
                or rel == "manifest.json" or rel in seen
                or not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None
                or type(size) is not int or size < 0):
            result["invalid"].append(f"invalid file entry: {rel!r}")
            continue
        seen.add(rel)
        path = root.joinpath(*rel.split("/"))
        if path.is_symlink():
            result["invalid"].append(f"symlink: {rel}")
        elif not path.is_file():
            result["missing"].append(rel)
        else:
            actual_sha = _sha256(path)
            actual_size = path.stat().st_size
            if actual_sha != sha or actual_size != size:
                result["mismatches"].append({
                    "path": rel, "expected_sha256": sha, "actual_sha256": actual_sha,
                    "expected_size": size, "actual_size": actual_size,
                })
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() or p.is_symlink()}
    result["unlisted"] = sorted(actual - seen - {"manifest.json"})
    result["file_count"] = len(seen)
    result["ok"] = not any(result[key] for key in ("mismatches", "missing", "unlisted", "invalid"))
    return result
