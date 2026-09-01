import asyncio
import time
import traceback
from typing import Any

from config import env
from utils.logging import log
from services.memory import (
    build_context_block,
    save_message,
    maybe_summarize_with_gemini,
)


async def _finalize_reply(
    model: str,
    wa_from: str,
    user_text: str,
    reply: str,
    started_at: float,
) -> str:
    latency = time.perf_counter() - started_at

    log(
        "llm_latency",
        model=model,
        wa_from=wa_from,
        seconds=round(latency, 3),
        input_chars=len(user_text or ""),
        output_chars=len(reply or ""),
    )

    save_message(wa_from, "user", user_text)
    save_message(wa_from, "assistant", reply)
    await maybe_summarize_with_gemini(wa_from)

    return (reply or "").strip()


def _log_llm_exception(
    model: str,
    wa_from: str,
    user_text: str,
    started_at: float,
    exc: Exception,
) -> None:
    latency = time.perf_counter() - started_at

    log(
        "llm_exception",
        model=model,
        wa_from=wa_from,
        seconds=round(latency, 3),
        input_chars=len(user_text or ""),
        error_type=type(exc).__name__,
        error=str(exc),
        traceback=traceback.format_exc(),
    )


async def handler_gemini(wa_from: str, user_text: str) -> str:
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain.schema import SystemMessage, HumanMessage, AIMessage

    started_at = time.perf_counter()

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=env("GEMINI_API_KEY"),
        convert_system_message_to_human=True,
        temperature=0.3,
        max_output_tokens=500,
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

        if not reply or not str(reply).strip():
            reply = "Desculpe, o modelo não retornou conteúdo."

        return await _finalize_reply("gemini", wa_from, user_text, reply, started_at)

    except Exception as e:
        _log_llm_exception("gemini", wa_from, user_text, started_at, e)
        reply = "Desculpe, ocorreu um erro."
        return await _finalize_reply("gemini", wa_from, user_text, reply, started_at)


async def handler_gpt(wa_from: str, user_text: str) -> str:
    import openai

    started_at = time.perf_counter()
    client = openai.OpenAI(api_key=env("OPENAI_API_KEY"))

    system, history, summary = build_context_block(wa_from, max_history=30)

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
                max_output_tokens=500,
            )
        )
        reply = getattr(res, "output_text", "") or "Erro."

        if not reply or not str(reply).strip():
            reply = "Desculpe, o modelo não retornou conteúdo."

        return await _finalize_reply("gpt", wa_from, user_text, reply, started_at)

    except Exception as e:
        _log_llm_exception("gpt", wa_from, user_text, started_at, e)
        reply = "Desculpe, ocorreu um erro ao processar sua solicitação."
        return await _finalize_reply("gpt", wa_from, user_text, reply, started_at)


async def handler_deepseek(wa_from: str, user_text: str) -> str:
    from langchain_openai import ChatOpenAI
    from langchain.schema import SystemMessage, HumanMessage, AIMessage

    started_at = time.perf_counter()

    client = ChatOpenAI(
        api_key=env("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        temperature=0.3,
        max_tokens=500,
        timeout=45,
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
        res = await asyncio.to_thread(client.invoke, msgs)
        reply = getattr(res, "content", "") or str(res)

        if not reply or not str(reply).strip():
            reply = "Desculpe, o modelo não retornou conteúdo."

        return await _finalize_reply("deepseek", wa_from, user_text, reply, started_at)

    except Exception as e:
        _log_llm_exception("deepseek", wa_from, user_text, started_at, e)
        reply = "Desculpe, ocorreu um erro ao processar sua solicitação."
        return await _finalize_reply("deepseek", wa_from, user_text, reply, started_at)


def route_llm(llm_key: str):
    if llm_key == "gemini":
        return handler_gemini
    if llm_key == "gpt":
        return handler_gpt
    if llm_key == "deepseek":
        return handler_deepseek
    return None