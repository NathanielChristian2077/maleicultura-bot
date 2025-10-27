import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from mangum import Mangum

import boto3
from botocore.config import Config
import asyncio
import re
import time

app = FastAPI()
_lambda = boto3.client("lambda", config=Config(retries={"max_attempts": 2}))

_seen_wamids: dict[str, float] = {}
_DEDUP_TTL_SEC = 600


def seen_bfr(wamid: str):
    now = time.time()
    expired = [k for k, ts in _seen_wamids.items() if now - ts > _DEDUP_TTL_SEC]
    for k in expired:
        _seen_wamids.pop(k, None)
    if not wamid:
        return False
    if wamid in _seen_wamids:
        return True
    _seen_wamids[wamid] = now
    return False


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
        "GRAPH_VERSION": env("GRAPH_API_VERSION", "v23.0"),
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
        value = change.get("value") or {}

        if not value.get("messages"):
            print({"type": "wa_inbound_skip", "reason": "no_messages_key"})
            return {"status": "ok"}

        msg = value["messages"][0]
        wa_type = msg.get("type")
        wa_from = msg.get("from")
        is_echo = msg.get("from") == (value.get("metadata") or {}).get("display_phone_number")
        if is_echo:
            print({"type": "wa_inbound_skip", "reason": "echo"})
            return {"status": "ok"}

        wamid = msg.get("id") or msg.get("wamid")
        if seen_bfr(wamid):
            print({"type": "wa_inbound_skip", "reason": "duplicate_wamid", "wamid": wamid})
            return {"status": "ok"}

        print({"type": "wa_inbound_message", "from": wa_from, "msg_type": wa_type})

        wa_api_url = f"https://graph.facebook.com/{C['GRAPH_VERSION']}/{C['PHONE_NUMBER_ID']}/messages"

        token = (C["WABA_TOKEN"] or "")
        token = token.replace("\r", "").replace("\n", "").strip().strip('"').strip("'")

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Feedback visual: read e typing
        try:
            async with httpx.AsyncClient(timeout=10) as fb:
                await fb.post(
                    wa_api_url,
                    headers=headers,
                    json={
                        "messaging_product": "whatsapp",
                        "status": "read",
                        "message_id": wamid,
                    },
                )
                await fb.post(
                    wa_api_url,
                    headers=headers,
                    json={
                        "messaging_product": "whatsapp",
                        "to": wa_from,
                        "type": "typing",
                        "typing": {"status": "typing"},
                    },
                )
        except Exception as e:
            print({"type": "wa_feedback_err", "error": str(e)})

        # ===========================================
        #  MEMÓRIA DynamoDB e Resumo (Gemini)
        # ===========================================
        # Direcionei todos os resumos pro Gemini (já que não tem custo), por enquanto é apenas para testes
        from botocore.exceptions import ClientError

        CONV_TABLE = os.getenv("CONV_TABLE", "conversations")
        CONV_TOKEN_LIMIT = int(os.getenv("CONV_TOKEN_LIMIT", "2000"))
        CONV_TTL_DAYS = int(os.getenv("CONV_TTL_DAYS", "7")) # A cada 7 dias a conversa é apagada dos registros
        ddb = boto3.client("dynamodb")
        
        def _ttl_epoch_seconds(days: int) -> int:
           return int(time.time()) + days * 86400 # Dynamo precisa dos dias em segundos 

        def _ts_ms():
            return int(time.time() * 1000)

        def _approx_tokens(text: str):
            if not text:
                return 0
            return max(1, int(len(text.split()) / 0.75))

        def save_message(role: str, content: str):
            try:
                ddb.put_item(
                    TableName=CONV_TABLE,
                    Item={
                        "wa_from": {"S": wa_from},
                        "ts": {"N": str(_ts_ms())},
                        "role": {"S": role},
                        "content": {"S": content or ""},
                        "ttl": {"N": str(_ttl_epoch_seconds(CONV_TTL_DAYS))},  # TTL em segundos
                    },
                )
            except ClientError as e:
                print({"type": "ddb_put_err", "error": str(e)})

        def fetch_messages(limit: int = 50):
            try:
                resp = ddb.query(
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
                print({"type": "ddb_query_err", "error": str(e)})
                return []

        async def maybe_summarize_with_gemini():
            history = fetch_messages(100)
            joined = "\n".join(f"{r['role']}: {r['content']}" for r in history)
            total = _approx_tokens(joined)
            if total <= CONV_TOKEN_LIMIT:
                return

            print({"type": "token_limit_hit", "tokens": total})
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
                if summary:
                    save_message("system_summary", summary.strip()[:2000])
                    print({"type": "summary_created", "len": len(summary)})
            except Exception as e:
                print({"type": "summary_exception", "error": str(e)})

        def latest_summary():
            for rec in reversed(fetch_messages(100)):
                if rec["role"] == "system_summary":
                    return rec["content"]
            return None

        # ===========================================
        #               HANDLERS
        # ===========================================
        # prefixo: @ (Gemini)
        async def dev_handler1(user_text: str, wa_from: str) -> str:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain.schema import SystemMessage, HumanMessage, AIMessage

            llm = ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                google_api_key=env("GEMINI_API_KEY"),
                convert_system_message_to_human=True,
                temperature=0.3,
                max_output_tokens=300,
            )

            history = fetch_messages(30)
            summary = latest_summary()

            msgs = [
                SystemMessage(
                    content=
                    """
                    Você é um consultor agrícola especializado em produção e manejo de maçãs.
                    Seu papel é orientar produtores sobre plantio, irrigação, poda, controle de pragas, colheita e comercialização.
                    Responda de forma clara, prática e técnica, com foco em aumentar a produtividade e a qualidade das maçãs, reduzindo custos e impactos ambientais.
                    Dê dicas objetivas baseadas em boas práticas agrícolas e experiências reais no campo.
                    """.strip()
                )
            ]
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
                print({"type": "gemini_exception", "error": str(e)})
                reply = "Desculpe, ocorreu um erro."

            save_message("user", user_text)
            save_message("assistant", reply)
            await maybe_summarize_with_gemini()
            return reply.strip()

        # prefixo: $ (GPT-5)
        async def dev_handler2(user_text: str, wa_from: str) -> str:
            import openai

            client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
            history = fetch_messages(20)
            summary = latest_summary()

            context = (
                """
                Você é um consultor agrícola especializado em produção e manejo de maçãs.
                Seu papel é orientar produtores sobre plantio, irrigação, poda, controle de pragas, colheita e comercialização.
                Responda de forma clara, prática e técnica, com foco em aumentar a produtividade e a qualidade das maçãs, reduzindo custos e impactos ambientais.
                Dê dicas objetivas baseadas em boas práticas agrícolas e experiências reais no campo.
                """.strip()
            )
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
                print({"type": "openai_exception", "error": str(e)})
                reply = "Desculpe, ocorreu um erro ao processar sua solicitação."

            save_message("user", user_text)
            save_message("assistant", reply)
            await maybe_summarize_with_gemini()
            return reply.strip()

        # prefixo: & (DeepSeek)
        async def dev_handler3(user_text: str, wa_from: str) -> str:
            from langchain_openai import ChatOpenAI
            from langchain.schema import SystemMessage, HumanMessage, AIMessage

            client = ChatOpenAI(
                api_key=os.environ.get("DEEPSEEK_API_KEY"),
                base_url="https://api.deepseek.com",
                model="deepseek-chat",
                temperature=0.3,
                max_tokens=300,
            )

            history = fetch_messages(20)
            summary = latest_summary()
            msgs = [
                SystemMessage(
                    content=
                    """
                    Você é um consultor agrícola especializado em produção e manejo de maçãs.
                    Seu papel é orientar produtores sobre plantio, irrigação, poda, controle de pragas, colheita e comercialização.
                    Responda de forma clara, prática e técnica, com foco em aumentar a produtividade e a qualidade das maçãs, reduzindo custos e impactos ambientais.
                    Dê dicas objetivas baseadas em boas práticas agrícolas e experiências reais no campo.
                    """.strip()
                )
            ]
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
                print({"type": "deepseek_exception", "error": str(e)})
                reply = "Desculpe, ocorreu um erro ao processar sua solicitação."

            save_message("user", user_text)
            save_message("assistant", reply)
            await maybe_summarize_with_gemini()
            return reply.strip()

        # ===========================================
        # Seleção do LLM
        # ===========================================
        text = (msg.get("text") or {}).get("body", "").strip()
        if not text:
            text = ""
        prefix = text[:1]
        user_text = text[1:].lstrip() if len(text) > 1 else ""
        match prefix:
            case "@":
                reply_text = await dev_handler1(user_text, wa_from)
            case "$":
                reply_text = await dev_handler2(user_text, wa_from)
            case "&":
                reply_text = await dev_handler3(user_text, wa_from)
            case _:
                reply_text = (
                    "Prefixo de mensagem não definido, por favor use:\n"
                    "@ [texto] -> Fabricio\n"
                    "$ [texto] -> Bruno\n"
                    "& [texto] -> Nathaniel\n"
                )

        if C["DRY_RUN"]:
            print({"type": "wa_outbound_dry_run", "to": wa_from, "text": reply_text[:4096]})
            return {"status": "dry_ok"}

        # limpa markdown
        reply_text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", reply_text)
        reply_text = re.sub(r"[`*_~#>]", "", reply_text).strip()
        if len(reply_text) > 4096:
            reply_text = reply_text[:4095] + "…"

        # pausa typing
        try:
            async with httpx.AsyncClient(timeout=10) as fb2:
                await fb2.post(
                    wa_api_url,
                    headers=headers,
                    json={
                        "messaging_product": "whatsapp",
                        "to": wa_from,
                        "type": "typing",
                        "typing": {"status": "paused"},
                    },
                )
        except Exception as e:
            print({"type": "wa_feedback_err_pause", "error": str(e)})

        payload = {
            "messaging_product": "whatsapp",
            "to": wa_from,
            "type": "text",
            "text": {"body": reply_text},
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(wa_api_url, json=payload, headers=headers)

        print({"type": "wa_outbound_resp", "status": resp.status_code, "body": resp.text[:4000]})
        return {"status": "sent" if resp.is_success else "error", "code": resp.status_code}

    except Exception as e:
        print({"type": "wa_exception", "error": str(e)})
        return {"status": "exception"}


handler = Mangum(app)
