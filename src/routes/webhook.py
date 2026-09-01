import json

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

from config import cfg
from utils.logging import log
from services.state import seen_before
from services.flow import handle_interactive, handle_text_message
from whatsapp.parser import extract_value, extract_message, extract_text
from whatsapp.sender import wa_api_url, wa_headers


router = APIRouter()


@router.get("/webhook")
async def verify(request: Request):
    C = cfg()
    qp = request.query_params

    mode = qp.get("hub.mode") or qp.get("mode")
    verify_token = qp.get("hub.verify_token") or qp.get("verify_token")
    challenge = qp.get("hub.challenge") or qp.get("challenge")

    if mode == "subscribe" and verify_token == C["VERIFY_TOKEN"] and challenge:
        return PlainTextResponse(challenge)

    return Response(status_code=403)


@router.post("/webhook")
async def incoming(request: Request):
    C = cfg()

    # soft fail pra evitar retry infinito
    if not C.get("PHONE_NUMBER_ID") or not C.get("WABA_TOKEN"):
        log(
            "cfg_missing",
            phone_number_id=bool(C.get("PHONE_NUMBER_ID")),
            token=bool(C.get("WABA_TOKEN")),
        )
        return {"status": "ok", "warning": "missing_config"}

    api_url = wa_api_url(C)
    headers = wa_headers(C)

    try:
        raw = await request.body()
        log("wa_inbound_raw", len=len(raw))

        try:
            body = json.loads(
                raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
            )
        except Exception:
            body = await request.json()

        log(
            "wa_inbound_parsed",
            keys=list(body.keys()) if isinstance(body, dict) else "non_dict",
        )

        value = extract_value(body if isinstance(body, dict) else {})

        if not value.get("messages"):
            statuses = value.get("statuses") or []
            log("wa_inbound_status", count=len(statuses), statuses=statuses[:3])
            return {"status": "ok"}

        msg = extract_message(value)
        if not msg:
            log("wa_inbound_skip", reason="messages_empty_after_check")
            return {"status": "ok"}

        wa_from = msg.get("from") or ""
        wa_type = msg.get("type") or ""

        # best-effort echo detection
        try:
            meta = value.get("metadata") or {}
            display_phone = meta.get("display_phone_number")
            if display_phone and wa_from == display_phone:
                log("wa_inbound_skip", reason="echo_like")
                return {"status": "ok"}
        except Exception:
            pass

        wamid = msg.get("id") or msg.get("wamid")
        if seen_before(wamid):
            log("wa_inbound_skip", reason="duplicate_wamid", wamid=wamid)
            return {"status": "ok"}

        log("wa_inbound_message", from_=wa_from, msg_type=wa_type)

        async with httpx.AsyncClient(timeout=30) as client:
            if wa_type in ("interactive", "button"):
                return await handle_interactive(
                    client,
                    api_url,
                    headers,
                    wa_from,
                    wamid,
                    msg,
                )

            if wa_type == "text":
                text = extract_text(msg)
                if not text:
                    return {"status": "ok"}

                return await handle_text_message(
                    client,
                    C,
                    api_url,
                    headers,
                    wa_from,
                    wamid,
                    text,
                )

            log("wa_inbound_skip", reason="unsupported_type", wa_type=wa_type)
            return {"status": "ok"}

    except Exception as e:
        log("wa_exception", error=str(e))
        return {"status": "exception"}
