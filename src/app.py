import os
import httpx
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

load_dotenv()
VERIFY_TOKEN   = os.getenv("VERIFY_TOKEN", "")
WABA_TOKEN     = os.getenv("WABA_TOKEN", "")
PHONE_NUMBER_ID= os.getenv("PHONE_NUMBER_ID", "")
GRAPH_VERSION  = os.getenv("GRAPH_VERSION", "v20.0")
DRY_RUN        = os.getenv("DRY_RUN", "true").lower() == "true"

app = FastAPI()

@app.get("/webhook")
async def verify(mode: str | None = None,
                 hub_mode: str | None = None,
                 challenge: str | None = None,
                 hub_challenge: str | None = None,
                 verify_token: str | None = None,
                 hub_verify_token: str | None = None):
    _mode = hub_mode or mode
    _vt   = hub_verify_token or verify_token
    _ch   = hub_challenge or challenge
    if _mode == "subscribe" and _vt == VERIFY_TOKEN:
        return int(_ch) if _ch and _ch.isdigit() else (_ch or "")
    raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/webhook")
async def incoming(request: Request):
    body = await request.json()
    try:
        changes = body["entry"][0]["changes"][0]["value"]
        messages = changes.get("messages", [])
        if not messages:
            return {"status": "ok"}
        from_wa = messages[0]["from"]
        if DRY_RUN:
            print(f"[DRY_RUN] Enviaria 'oi' para {from_wa}")
            return {"status": "dry_ok"}
        url = f"https://graph.facebook.com/{GRAPH_VERSION}/{PHONE_NUMBER_ID}/messages"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={
                "messaging_product": "whatsapp",
                "to": from_wa,
                "type": "text",
                "text": {"body": "oi"}
            }, headers={"Authorization": f"Bearer {WABA_TOKEN}"})
            r.raise_for_status()
        return {"status": "sent"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))