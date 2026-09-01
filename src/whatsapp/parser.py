from typing import Any, Optional


def extract_value(payload: dict[str, Any]) -> dict[str, Any]:
    entry = (payload.get("entry") or [{}])[0]
    change = (entry.get("changes") or [{}])[0]
    return change.get("value") or {}


def extract_message(value: dict[str, Any]) -> Optional[dict[str, Any]]:
    msgs = value.get("messages") or []
    if not msgs:
        return None
    return msgs[0]


def extract_text(msg: dict[str, Any]) -> str:
    return ((msg.get("text") or {}).get("body") or "").strip()
