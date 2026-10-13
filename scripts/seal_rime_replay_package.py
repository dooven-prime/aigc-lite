"""Regenerate only the size/hash fields of a reviewed replay manifest.

This is a build utility, not a trust decision. The resulting manifest SHA-256
must be distributed out of band to a verifier before replay.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from verify_rime_replay_package import sha256_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    package = args.package.resolve()
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    listed = {entry["path"] for entry in manifest["files"]}
    actual = {
        path.relative_to(package).as_posix()
        for path in package.rglob("*") if path.is_file() or path.is_symlink()
    } - {"manifest.json"}
    if listed != actual:
        raise SystemExit(f"file inventory differs: missing={listed - actual}, unlisted={actual - listed}")
    for entry in manifest["files"]:
        path = package.joinpath(*entry["path"].split("/"))
        if path.is_symlink():
            raise SystemExit(f"symlink not allowed: {entry['path']}")
        entry["size"] = path.stat().st_size
        entry["sha256"] = sha256_file(path)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"manifest_sha256={sha256_file(manifest_path)}")


if __name__ == "__main__":
    main()
