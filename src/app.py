import os
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, Response
from mangum import Mangum

app = FastAPI()

def env(name: str, default: str = "") -> str:
    fallback_map = {
        "WHATSAPP_VERIFY_TOKEN": ["VERIFY_TOKEN"],
        "WHATSAPP_TOKEN": ["WABA_TOKEN"],
        "WHATSAPP_PHONE_NUMBER_ID": ["PHONE_NUMBER_ID"],
        "GRAPH_API_VERSION": ["GRAPH_VERSION"],
        "DRY_RUN": [],
    }
    val = os.getenv(name)
    if val is None:
        for fb in fallback_map.get(name, []):
            val = os.getenv(fb)
            if val is not None:
                break
    return val if val is not None else default

def cfg():
    raw = env("WHATSAPP_TOKEN")
    clean = (raw or "").replace("\r", "").replace("\n", "").strip().strip('"').strip("'")
    return {
        "VERIFY_TOKEN": env("WHATSAPP_VERIFY_TOKEN"),
        "WABA_TOKEN": clean,
        "PHONE_NUMBER_ID": env("WHATSAPP_PHONE_NUMBER_ID"),
        "GRAPH_VERSION": env("GRAPH_API_VERSION", "v20.0"),
        "DRY_RUN": env("DRY_RUN", "false").lower() == "true",
    }

@app.get("/webhook")
async def verify(request: Request):
    C = cfg()
    qp = request.query_params

    mode = qp.get("hub.mode") or qp.get("mode")
    verify_token = qp.get("hub.verify_token") or qp.get("verify_token")
    challenge = qp.get("hub.challenge") or qp.get("challenge")

    if mode == "subscribe" and verify_token == C["VERIFY_TOKEN"] and challenge:
        return PlainTextResponse(challenge)

    return Response(status_code=403)

@app.post("/webhook")
async def incoming(request: Request):
    C = cfg()
    try:
        raw = await request.body()
        print({"type": "wa_inbound_raw", "len": len(raw)})

        body = await request.json()
        print({"type": "wa_inbound_parsed", "keys": list(body.keys())})

        entry = (body.get("entry") or [{}])[0]
        change = (entry.get("changes") or [{}])[0]
        value  = change.get("value") or {}

        if not value.get("messages"):
            print({"type": "wa_inbound_skip", "reason": "no_messages_key"})
            return {"status": "ok"}

        msg = value["messages"][0]
        wa_type = msg.get("type")
        wa_from = msg.get("from")
        is_echo = msg.get("from") == (value.get("metadata") or {}).get("display_phone_number")
        if is_echo:
            print({"type":"wa_inbound_skip", "reason":"echo"})
            return {"status": "ok"}

        print({"type":"wa_inbound_message", "from": wa_from, "msg_type": wa_type})

        if C["DRY_RUN"]:
            print({"type":"wa_outbound_dry_run", "to": wa_from, "text": "oi"})
            return {"status": "dry_ok"}

        url = f"https://graph.facebook.com/{C['GRAPH_VERSION']}/{C['PHONE_NUMBER_ID']}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": wa_from,
            "type": "text",
            "text": {"body": "oi"},
        }
        token = (C["WABA_TOKEN"] or "")
        token = token.replace("\r", "").replace("\n", "").strip().strip('"').strip("'")

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=headers)

        txt = resp.text[:2000]
        print({"type":"wa_outbound_resp", "status": resp.status_code, "body": txt})

        if resp.is_success:
            return {"status": "sent"}

        return {"status": "error", "code": resp.status_code}

    except Exception as e:
        print({"type":"wa_exception", "error": str(e)})
        return {"status": "exception"}

handler = Mangum(app)
