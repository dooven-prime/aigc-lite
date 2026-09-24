"""Small in-process fixed-window limiter for the single-process default."""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException

from .config import settings

_counts: dict[tuple[str, int], int] = defaultdict(int)
_lock = Lock()


def check_rate_limit(tenant_id: str) -> None:
    now = int(time.time() // 60)
    key = (tenant_id, now)
    with _lock:
        _counts[key] += 1
        for old_key in tuple(_counts):
            if old_key[1] < now - 1:
                del _counts[old_key]
        if _counts[key] > settings.rate_limit_per_minute:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
