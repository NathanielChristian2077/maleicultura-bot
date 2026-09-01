from typing import Any, Optional

import httpx

from services.llm import handler_gpt5_rag
from utils.logging import log
from utils.text import clean_reply_text
from whatsapp.sender import wa_send_text_chunks, wa_typing_and_read


async def handle_interactive(
    client: httpx.AsyncClient,
    api_url: str,
    headers: dict[str, str],
    wa_from: str,
    wamid: Optional[str],
    msg: dict[str, Any],
) -> dict[str, Any]:
    log("wa_interactive_obsolete", wa_from=wa_from, msg_type=msg.get("type", ""))
    return {"status": "interactive_obsolete"}


async def handle_text_message(
    client: httpx.AsyncClient,
    C: dict[str, Any],
    api_url: str,
    headers: dict[str, str],
    wa_from: str,
    wamid: Optional[str],
    text: str,
) -> dict[str, Any]:
    if wamid:
        await wa_typing_and_read(client, api_url, headers, wamid)

    user_text = (text or "").strip()
    if not user_text:
        return {"status": "empty_text"}

    reply_text = await handler_gpt5_rag(wa_from, user_text)

    if C.get("DRY_RUN"):
        log("wa_outbound_dry_run", to=wa_from, text=(reply_text or "")[:500])
        return {"status": "dry_ok"}

    reply_text = clean_reply_text(reply_text)
    responses = await wa_send_text_chunks(client, api_url, headers, wa_from, reply_text)

    ok = all(r.is_success for r in responses)
    return {
        "status": "sent" if ok else "error",
        "chunks": len(responses),
        "code": responses[-1].status_code if responses else 500,
    }
