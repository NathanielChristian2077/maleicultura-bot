import os
import re
import time
import json
import asyncio
from typing import Any, Optional, Literal, Tuple

import httpx
import boto3
from botocore.exceptions import ClientError

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from mangum import Mangum


# ============================================================
# App / Clients (nível de módulo para reutilização no Lambda)
# ============================================================

app = FastAPI()
_ddb = boto3.client("dynamodb")


# ============================================================
# Constantes / Config
# ============================================================

SYSTEM_PROMPT = """
Você é um consultor agrícola especializado em produção e manejo de maçãs.
Seu papel é orientar produtores sobre plantio, irrigação, poda, controle de pragas, colheita e comercialização.
Responda de forma clara, prática e técnica, com foco em aumentar a produtividade e a qualidade das maçãs, reduzindo custos e impactos ambientais.
Dê dicas objetivas baseadas em boas práticas agrícolas e experiências reais no campo.
""".strip()

MAX_WA_TEXT = 4096

# WhatsApp Cloud API: reply buttons (max 3)
MENU_TITLE_MAX = 20
MENU_ID_MAX = 256
MENU_BODY_MAX = 1024

GRAPH_DEFAULT_VERSION = os.getenv("GRAPH_API_VERSION", "v23.0")

# TTLs (ajustáveis por env se quiser)
_DEDUP_TTL_SEC = int(os.getenv("DEDUP_TTL_SEC", "600"))
_STATE_TTL_SEC = int(os.getenv("STATE_TTL_SEC", "1800"))  # 30min

# Dynamo (conversas) - "memória"
CONV_TABLE = os.getenv("CONV_TABLE", "conversations")
CONV_TOKEN_LIMIT = int(os.getenv("CONV_TOKEN_LIMIT", "2000"))
CONV_TTL_DAYS = int(os.getenv("CONV_TTL_DAYS", "7"))

# Regex precompile (performance + consistência) - limpa markdowns, os quais o wpp não mostra
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_GARBAGE_RE = re.compile(r"[`*_~#>]")


# ============================================================
# Logger simples (prints estruturados)
# ============================================================


def log(event: str, **fields: Any) -> None:
    print({"type": event, **fields})


# ============================================================
# Env helpers / runtime config
# ============================================================


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


def clean_token(raw: str) -> str:
    return (raw or "").replace("\r", "").replace("\n", "").strip().strip('"').strip("'")


def cfg() -> dict[str, Any]:
    token = clean_token(env("WHATSAPP_TOKEN"))
    return {
        "VERIFY_TOKEN": env("WHATSAPP_VERIFY_TOKEN"),
        "WABA_TOKEN": token,
        "PHONE_NUMBER_ID": env("WHATSAPP_PHONE_NUMBER_ID"),
        "GRAPH_VERSION": env("GRAPH_API_VERSION", GRAPH_DEFAULT_VERSION),
        "DRY_RUN": env("DRY_RUN", "false").lower() == "true",
    }


def wa_api_url(C: dict[str, Any]) -> str:
    # Graph API endpoint: /{PHONE_NUMBER_ID}/messages
    return f"https://graph.facebook.com/{C['GRAPH_VERSION']}/{C['PHONE_NUMBER_ID']}/messages"


