import asyncio
import time
import traceback

from config import GPT5_RAG_MODEL, env
from services.memory import (
    build_context_block,
    maybe_summarize_with_gpt5_rag,
    save_message,
)
from utils.logging import log


async def _finalize_reply(
    wa_from: str,
    user_text: str,
    reply: str,
    started_at: float,
) -> str:
    latency = time.perf_counter() - started_at

    log(
        "llm_latency",
        model="gpt5-rag",
        wa_from=wa_from,
        seconds=round(latency, 3),
        input_chars=len(user_text or ""),
        output_chars=len(reply or ""),
    )

    save_message(wa_from, "user", user_text)
    save_message(wa_from, "assistant", reply)
    await maybe_summarize_with_gpt5_rag(wa_from)

    return (reply or "").strip()


def _log_llm_exception(
    wa_from: str,
    user_text: str,
    started_at: float,
    exc: Exception,
) -> None:
    latency = time.perf_counter() - started_at

    log(
        "llm_exception",
        model="gpt5-rag",
        wa_from=wa_from,
        seconds=round(latency, 3),
        input_chars=len(user_text or ""),
        error_type=type(exc).__name__,
        error=str(exc),
        traceback=traceback.format_exc(),
    )


def _build_instruction_context(wa_from: str) -> str:
    system, history, summary = build_context_block(wa_from, max_history=30)
    context_parts = [system]

    if summary:
        context_parts.append(f"Resumo da conversa anterior:\n{summary}")

    recent_history = [r for r in history if r["role"] in ("user", "assistant")][-10:]
    if recent_history:
        formatted_history = "\n".join(
            f"{'Usuário' if rec['role'] == 'user' else 'Assistente'}: {rec['content']}"
            for rec in recent_history
        )
        context_parts.append(f"Histórico recente:\n{formatted_history}")

    return "\n\n".join(context_parts)


async def handler_gpt5_rag(wa_from: str, user_text: str) -> str:
    from openai import OpenAI

    started_at = time.perf_counter()
    client = OpenAI(api_key=env("OPENAI_API_KEY"))
    instructions = _build_instruction_context(wa_from)

    try:
        res = await asyncio.to_thread(
            lambda: client.responses.create(
                model=GPT5_RAG_MODEL,
                reasoning={"effort": "minimal"},
                instructions=instructions,
                input=user_text,
                max_output_tokens=500,
            )
        )
        reply = getattr(res, "output_text", "") or ""

        if not reply.strip():
            reply = "Desculpe, o modelo não retornou conteúdo."

        return await _finalize_reply(wa_from, user_text, reply, started_at)
    except Exception as e:
        _log_llm_exception(wa_from, user_text, started_at, e)
        reply = "Desculpe, ocorreu um erro ao processar sua solicitação."
        return await _finalize_reply(wa_from, user_text, reply, started_at)
