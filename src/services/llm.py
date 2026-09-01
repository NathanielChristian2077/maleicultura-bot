import asyncio
from typing import Any

from config import env
from utils.logging import log
from services.memory import (
    build_context_block,
    save_message,
    maybe_summarize_with_gemini,
)


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
    import asyncio

    client = openai.OpenAI(api_key=env("OPENAI_API_KEY"))

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
    import asyncio
    from langchain_openai import ChatOpenAI
    from langchain.schema import SystemMessage, HumanMessage, AIMessage

    client = ChatOpenAI(
        api_key=env("DEEPSEEK_API_KEY"),
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