def wa_headers(C: dict[str, Any]) -> dict[str, str]:
    token = clean_token(C.get("WABA_TOKEN") or "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ============================================================
# Deduplicação em memória (wamid) com GC
# Objetivo: evitar reprocessar retries do WhatsApp (gera loops infinitos de retry ou causa respostas desincronizadas com as perguntas).
# ============================================================

_seen_wamids: dict[str, float] = {}


def dedup_gc() -> None:
    now = time.time()
    expired = [k for k, ts in _seen_wamids.items() if now - ts > _DEDUP_TTL_SEC]
    for k in expired:
        _seen_wamids.pop(k, None)


def seen_before(wamid: Optional[str]) -> bool:
    dedup_gc()
    if not wamid:
        return False
    now = time.time()
    if wamid in _seen_wamids:
        return True
    _seen_wamids[wamid] = now
    return False


# ============================================================
# Estado em memória do usuário (escolhas de menu)
# NÃO é persistido entre execuções / instâncias.
# Isso é intencional por enquanto.
# Considerando que ainda não existe nenhum modo além do normal e ainda não sabemos se tudo será mantido ou apenas o LLM + mode que se sair melhor.
# ============================================================

_user_state: dict[str, dict[str, Any]] = {}


def state_gc() -> None:
    now = time.time()
    expired = []
    for k, st in _user_state.items():
        ts = float(st.get("_ts", 0))
        if now - ts > _STATE_TTL_SEC:
            expired.append(k)
    for k in expired:
        _user_state.pop(k, None)


def get_state(wa_from: str) -> dict[str, Any]:
    state_gc()
    st = _user_state.get(wa_from)
    if not st:
        return {"stage": "choose_llm"}
    return st


def set_state(wa_from: str, **kwargs: Any) -> None:
    state_gc()
    st = _user_state.get(wa_from) or {}
    st.update(kwargs)
    st["_ts"] = time.time()
    _user_state[wa_from] = st


def reset_state(wa_from: str) -> None:
    _user_state.pop(wa_from, None)


# ============================================================
# Dynamo Conversation Memory + Summarization
# ============================================================


def _ts_ms() -> int:
    return int(time.time() * 1000)


def _ttl_epoch_seconds(days: int) -> int:
    # DynamoDB TTL usa epoch em segundos
    return int(time.time()) + days * 86400


def _approx_tokens(text: str) -> int:
    # Aproximação simples -> suficiente para "trigger de resumo".
    if not text:
        return 0
    return max(1, int(len(text.split()) / 0.75))


def save_message(wa_from: str, role: str, content: str) -> None:
    try:
        _ddb.put_item(
            TableName=CONV_TABLE,
            Item={
                "wa_from": {"S": wa_from},
                "ts": {"N": str(_ts_ms())},
                "role": {"S": role},
                "content": {"S": content or ""},
                "ttl": {"N": str(_ttl_epoch_seconds(CONV_TTL_DAYS))},
            },
        )
    except ClientError as e:
        log("ddb_put_err", error=str(e))


def fetch_messages(wa_from: str, limit: int = 50) -> list[dict[str, Any]]:
    try:
        resp = _ddb.query(
            TableName=CONV_TABLE,
            KeyConditionExpression="wa_from = :w",
            ExpressionAttributeValues={":w": {"S": wa_from}},
            Limit=limit,
            ScanIndexForward=False,
        )
        items = list(reversed(resp.get("Items", [])))
        return [
            {
                "ts": int(it["ts"]["N"]),
                "role": it["role"]["S"],
                "content": it["content"]["S"],
            }
            for it in items
        ]
    except ClientError as e:
        log("ddb_query_err", error=str(e))
        return []


def latest_summary(wa_from: str, limit: int = 120) -> Optional[str]:
    hist = fetch_messages(wa_from, limit)
    for rec in reversed(hist):
        if rec["role"] == "system_summary":
            return rec["content"]
    return None


async def maybe_summarize_with_gemini(wa_from: str) -> None:
    history = fetch_messages(wa_from, 120)
    joined = "\n".join(f"{r['role']}: {r['content']}" for r in history)
    total = _approx_tokens(joined)
    if total <= CONV_TOKEN_LIMIT:
        return

    log("token_limit_hit", wa_from=wa_from, tokens=total)

    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain.schema import HumanMessage

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=env("GEMINI_API_KEY"),
        convert_system_message_to_human=True,
        temperature=0.3,
        max_output_tokens=500,
    )
    prompt = (
        "Resuma o diálogo a seguir de forma breve, mantendo informações importantes e contexto:\n\n"
        + joined
    )

    try:
        res = await asyncio.to_thread(llm.invoke, [HumanMessage(content=prompt)])
        summary = getattr(res, "content", "") or str(res)
        summary = (summary or "").strip()
        if summary:
            save_message(wa_from, "system_summary", summary[:2000])
            log("summary_created", wa_from=wa_from, length=len(summary))
    except Exception as e:
        log("summary_exception", wa_from=wa_from, error=str(e))


# ============================================================
# LLM Handlers (Gemini / GPT / DeepSeek)
# ============================================================


