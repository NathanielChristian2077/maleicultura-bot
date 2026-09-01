import json
import time
from typing import Any, Optional

import boto3

from config import CONV_TABLE, DEDUP_TTL_SEC, STATE_TTL_SEC
from utils.logging import log

_ddb = boto3.client("dynamodb")

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


# ============================================================
# Estado do usuário (RAM + Dynamo fallback)
# ============================================================

_STATE_TS = 0
_user_state: dict[str, dict[str, Any]] = {}


def _ttl_epoch_seconds_in(sec: int) -> int:
    return int(time.time()) + int(sec)


def state_gc() -> None:
    now = time.time()
    expired = []

    for k, st in _user_state.items():
        ts = float(st.get("_ts", 0))
        if now - ts > STATE_TTL_SEC:
            expired.append(k)

    for k in expired:
        _user_state.pop(k, None)


def _ddb_get_state(wa_from: str) -> Optional[dict[str, Any]]:
    try:
        resp = _ddb.get_item(
            TableName=CONV_TABLE,
            Key={
                "wa_from": {"S": wa_from},
                "ts": {"N": str(_STATE_TS)},
            },
            ConsistentRead=True,
        )
        item = resp.get("Item")
        if not item:
            return None

        ttl = int(item.get("ttl", {}).get("N", "0") or "0")
        if ttl and ttl < int(time.time()):
            return None

        content = (item.get("content") or {}).get("S") or ""
        if not content:
            return None

        st = json.loads(content)
        if isinstance(st, dict):
            return st

        return None

    except Exception as e:
        log("ddb_get_state_err", error=str(e))
        return None


def _ddb_put_state(wa_from: str, st: dict[str, Any]) -> None:
    try:
        _ddb.put_item(
            TableName=CONV_TABLE,
            Item={
                "wa_from": {"S": wa_from},
                "ts": {"N": str(_STATE_TS)},
                "role": {"S": "state"},
                "content": {"S": json.dumps(st, ensure_ascii=False)},
                "ttl": {"N": str(_ttl_epoch_seconds_in(STATE_TTL_SEC))},
            },
        )
    except Exception as e:
        log("ddb_put_state_err", error=str(e))


def _ddb_delete_state(wa_from: str) -> None:
    try:
        _ddb.delete_item(
            TableName=CONV_TABLE,
            Key={
                "wa_from": {"S": wa_from},
                "ts": {"N": str(_STATE_TS)},
            },
        )
    except Exception as e:
        log("ddb_del_state_err", error=str(e))


def get_state(wa_from: str) -> dict[str, Any]:
    state_gc()

    st = _user_state.get(wa_from)
    if st:
        return st

    ddb_st = _ddb_get_state(wa_from)
    if ddb_st:
        ddb_st["_ts"] = time.time()
        _user_state[wa_from] = ddb_st
        return ddb_st

    return {"stage": "choose_llm"}


def set_state(wa_from: str, **kwargs: Any) -> None:
    state_gc()

    st = _user_state.get(wa_from) or {}
    st.update(kwargs)
    st["_ts"] = time.time()
    _user_state[wa_from] = st

    persist = {k: v for k, v in st.items() if k in ("stage", "llm", "mode")}
    _ddb_put_state(wa_from, persist)


def reset_state(wa_from: str) -> None:
    _user_state.pop(wa_from, None)
    _ddb_delete_state(wa_from)
