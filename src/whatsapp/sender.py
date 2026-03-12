from typing import Any, Tuple

import httpx

from config import (
    MENU_BODY_MAX,
    MENU_ID_MAX,
    MENU_TITLE_MAX,
)
from utils.logging import log


def wa_api_url(C: dict[str, Any]) -> str:
    return f"https://graph.facebook.com/{C['GRAPH_VERSION']}/{C['PHONE_NUMBER_ID']}/messages"


def wa_headers(C: dict[str, Any]) -> dict[str, str]:
    token = (C.get("WABA_TOKEN") or "").strip()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


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
        "text": {"body": body},
    }
    resp = await client.post(api_url, json=payload, headers=headers)
    log("wa_out_text", status=resp.status_code, body=resp.text[:1500])
    return resp


async def wa_send_interactive_buttons(
    client: httpx.AsyncClient,
    api_url: str,
    headers: dict[str, str],
    to: str,
    body_text: str,
    buttons: list[Tuple[str, str]],
) -> httpx.Response:
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": (body_text or "")[:MENU_BODY_MAX]},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": bid[:MENU_ID_MAX],
                            "title": title[:MENU_TITLE_MAX],
                        },
                    }
                    for (bid, title) in buttons[:3]
                ]
            },
        },
    }

    resp = await client.post(api_url, json=payload, headers=headers)
    log("wa_out_interactive", status=resp.status_code, body=resp.text[:1500])
    return resp


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


def llm_label(llm_key: str) -> str:
    return {
        "gemini": "Gemini",
        "gpt": "ChatGPT",
        "deepseek": "DeepSeek",
    }.get((llm_key or "").strip().lower(), "—")


def mode_label(mode_key: str) -> str:
    mode_key = (mode_key or "").strip().lower()

    if mode_key == "normal":
        return "Normal"
    if mode_key == "rag":
        return "RAG (em breve)"
    if mode_key == "finetune":
        return "Fine-tuning (em breve)"

    return "—"


async def send_llm_menu(
    client: httpx.AsyncClient,
    api_url: str,
    headers: dict[str, str],
    wa_from: str,
) -> None:
    await wa_send_interactive_buttons(
        client,
        api_url,
        headers,
        wa_from,
        "Escolha o modelo que vai responder:",
        [
            ("llm_gemini", "Gemini"),
            ("llm_gpt", "ChatGPT"),
            ("llm_deepseek", "DeepSeek"),
        ],
    )


async def send_mode_menu(
    client: httpx.AsyncClient,
    api_url: str,
    headers: dict[str, str],
    wa_from: str,
) -> None:
    await wa_send_interactive_buttons(
        client,
        api_url,
        headers,
        wa_from,
        "Escolha o modo:",
        [
            ("mode_normal", "Normal"),
            ("mode_rag", "RAG"),
            ("mode_ft", "Fine-tuning"),
        ],
    )


async def send_ready_prompt(
    client: httpx.AsyncClient,
    api_url: str,
    headers: dict[str, str],
    wa_from: str,
    llm_key: str,
    mode_key: str,
) -> None:
    llm_show = llm_label(llm_key)
    mode_show = mode_label(mode_key)

    await wa_send_interactive_buttons(
        client,
        api_url,
        headers,
        wa_from,
        f"Fechado.\nLLM: {llm_show}\nModo: {mode_show}\n\nAgora envie sua pergunta.",
        [
            ("menu_reset", "Alterar modelo"),
            ("menu_help", "Ajuda"),
        ],
    )