def build_context_block(
    wa_from: str, max_history: int = 20
) -> tuple[str, list[dict[str, Any]], Optional[str]]:
    history = fetch_messages(wa_from, max_history)
    summary = latest_summary(wa_from)
    return SYSTEM_PROMPT, history, summary


async def handler_gemini(wa_from: str, user_text: str) -> str:
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain.schema import SystemMessage, HumanMessage, AIMessage

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=env("GEMINI_API_KEY"),
        convert_system_message_to_human=True,
        temperature=0.3,
        max_output_tokens=300,
    )

    system, history, summary = build_context_block(wa_from, max_history=30)

    msgs: list[Any] = [SystemMessage(content=system)]
    if summary:
        msgs.append(SystemMessage(content=f"Resumo: {summary}"))

    for rec in [r for r in history if r["role"] in ("user", "assistant")][-10:]:
        if rec["role"] == "user":
            msgs.append(HumanMessage(content=rec["content"]))
        else:
            msgs.append(AIMessage(content=rec["content"]))
    msgs.append(HumanMessage(content=user_text))

    try:
        res = await asyncio.to_thread(llm.invoke, msgs)
        reply = getattr(res, "content", None) or str(res)
    except Exception as e:
        log("gemini_exception", wa_from=wa_from, error=str(e))
        reply = "Desculpe, ocorreu um erro."

    save_message(wa_from, "user", user_text)
    save_message(wa_from, "assistant", reply)
    await maybe_summarize_with_gemini(wa_from)
    return (reply or "").strip()


async def handler_gpt(wa_from: str, user_text: str) -> str:
    import openai

    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))

    system, history, summary = build_context_block(wa_from, max_history=20)

    context = system + "\n"
    if summary:
        context += f"Resumo: {summary}\n"
    for rec in [r for r in history if r["role"] in ("user", "assistant")][-10:]:
        context += f"{rec['role']}: {rec['content']}\n"
    context += f"Usuário: {user_text}"

    try:
        res = await asyncio.to_thread(
            lambda: client.responses.create(
                model="gpt-5-2025-08-07",
                reasoning={"effort": "minimal"},
                instructions=context,
                input=user_text,
            )
        )
        reply = getattr(res, "output_text", "") or "Erro."
    except Exception as e:
        log("openai_exception", wa_from=wa_from, error=str(e))
        reply = "Desculpe, ocorreu um erro ao processar sua solicitação."

    save_message(wa_from, "user", user_text)
    save_message(wa_from, "assistant", reply)
    await maybe_summarize_with_gemini(wa_from)
    return (reply or "").strip()


async def handler_deepseek(wa_from: str, user_text: str) -> str:
    from langchain_openai import ChatOpenAI
    from langchain.schema import SystemMessage, HumanMessage, AIMessage

    client = ChatOpenAI(
        api_key=os.environ.get("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0.3,
        max_tokens=300,
    )

    system, history, summary = build_context_block(wa_from, max_history=20)

    msgs: list[Any] = [SystemMessage(content=system)]
    if summary:
        msgs.append(SystemMessage(content=f"Resumo: {summary}"))

    for rec in [r for r in history if r["role"] in ("user", "assistant")][-10:]:
        if rec["role"] == "user":
            msgs.append(HumanMessage(content=rec["content"]))
        else:
            msgs.append(AIMessage(content=rec["content"]))
    msgs.append(HumanMessage(content=user_text))

    try:
        res = await asyncio.to_thread(client.invoke, msgs)
        reply = getattr(res, "content", "") or str(res)
    except Exception as e:
        log("deepseek_exception", wa_from=wa_from, error=str(e))
        reply = "Desculpe, ocorreu um erro ao processar sua solicitação."

    save_message(wa_from, "user", user_text)
    save_message(wa_from, "assistant", reply)
    await maybe_summarize_with_gemini(wa_from)
    return (reply or "").strip()


def route_llm(llm_key: str):
    if llm_key == "gemini":
        return handler_gemini
    if llm_key == "gpt":
        return handler_gpt
    if llm_key == "deepseek":
        return handler_deepseek
    return None


# ============================================================
# WhatsApp Send Helpers (text / interactive / feedback)
# ============================================================


def clean_reply_text(text: str) -> str:
    # Remove links markdown e alguns caracteres de formatação
    text = _MD_LINK_RE.sub(r"\1", text or "")
    text = _MD_GARBAGE_RE.sub("", text).strip()
    if len(text) > MAX_WA_TEXT:
        text = text[: MAX_WA_TEXT - 1] + "…"
    return text


async def wa_send_text(
    client: httpx.AsyncClient, api_url: str, headers: dict[str, str], to: str, body: str
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
    client: httpx.AsyncClient, api_url: str, headers: dict[str, str], wamid: str
) -> None:
    try:
        await client.post(
            api_url,
            headers=headers,
            json={
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": wamid,
            },
        )
    except Exception as e:
        log("wa_feedback_err_read", error=str(e))


async def wa_typing(
    client: httpx.AsyncClient,
    api_url: str,
    headers: dict[str, str],
    to: str,
    status: Literal["typing", "paused"],
) -> None:
    try:
        await client.post(
            api_url,
            headers=headers,
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "typing",
                "typing": {"status": status},
            },
        )
    except Exception as e:
        log("wa_feedback_err_typing", status=status, error=str(e))


