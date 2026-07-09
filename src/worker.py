import asyncio
import threading
import time
from typing import Any

import httpx

from config import cfg
from services.flow import handle_interactive, handle_text_message
from utils.logging import log
from whatsapp.sender import wa_api_url, wa_headers, wa_typing_and_read


TYPING_REFRESH_SECONDS = 12
TYPING_INITIAL_DELAY_SECONDS = 8


async def _send_typing_once(
    api_url: str,
    headers: dict[str, str],
    wamid: str,
) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        await wa_typing_and_read(client, api_url, headers, wamid)


def _typing_heartbeat_thread(
    api_url: str,
    headers: dict[str, str],
    wamid: str | None,
    stop_event: threading.Event,
) -> None:
    """
    Mantém o indicador de 'digitando' vivo em uma thread separada.

    Motivo: o processamento principal pode bloquear o event loop por causa de
    Chroma, chamadas de rede, SDKs ou qualquer outra maravilha moderna.
    A thread evita que o heartbeat dependa do loop principal continuar respirando.
    """
    if not wamid:
        log("typing_heartbeat_skip", reason="missing_wamid")
        return

    headers_copy = dict(headers)

    log(
        "typing_heartbeat_started",
        wamid=wamid,
        refresh_seconds=TYPING_REFRESH_SECONDS,
        initial_delay_seconds=TYPING_INITIAL_DELAY_SECONDS,
    )

    if stop_event.wait(TYPING_INITIAL_DELAY_SECONDS):
        log("typing_heartbeat_stopped_before_first_refresh", wamid=wamid)
        return

    while not stop_event.is_set():
        try:
            asyncio.run(_send_typing_once(api_url, headers_copy, wamid))
            log("typing_heartbeat_sent", wamid=wamid)

        except Exception as exc:
            log(
                "typing_heartbeat_exception",
                wamid=wamid,
                error_type=type(exc).__name__,
                error=str(exc),
            )

        stop_event.wait(TYPING_REFRESH_SECONDS)

    log("typing_heartbeat_stopped", wamid=wamid)


async def _run_with_typing(
    api_url: str,
    headers: dict[str, str],
    wamid: str | None,
    coro,
):
    stop_event = threading.Event()

    heartbeat_thread = threading.Thread(
        target=_typing_heartbeat_thread,
        args=(api_url, headers, wamid, stop_event),
        daemon=True,
    )

    heartbeat_thread.start()

    try:
        return await coro

    finally:
        stop_event.set()

        await asyncio.to_thread(heartbeat_thread.join, 2)


async def _run_worker(event: dict[str, Any]) -> dict[str, Any]:
    kind = event.get("kind")
    wa_from = event.get("wa_from") or ""
    wamid = event.get("wamid")
    msg = event.get("msg") or {}

    C = cfg()
    api_url = wa_api_url(C)
    headers = wa_headers(C)

    log("worker_started", kind=kind, wa_from=wa_from, wamid=wamid)

    async with httpx.AsyncClient(timeout=30) as client:
        if kind == "text":
            text = event.get("text") or ""

            if not text:
                log("worker_ignored", reason="empty_text", wa_from=wa_from)
                return {"status": "ignored", "reason": "empty_text"}

            result = await _run_with_typing(
                api_url,
                headers,
                wamid,
                handle_text_message(
                    client,
                    C,
                    api_url,
                    headers,
                    wa_from,
                    wamid,
                    text,
                ),
            )

        elif kind == "interactive":
            result = await _run_with_typing(
                api_url,
                headers,
                wamid,
                handle_interactive(
                    client,
                    api_url,
                    headers,
                    wa_from,
                    wamid,
                    msg,
                ),
            )

        else:
            log("worker_ignored", reason="unknown_kind", kind=kind)
            return {"status": "ignored", "reason": "unknown_kind"}

    log("worker_done", kind=kind, wa_from=wa_from, wamid=wamid)
    return {"status": "ok", "result": result}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        return asyncio.run(_run_worker(event))

    except Exception as exc:
        log(
            "worker_exception",
            kind=event.get("kind") if isinstance(event, dict) else None,
            wa_from=event.get("wa_from") if isinstance(event, dict) else None,
            wamid=event.get("wamid") if isinstance(event, dict) else None,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise