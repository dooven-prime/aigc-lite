"""Document chunking and dependency-light vector operations.

The default embedding is a deterministic hashing vector so local deployments
work without another service. A provider embedding adapter can replace
``embed_text`` later without changing the repository API.
"""

from __future__ import annotations

import hashlib
import math
import re


def chunk_text(text: str, size: int = 1200, overlap: int = 150) -> list[str]:
    """Split text into bounded overlapping chunks while preferring paragraphs."""
    if size < 100 or overlap < 0 or overlap >= size:
        raise ValueError("size must be >= 100 and overlap must be in [0, size)")
    normalized = re.sub(r"\r\n?", "\n", text).strip()
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        if end < len(normalized):
            boundary = max(normalized.rfind("\n\n", start, end), normalized.rfind("\n", start, end))
            if boundary > start + size // 2:
                end = boundary
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - overlap, start + 1)
    return chunks


def embed_text(text: str, dimensions: int = 128) -> list[float]:
    """Create a deterministic normalized hashing vector for local retrieval."""
    if dimensions < 8:
        raise ValueError("dimensions must be at least 8")
    vector = [0.0] * dimensions
    tokens = re.findall(r"[\w]+", text.lower(), flags=re.UNICODE)
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Return cosine similarity for two vectors."""
    if len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))
