from typing import Any

import httpx

from config import MAX_WA_TEXT, clean_token
from utils.logging import log


WA_TEXT_SAFE_CHUNK = 3500


def wa_api_url(C: dict[str, Any]) -> str:
    return f"https://graph.facebook.com/{C['GRAPH_VERSION']}/{C['PHONE_NUMBER_ID']}/messages"


def wa_headers(C: dict[str, Any]) -> dict[str, str]:
    token = clean_token(C.get("WABA_TOKEN") or "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def split_text_for_whatsapp(text: str, chunk_size: int = WA_TEXT_SAFE_CHUNK) -> list[str]:
    text = (text or "").strip()
    if not text:
        return [""]

    parts: list[str] = []
    remaining = text

    while len(remaining) > chunk_size:
        cut = -1

        for sep in ("\n\n", "\n", " "):
            pos = remaining.rfind(sep, 0, chunk_size)
            if pos >= int(chunk_size * 0.6):
                cut = pos + len(sep)
                break

        if cut == -1:
            cut = chunk_size

        part = remaining[:cut].strip()
        if part:
            parts.append(part)
        remaining = remaining[cut:].strip()

    if remaining:
        parts.append(remaining)

    return parts


async def wa_send_text(
    client: httpx.AsyncClient,
    api_url: str,
    headers: dict[str, str],
    to: str,
    body: str,
) -> httpx.Response:
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body[:MAX_WA_TEXT]},
    }
    resp = await client.post(api_url, json=payload, headers=headers)
    log(
        "wa_out_text",
        status=resp.status_code,
        text_len=len(body or ""),
        body=resp.text[:1500],
    )
    return resp


async def wa_send_text_chunks(
    client: httpx.AsyncClient,
    api_url: str,
    headers: dict[str, str],
    to: str,
    body: str,
) -> list[httpx.Response]:
    parts = split_text_for_whatsapp(body)
    responses: list[httpx.Response] = []

    for idx, part in enumerate(parts, start=1):
        log("wa_out_text_chunk", chunk_index=idx, chunk_total=len(parts), text_len=len(part))
        resp = await wa_send_text(client, api_url, headers, to, part)
        responses.append(resp)

        if not resp.is_success:
            break

    return responses


async def wa_mark_read(
    client: httpx.AsyncClient,
    api_url: str,
    headers: dict[str, str],
    wamid: str,
) -> None:
    try:
        resp = await client.post(
            api_url,
            headers=headers,
            json={
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": wamid,
            },
        )
        log("wa_feedback_read", status=resp.status_code, body=resp.text[:600])
    except Exception as e:
        log("wa_feedback_err_read", error=str(e))


async def wa_typing_and_read(
    client: httpx.AsyncClient,
    api_url: str,
    headers: dict[str, str],
    wamid: str,
) -> None:
    try:
        resp = await client.post(
            api_url,
            headers=headers,
            json={
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": wamid,
                "typing_indicator": {"type": "text"},
            },
        )
        log("wa_feedback_typing_read", status=resp.status_code, body=resp.text[:600])
    except Exception as e:
        log("wa_feedback_err_typing_read", error=str(e))
