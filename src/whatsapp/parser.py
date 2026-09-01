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


def extract_button_id(msg: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    inter = msg.get("interactive") or {}
    br = inter.get("button_reply") or {}
    if br.get("id") or br.get("title"):
        return br.get("id"), br.get("title")

    btn = msg.get("button") or {}
    if btn.get("payload") or btn.get("text"):
        return btn.get("payload"), btn.get("text")

    return None, None