async def send_llm_menu(
    client: httpx.AsyncClient, api_url: str, headers: dict[str, str], wa_from: str
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
    client: httpx.AsyncClient, api_url: str, headers: dict[str, str], wa_from: str
) -> None:
    await wa_send_interactive_buttons(
        client,
        api_url,
        headers,
        wa_from,
        "Escolha o modo (por enquanto todos vão pro modo normal):",
        [("mode_normal", "Normal"), ("mode_rag", "RAG"), ("mode_ft", "Fine-tuning")],
    )


# ============================================================
# Helpers de parse do payload
# ============================================================


def extract_value(payload: dict[str, Any]) -> dict[str, Any]:
    entry = (payload.get("entry") or [{}])[0]
    change = (entry.get("changes") or [{}])[0]
    return change.get("value") or {}


def extract_message(value: dict[str, Any]) -> Optional[dict[str, Any]]:
    msgs = value.get("messages") or []
    if not msgs:
        return None
    return msgs[0]


def extract_text(msg: dict[str, Any]) -> str:
    return ((msg.get("text") or {}).get("body") or "").strip()


def extract_button_id(msg: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    inter = msg.get("interactive") or {}
    br = inter.get("button_reply") or {}
    return br.get("id"), br.get("title")


# ============================================================
# Helpers de UI (labels / prompt "ready")
# ============================================================

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


async def send_ready_prompt(
    client: httpx.AsyncClient,
    api_url: str,
    headers: dict[str, str],
    wa_from: str
) -> None:
    st = get_state(wa_from)
    llm_show = llm_label(st.get("llm"))
    mode_show = mode_label(st.get("mode"))
    await wa_send_interactive_buttons(
        client, api_url, headers, wa_from,
        f"Fechado.\nLLM: {llm_show}\nModo: {mode_show}\n\nAgora envie sua pergunta.",
        [("menu_reset", "Alterar modelo"), ("menu_keep", "Manter escolhas"), ("menu_help", "Ajuda")],
    )


# ============================================================
# Interativos (botões)
# ============================================================

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

    # Feedback visual (best effort) também para clique em botão
    if wamid:
        await wa_mark_read(client, api_url, headers, wamid)
    await wa_typing(client, api_url, headers, wa_from, "typing")

    try:
        if not button_id:
            await wa_send_interactive_buttons(
                client, api_url, headers, wa_from,
                "Não entendi tua seleção. Vamos de novo.",
                [("menu_reset", "Reset"), ("menu_help", "Ajuda"), ("menu_keep", "Manter")],
            )
            return {"status": "ok_no_button_id"}

        if button_id in ("llm_gemini", "llm_gpt", "llm_deepseek"):
            llm = {"llm_gemini": "gemini", "llm_gpt": "gpt", "llm_deepseek": "deepseek"}[button_id]
            set_state(wa_from, stage="choose_mode", llm=llm)
            await send_mode_menu(client, api_url, headers, wa_from)
            return {"status": "ok_llm_selected"}

        if button_id in ("mode_normal", "mode_rag", "mode_ft"):
            mode = {"mode_normal": "normal", "mode_rag": "rag", "mode_ft": "finetune"}[button_id]

            st = get_state(wa_from)
            if not st.get("llm"):
                # Se o usuário selecionou modo sem LLM, recupera o fluxo
                reset_state(wa_from)
                await send_llm_menu(client, api_url, headers, wa_from)
                return {"status": "need_llm_first"}

            # Por enquanto, o modo (normal / RAG / fine-tuning) é apenas UI.
            set_state(wa_from, stage="ready", mode=mode)

            await send_ready_prompt(client, api_url, headers, wa_from)
            return {"status": "ok_mode_selected"}

        if button_id == "menu_reset":
            reset_state(wa_from)
            await send_llm_menu(client, api_url, headers, wa_from)
            return {"status": "ok_reset"}

        if button_id == "menu_help":
            await wa_send_interactive_buttons(
                client, api_url, headers, wa_from,
                "Fluxo:\n1) Escolher LLM\n2) Escolher modo\n3) Enviar pergunta\n\nReset para alterar opções.",
                [("menu_reset", "Reset"), ("menu_keep", "Manter escolhas")],
            )
            return {"status": "ok_help"}

        if button_id == "menu_keep":
            await send_ready_prompt(client, api_url, headers, wa_from)
            return {"status": "ok_keep"}

        await wa_send_interactive_buttons(
            client, api_url, headers, wa_from,
            "Seleção inválida. Por favor, tente novamente.",
            [("menu_reset", "Reset"), ("menu_help", "Ajuda"), ("menu_keep", "Manter escolhas")],
        )
        return {"status": "ok_unknown_button"}

    finally:
        await wa_typing(client, api_url, headers, wa_from, "paused")


# ============================================================
# Roteamento de mensagens de texto
# ============================================================

async def handle_text_message(
    client: httpx.AsyncClient,
    C: dict[str, Any],
    api_url: str,
    headers: dict[str, str],
    wa_from: str,
    wamid: Optional[str],
    text: str,
) -> dict[str, Any]:
    # Gate: se ainda não escolheu tudo do menu, força o fluxo (com explicação curta)
    st = get_state(wa_from)
    if text and st.get("stage") != "ready":
        if st.get("stage") == "choose_mode" and st.get("llm"):
            await wa_send_text(
                client, api_url, headers, wa_from,
                "Antes eu preciso que você escolha o modo no menu abaixo."
            )
            await send_mode_menu(client, api_url, headers, wa_from)
            return {"status": "need_mode"}

        await wa_send_text(
            client, api_url, headers, wa_from,
            "Antes eu preciso que você escolha o modelo no menu abaixo."
        )
        await send_llm_menu(client, api_url, headers, wa_from)
        return {"status": "need_llm"}

    # Feedback visual (best effort)
    if wamid:
        await wa_mark_read(client, api_url, headers, wamid)
    await wa_typing(client, api_url, headers, wa_from, "typing")

    try:
        prefix = text[:1] if text else ""
        user_text = text[1:].lstrip() if len(text) > 1 else text

        reply_text: str
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
            log("wa_outbound_dry_run", to=wa_from, text=reply_text[:MAX_WA_TEXT])
            return {"status": "dry_ok"}

        reply_text = clean_reply_text(reply_text)

        resp = await wa_send_text(client, api_url, headers, wa_from, reply_text)
        return {"status": "sent" if resp.is_success else "error", "code": resp.status_code}

    finally:
        # Sempre tenta pausar o typing, mesmo em early-return/erro
        await wa_typing(client, api_url, headers, wa_from, "paused")


# ============================================================
# FastAPI routes
# ============================================================


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

    # "soft" fail: retorna ok pra evitar retries infinitos
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

        # Best-effort echo detection
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

        async with httpx.AsyncClient(timeout=10) as client:
            if wa_type == "interactive":
                return await handle_interactive(client, api_url, headers, wa_from, msg)

            if wa_type == "text":
                text = extract_text(msg)
                if not text:
                    return {"status": "ok"}
                return await handle_text_message(
                    client, C, api_url, headers, wa_from, wamid, text
                )

            log("wa_inbound_skip", reason="unsupported_type", wa_type=wa_type)
            return {"status": "ok"}

    except Exception as e:
        log("wa_exception", error=str(e))
        return {"status": "exception"}


handler = Mangum(app)
