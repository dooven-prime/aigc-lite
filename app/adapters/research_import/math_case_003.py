"""Pinned source bytes for one OpenAI mathematics theorem, not a trust assertion.

The expected hashes are a local baseline of the fetched bytes. They do not
authenticate the Git tree, GitHub, the paper's mathematical content, or its
agreement with the Lean challenge.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

import httpx

from ...core.errors import InvalidEvidenceError, UpstreamRequestError

SOURCE_COMMIT = "adc7f1241b42e322a6451854ab7e4b4c146bf78a"
SOURCE_REPOSITORY = "openai/math"
THEOREM_NAME = "OAI.riemannZeta_ne_zero_of_seven_eighths_lt_re"
PDF_PATH = "preprints/The-Quasi-Riemann-Hypothesis-September-30-2026/paper.pdf"
CHALLENGE_PATH = "lean/ComparatorChallenges/QuasiRiemannHypothesis.lean"
CONFIG_PATH = "lean/ComparatorChallenges/QuasiRiemannHypothesis.json"
SOLUTION_PATH = "lean/OAI/NumberTheory/DirichletL/Nonvanishing.lean"
TOOLCHAIN_PATH = "lean/lean-toolchain"
MANIFEST_PATH = "lean/lake-manifest.json"
LAKEFILE_PATH = "lean/lakefile.lean"

# SHA-256 of raw, unmodified responses for this exact commit, not Git signatures.
PINNED_FILES = {
    PDF_PATH: "8fe93046f8cf5ef1ba5969c89addc02d76311adc4ee907509ff9cd96f7ec99e7",
    "lean/docs/003.md": "41e52ed718260e6feb1db218f8e97287e703cb63c6f16e6c36f5563e6e2395ce",
    CHALLENGE_PATH: "065f8c9a01d28db78c8b1bfc5083b535082a2d2aac2230d563f98b8caa812cc5",
    CONFIG_PATH: "46ebb7edc11f69536210f502b4bcbd36bbaf3f0872c16748b6c8ce43991bf320",
    SOLUTION_PATH: "d98c4a7e074469b429890e1c21b8cc76c410026525a776895293cdc0f9e6ae89",
    TOOLCHAIN_PATH: "d5edba4e4b8faad9c1baeadb265716d20d03be4d1a2647dc5e35b0c0325bea7b",
    MANIFEST_PATH: "cf6105a25d9dca2f166b241d9191bd12c7e13305890dc9c4d0952351cccc0794",
    LAKEFILE_PATH: "4cca977ebece444b6c999d99755c1ad3c93119bafc127ea761f7f703c7279e74",
    "lean/patches/AINTLIB-lean4341.patch": "076bee4cee068ad938e611d266bb32f7f1798189336087e88c08c8b3ac58dff1",
    "lean/patches/AbsorptionCutoff-lean4341.patch": "8b35104dc12f807f594f7a61f7a183d967edcd0e601c7172e0e851e4c3adb3ba",
    "lean/patches/ClassFieldTheory-lean4341.patch": "0275686e2febd9c14523a94cbfae105e6cf30997ed0b2a1ea28c84d0d0c12ba5",
    "lean/patches/PrimeNumberTheoremAnd-lean4341.patch": "0890432340c0025973bcfe070b634355fa366cc831595f68526e4c73c8903d82",
    "lean/patches/SphereEversion-lean4341.patch": "5c7c8dba85e4636ed2fec14a5765e937f2b1c428eadb12168645b9d2c0046098",
    "lean/patches/StrongPNT-lean4341.patch": "2cac5cbef22987314f9293bebcf243d0a3341caaf0227925d1f8d4c31be924d7",
    "lean/patches/Zeta3Irrational-lean4341.patch": "169eb72e9d12740688467a0e6abf63923a60ec5f7ce8f49ca4b3684a22418901",
    "lean/patches/belyi-lean4341.patch": "f54c2e211464c0f8194ce63a80a6562489f23f688b1c233efe3c2debfc1b9f66",
    "lean/patches/carleson-lean4341.patch": "8aa67adaeb6ef56967aa78c47a6c743f03ac72c125f503219a72dee5be23168c",
    "lean/patches/elliptic-curves-lean4341.patch": "69e6575dae5797d43242694dc737b400b3e1e8fcb854094fead2a204a1f58632",
    "lean/patches/fixed-point-theorems-lean4341.patch": "d70872e41e80b25191538d1659c4aa3a001349b5bd16f806c9e3833a502516e5",
    "lean/patches/formal-schemes-lean4341.patch": "5376716aa94fe0c6907570d650dd6604f170687c218cea7028d09b9f95877cea",
    "lean/patches/genl-lean4341.patch": "3a9edd7dd539e98f81145a3b6cc980c9d62a821b7d41e2591f9aa5c557a7b6fa",
    "lean/patches/gromov-lean4341.patch": "26a1924bef774183a2e0865f144b782dc1f78eb0246993a31a5d63193b7781bb",
    "lean/patches/heights-lean4341.patch": "bd3b58a301df273a199d9a0fe486b25d194df176afe32583289d42b64ab583a3",
    "lean/patches/iut-lean4341.patch": "610ef069f7de5a5d500ead287287b60f193269ea4ddc0011101a9c7c98356dd4",
    "lean/patches/oka-lean4341.patch": "02995b860e7d566a10adc3488439d38abc06dcb317ae4576d504c71c921816e7",
    "lean/patches/orbicurve-cores-lean4341.patch": "4376110b9b7c823057cda21479a45de688c5138c81e5d4f1c050b632f168b93d",
    "lean/patches/pi1-lean4341.patch": "816e0d8123d5622c5b3415675638053ae31126a829e4f21d96f7af28db292f5f",
    "lean/patches/rellich-kondrachov-lean4341.patch": "0c121d8517dfeadc93ef24fe2deb56beec7cb3998e2552dee8f61c780b1408c8",
    "lean/patches/schoenflies-lean-lean4341.patch": "4cbc255a2be785e6b617ecd61cdaf67e6d236d65a87f284380f2713670c8e350",
    "lean/patches/tate-curves-theta-lean4341.patch": "885a19a0dc2d004aaece1987e2b7d2ff4745aef8881cbc53971a852cc5644c3f",
    "lean/patches/tempered-fundamental-groups-lean4341.patch": "f2e51b5f38459f11515c21801c92c39de85a8feb5f2b490ebbe07a93511a8860",
}


@dataclass(frozen=True, slots=True)
class PinnedSourceFile:
    path: str
    sha256: str
    content: bytes


class OpenAIMathCase003Source:
    """Bounded fetch of a fixed allowlist at a fixed commit, with no redirects."""

    def fetch(self, client: httpx.Client | None = None) -> tuple[PinnedSourceFile, ...]:
        if client is None:
            with httpx.Client(timeout=60, follow_redirects=False) as owned:
                return self._fetch(owned)
        return self._fetch(client)

    @staticmethod
    def _fetch(client: httpx.Client) -> tuple[PinnedSourceFile, ...]:
        files = []
        for path, expected in PINNED_FILES.items():
            url = f"https://raw.githubusercontent.com/{SOURCE_REPOSITORY}/{SOURCE_COMMIT}/{path}"
            limit = 3_000_000 if path == PDF_PATH else (
                2_500_000 if path.startswith("lean/patches/") else 100_000
            )
            try:
                with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        raise UpstreamRequestError("Pinned math source is unavailable")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > limit:
                            raise InvalidEvidenceError(
                                "source", "Pinned math source exceeds size limit"
                            )
            except httpx.HTTPError as exc:
                raise UpstreamRequestError("Pinned math source fetch failed") from exc
            payload = bytes(content)
            digest = hashlib.sha256(payload).hexdigest()
            if digest != expected:
                raise InvalidEvidenceError("source", f"Pinned source hash mismatch: {path}")
            if path == PDF_PATH and not payload.startswith(b"%PDF-"):
                raise InvalidEvidenceError("source", "Pinned PDF has an invalid header")
            files.append(PinnedSourceFile(path, digest, payload))
        return tuple(files)


def validate_snapshot_files(files: object, artifacts: dict[str, dict]) -> bool:
    """Recompute raw-byte hashes from persisted chunk Artifacts, not metadata."""

    if not isinstance(files, list) or len(files) != len(PINNED_FILES):
        return False
    by_path = {item.get("path"): item for item in files if isinstance(item, dict)}
    if set(by_path) != set(PINNED_FILES):
        return False
    for path, expected in PINNED_FILES.items():
        entry = by_path[path]
        identifiers = entry.get("artifact_ids")
        if not isinstance(identifiers, list) or not identifiers:
            return False
        chunks = []
        for index, artifact_id in enumerate(identifiers):
            artifact = artifacts.get(artifact_id)
            if not artifact or artifact.get("metadata", {}).get("role") != "source_snapshot_file":
                return False
            meta = artifact["metadata"]
            if meta.get("source_path") != path or meta.get("chunk_index") != index:
                return False
            content = artifact.get("content_text")
            if not isinstance(content, str):
                return False
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != artifact.get("content_hash"):
                return False
            try:
                chunk = base64.b64decode(content, validate=True)
            except (ValueError, UnicodeError):
                return False
            if hashlib.sha256(chunk).hexdigest() != meta.get("raw_chunk_sha256"):
                return False
            chunks.append(chunk)
        whole = b"".join(chunks)
        if (
            len(whole) != entry.get("size_bytes")
            or hashlib.sha256(whole).hexdigest() != expected
            or entry.get("sha256") != expected
        ):
            return False
    return True
