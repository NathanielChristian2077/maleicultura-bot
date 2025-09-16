import json
from fastapi.testclient import TestClient
from app import app
import respx
import httpx
import os

client = TestClient(app)

def test_verify_ok():
    r = client.get("/webhook", params={
        "hub.mode":"subscribe",
        "hub.verify_token": os.getenv("VERIFY_TOKEN", "token_verificacao"),
        "hub.challenge":"42"
    })
    assert r.status_code == 200
    assert r.text == "42"

def test_incoming_dry_run(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    payload = {
        "object":"whatsapp_business_account",
        "entry":[{"changes":[{"value":{"messages":[{"from":"5511999999999","type":"text","text":{"body":"oi"}}]}}]}]
    }
    r = client.post("/webhook", json=payload)
    assert r.status_code == 200
    assert r.json()["status"] in ("dry_ok","ok")

@respx.mock
def test_incoming_real_sends(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GRAPH_VERSION", "v20.0")
    monkeypatch.setenv("PHONE_NUMBER_ID", "123")
    api = respx.post("https://graph.facebook.com/v20.0/123/messages").mock(
        return_value=httpx.Response(200, json={"messages":[{"id":"wamid.X"}]})
    )
    payload = {
        "object":"whatsapp_business_account",
        "entry":[{"changes":[{"value":{"messages":[{"from":"5511888888888","type":"text","text":{"body":"oi"}}]}}]}]
    }
    r = client.post("/webhook", json=payload)
    assert r.status_code == 200
    assert r.json()["status"] == "sent"
    assert api.called