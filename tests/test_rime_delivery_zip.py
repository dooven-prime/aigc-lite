"""Host-side ZIP inspection precedes extraction and checker execution."""

from __future__ import annotations

import hashlib
import stat
import warnings
import zipfile
from pathlib import Path

import pytest

from scripts.verify_rime_replay_zip import (
    UnsafeDeliveryZipError,
    safely_extract_delivery,
)

ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "replay_packages" / "rime-consumer-r2.zip"
ZIP_SHA256 = "11f6c61f430e5adedbfc488b02765d146ca652afa277e4d952988e10702c84c6"
MANIFEST_SHA256 = "fd75add881becc2e37ceb79ed5999200132d90ed958f2ddb66df637b30ae46f6"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive(tmp_path: Path, bad: str | None = None) -> Path:
    archive = tmp_path / "delivery.zip"
    with zipfile.ZipFile(DELIVERY) as original, zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED
    ) as zipped:
        for info in original.infolist():
            zipped.writestr(info, original.read(info))
        if bad == "traversal":
            zipped.writestr("../escape", "bad")
        elif bad == "duplicate":
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                zipped.writestr("rime-consumer-v1-r2/manifest.json", b"wrong")
        elif bad == "symlink":
            info = zipfile.ZipInfo("rime-consumer-v1-r2/link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zipped.writestr(info, "../../escape")
        elif bad == "oversized":
            zipped.writestr("rime-consumer-v1-r2/extra.bin", b"0" * 10_000_001)
        elif bad == "unexpected":
            zipped.writestr("surprise.py", "print('bad')")
        elif bad == "unexpected-directory":
            zipped.writestr("rime-consumer-v1-r2/unused/", b"")
    return archive


def test_delivery_zip_extracts_without_executing_bundled_verifier(tmp_path: Path) -> None:
    archive = DELIVERY
    assert _sha256(archive) == ZIP_SHA256
    destination = tmp_path / "unpacked"
    result = safely_extract_delivery(archive, destination, _sha256(archive), MANIFEST_SHA256)
    assert result["executed"] is False
    assert result["bundled_verifier_trusted"] is False
    assert result["package_file_count"] == 26
    assert _sha256(destination / "rime-consumer-v1-r2" / "manifest.json") == MANIFEST_SHA256
    assert (destination / "verify_rime_replay_package.py").is_file()


@pytest.mark.parametrize(
    "bad", ["traversal", "duplicate", "symlink", "oversized", "unexpected", "unexpected-directory"],
)
def test_delivery_zip_rejects_hostile_members_before_extraction(tmp_path: Path, bad: str) -> None:
    archive = _archive(tmp_path, bad)
    destination = tmp_path / "unpacked"
    with pytest.raises(UnsafeDeliveryZipError):
        safely_extract_delivery(archive, destination, _sha256(archive), MANIFEST_SHA256)
    assert not destination.exists()
    assert not (tmp_path / "escape").exists()


def test_delivery_zip_requires_out_of_band_hashes(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    destination = tmp_path / "unpacked"
    with pytest.raises(UnsafeDeliveryZipError, match="ZIP differs"):
        safely_extract_delivery(archive, destination, "0" * 64, MANIFEST_SHA256)
    assert not destination.exists()
    with pytest.raises(UnsafeDeliveryZipError, match="manifest differs"):
        safely_extract_delivery(archive, destination, _sha256(archive), "0" * 64)
    assert not destination.exists()


def test_delivery_zip_never_overwrites_destination(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    destination = tmp_path / "unpacked"
    destination.mkdir()
    marker = destination / "preserve"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(UnsafeDeliveryZipError, match="must not already exist"):
        safely_extract_delivery(archive, destination, _sha256(archive), MANIFEST_SHA256)
    assert marker.read_text(encoding="utf-8") == "keep"
