from fastapi import FastAPI, Request, Query
from mangum import Mangum
import os, json, httpx, boto3

app = FastAPI()

WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
WHATSAPP_TOKEN_PARAM = os.getenv("WHATSAPP_TOKEN_PARAM", "/maleicultura/whatsapp_token")

_client: httpx.AsyncClient | None = None
_WHATSAPP_TOKEN: str | None = None

@app.on_event("startup")
async def _startup():
    global _client, _WHATSAPP_TOKEN
    _client = httpx.AsyncClient(timeout=10)

    ssm = boto3.client("ssm")
    resp = ssm.get_parameter(Name=WHATSAPP_TOKEN_PARAM, WithDecryption=True)
    _WHATSAPP_TOKEN = resp["Parameter"]["Value"]

@app.on_event("shutdown")
async def _shutdown():
    global _client
    if _client:
        await _client.aclose()

@app.get("/")
def raiz():
    return {"mensagem": "oi"}

@app.get("/webhook")
def verificar(
    mode: str | None = Query(None, alias="hub.mode"),
    challenge: str | None = Query(None, alias="hub.challenge"),
    verify_token: str | None = Query(None, alias="hub.verify_token"),
):
    if verify_token and verify_token == WHATSAPP_VERIFY_TOKEN:
        return int(challenge or "0")
    return {"status": "forbidden"}

@app.post("/webhook")
async def receber(request: Request):
    body = await request.json()
    try:
        entry = body.get("entry", [])[0]
        change = entry.get("changes", [])[0]
        value = change.get("value", {})
        messages = value.get("messages", [])
        if not messages:
            return {"status": "ignored"}

        msg = messages[0]
        from_wa = msg.get("from")
        await enviar_texto_whatsapp(from_wa, "oi")
    except Exception as e:
        print("Erro processando:", e, "payload:", json.dumps(body)[:1500])
    return {"status": "ok"}

async def enviar_texto_whatsapp(to: str, text: str):
    global _client, _WHATSAPP_TOKEN
    if not (_WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID):
        print("Config faltando: token ou phone_number_id")
        return

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {_WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    assert _client is not None
    r = await _client.post(url, headers=headers, json=payload)
    if r.status_code >= 400:
        print("Erro WhatsApp:", r.status_code, r.text)

handler = Mangum(app)
