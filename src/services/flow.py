from typing import Any, Optional

import httpx

from utils.logging import log
from utils.text import clean_reply_text
from services.state import get_state, set_state, reset_state
from services.llm import route_llm, handler_gemini, handler_gpt, handler_deepseek
from whatsapp.parser import extract_button_id
from whatsapp.sender import (
    wa_send_text,
    wa_send_interactive_buttons,
    wa_typing_and_read,
    send_llm_menu,
    send_mode_menu,
    send_ready_prompt,
)


async def handle_interactive(
    client: httpx.AsyncClient,
    api_url: str,
    headers: dict[str, str],
    wa_from: str,
    wamid: Optional[str],
    msg: dict[str, Any],
) -> dict[str, Any]:
    button_id, button_title = extract_button_id(msg)
    log("wa_button_click", wa_from=wa_from, id=button_id, title=button_title)

    if wamid:
        await wa_typing_and_read(client, api_url, headers, wamid)

    if not button_id:
        await wa_send_interactive_buttons(
            client,
            api_url,
            headers,
            wa_from,
            "Algo parece ter dado errado na seleção. Tente novamente.",
            [
                ("menu_reset", "Reiniciar"),
                ("menu_help", "Ajuda"),
            ],
        )
        return {"status": "ok_no_button_id"}

    if button_id in ("llm_gemini", "llm_gpt", "llm_deepseek"):
        llm = {
            "llm_gemini": "gemini",
            "llm_gpt": "gpt",
            "llm_deepseek": "deepseek",
        }[button_id]

        set_state(wa_from, stage="choose_mode", llm=llm)
        await send_mode_menu(client, api_url, headers, wa_from)
        return {"status": "ok_llm_selected"}

    if button_id in ("mode_normal", "mode_rag", "mode_ft"):
        mode = {
            "mode_normal": "normal",
            "mode_rag": "rag",
            "mode_ft": "finetune",
        }[button_id]

        st = get_state(wa_from)
        if not st.get("llm"):
            reset_state(wa_from)
            await send_llm_menu(client, api_url, headers, wa_from)
            return {"status": "need_llm_first"}

        set_state(wa_from, stage="ready", mode=mode)
        st = get_state(wa_from)

        await send_ready_prompt(
            client,
            api_url,
            headers,
            wa_from,
            st.get("llm", ""),
            st.get("mode", ""),
        )
        return {"status": "ok_mode_selected"}

    if button_id == "menu_reset":
        reset_state(wa_from)
        await send_llm_menu(client, api_url, headers, wa_from)
        return {"status": "ok_reset"}

    if button_id == "menu_help":
        await wa_send_interactive_buttons(
            client,
            api_url,
            headers,
            wa_from,
            "Passo a passo:\n1) Escolher LLM\n2) Escolher modo\n3) Enviar pergunta",
            [
                ("menu_reset", "Reset"),
                ("menu_keep", "Manter escolhas"),
            ],
        )
        return {"status": "ok_help"}

    if button_id == "menu_keep":
        st = get_state(wa_from)
        await send_ready_prompt(
            client,
            api_url,
            headers,
            wa_from,
            st.get("llm", ""),
            st.get("mode", ""),
        )
        return {"status": "ok_keep"}

    await wa_send_interactive_buttons(
        client,
        api_url,
        headers,
        wa_from,
        "Seleção inválida. Por favor, tente novamente.",
        [
            ("menu_reset", "Reiniciar"),
            ("menu_help", "Ajuda"),
            ("menu_keep", "Manter escolha"),
        ],
    )
    return {"status": "ok_unknown_button"}


async def handle_text_message(
    client: httpx.AsyncClient,
    C: dict[str, Any],
    api_url: str,
    headers: dict[str, str],
    wa_from: str,
    wamid: Optional[str],
    text: str,
) -> dict[str, Any]:
    st = get_state(wa_from)

    # Gate: força o usuário a concluir o fluxo antes de perguntar
    if text and st.get("stage") != "ready":
        if st.get("stage") == "choose_mode" and st.get("llm"):
            await wa_send_text(
                client,
                api_url,
                headers,
                wa_from,
                "Antes eu preciso que você escolha o modo no menu abaixo.",
            )
            await send_mode_menu(client, api_url, headers, wa_from)
            return {"status": "need_mode"}

        await wa_send_text(
            client,
            api_url,
            headers,
            wa_from,
            "Antes eu preciso que você escolha o modelo no menu abaixo.",
        )
        await send_llm_menu(client, api_url, headers, wa_from)
        return {"status": "need_llm"}

    if wamid:
        await wa_typing_and_read(client, api_url, headers, wamid)

    prefix = text[:1] if text else ""
    user_text = text[1:].lstrip() if len(text) > 1 else text

    if prefix == "@":
        reply_text = await handler_gemini(wa_from, user_text)
    elif prefix == "$":
        reply_text = await handler_gpt(wa_from, user_text)
    elif prefix == "&":
        reply_text = await handler_deepseek(wa_from, user_text)
    else:
        llm_key = (get_state(wa_from).get("llm") or "").strip()
        handler = route_llm(llm_key)

        if not handler:
            reset_state(wa_from)
            await send_llm_menu(client, api_url, headers, wa_from)
            return {"status": "need_llm_again"}

        reply_text = await handler(wa_from, user_text)

    if C.get("DRY_RUN"):
        log("wa_outbound_dry_run", to=wa_from, text=(reply_text or "")[:500])
        return {"status": "dry_ok"}

    reply_text = clean_reply_text(reply_text)

    resp = await wa_send_text(client, api_url, headers, wa_from, reply_text)
    return {
        "status": "sent" if resp.is_success else "error",
        "code": resp.status_code,
    }