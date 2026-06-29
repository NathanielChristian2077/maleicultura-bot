import time
from typing import Optional

from config import DEDUP_TTL_SEC


# ============================================================
# Deduplicação em memória (wamid)
# ============================================================

_seen_wamids: dict[str, float] = {}


def dedup_gc() -> None:
    now = time.time()
    expired = [k for k, ts in _seen_wamids.items() if now - ts > DEDUP_TTL_SEC]
    for k in expired:
        _seen_wamids.pop(k, None)


def seen_before(wamid: Optional[str]) -> bool:
    dedup_gc()

    if not wamid:
        return False

    now = time.time()
    if wamid in _seen_wamids:
        return True

    _seen_wamids[wamid] = now
    return False
