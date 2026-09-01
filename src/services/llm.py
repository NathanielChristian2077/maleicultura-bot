import asyncio
import time
import traceback
from typing import Any

from config import GPT5_FAST_MODEL, GPT5_RAG_MODEL, SYSTEM_PROMPT, env
from rag.prompt import build_rag_input
from rag.store import retrieve_documents
from services.memory import build_context_block, save_message
from services.router import RouteDecision, build_retrieval_query, route_message
from utils.logging import log

_client: Any | None = None


def _openai_client() -> Any:
    global _client
    if _client is None:
        from openai import AsyncOpenAI

        _client = AsyncOpenAI(api_key=env("OPENAI_API_KEY"))
    return _client


async def _finalize_reply(
    wa_from: str,
    user_text: str,
    reply: str,
    started_at: float,
    model: str,
    route_kind: str,
) -> str:
    latency = time.perf_counter() - started_at
    log(
        "llm_latency",
        model=model,
        route=route_kind,
        wa_from=wa_from,
        seconds=round(latency, 3),
        input_chars=len(user_text or ""),
        output_chars=len(reply or ""),
    )

    await asyncio.to_thread(save_message, wa_from, "user", user_text)
    await asyncio.to_thread(save_message, wa_from, "assistant", reply)
    return (reply or "").strip()


def _log_llm_exception(
    wa_from: str,
    user_text: str,
    started_at: float,
    exc: Exception,
    model: str,
    route_kind: str,
) -> None:
    latency = time.perf_counter() - started_at
    log(
        "llm_exception",
        model=model,
        route=route_kind,
        wa_from=wa_from,
        seconds=round(latency, 3),
        input_chars=len(user_text or ""),
        error_type=type(exc).__name__,
        error=str(exc),
        traceback=traceback.format_exc(),
    )


def _format_instruction_context(
    system: str,
    history: list[dict[str, Any]],
) -> str:
    context_parts = [system]
    recent_history = [r for r in history if r.get("role") in ("user", "assistant")][-8:]
    if recent_history:
        formatted_history = "\n".join(
            f"{'Usuário' if rec['role'] == 'user' else 'Assistente'}: {rec['content']}"
            for rec in recent_history
        )
        context_parts.append(f"Histórico recente:\n{formatted_history}")
    return "\n\n".join(context_parts)


async def _context_for_route(
    wa_from: str,
    decision: RouteDecision,
) -> tuple[str, list[dict[str, Any]]]:
    if not decision.include_history:
        return SYSTEM_PROMPT, []

    system, history, _summary = await asyncio.to_thread(
        build_context_block,
        wa_from,
        12,
    )
    return _format_instruction_context(system, history), history


async def handler_gpt5_rag(wa_from: str, user_text: str) -> str:
    started_at = time.perf_counter()
    decision = route_message(user_text)
    log(
        "message_routed",
        wa_from=wa_from,
        route=decision.kind,
        use_rag=decision.use_rag,
        include_history=decision.include_history,
        model_tier=decision.model_tier,
        max_output_tokens=decision.max_output_tokens,
    )

    if decision.static_reply is not None:
        log(
            "static_reply",
            wa_from=wa_from,
            route=decision.kind,
            seconds=round(time.perf_counter() - started_at, 3),
        )
        return decision.static_reply

    model = GPT5_RAG_MODEL if decision.model_tier == "full" else GPT5_FAST_MODEL

    try:
        instructions, history = await _context_for_route(wa_from, decision)
        retrieval_query = build_retrieval_query(user_text, history, decision)
        docs = await retrieve_documents(retrieval_query) if decision.use_rag else []

        if decision.use_rag and not docs:
            reply = (
                "Não encontrei informação suficiente na base documental para "
                "responder com segurança a essa pergunta."
            )
            return await _finalize_reply(
                wa_from,
                user_text,
                reply,
                started_at,
                model="retrieval-abstention",
                route_kind=decision.kind,
            )

        rag_input = build_rag_input(user_text, docs)
        log(
            "rag_prompt_built",
            wa_from=wa_from,
            route=decision.kind,
            docs=len(docs),
            input_chars=len(rag_input),
            retrieval_query_chars=len(retrieval_query),
        )

        client = _openai_client()
        res = await client.responses.create(
            model=model,
            reasoning={"effort": "minimal"},
            instructions=instructions,
            input=rag_input,
            max_output_tokens=decision.max_output_tokens,
            store=False,
        )
        reply = getattr(res, "output_text", "") or ""
        if not reply.strip():
            reply = "Desculpe, o modelo não retornou conteúdo."

        return await _finalize_reply(
            wa_from,
            user_text,
            reply,
            started_at,
            model=model,
            route_kind=decision.kind,
        )
    except Exception as exc:
        _log_llm_exception(
            wa_from,
            user_text,
            started_at,
            exc,
            model=model,
            route_kind=decision.kind,
        )
        reply = "Desculpe, ocorreu um erro ao processar sua solicitação."
        return await _finalize_reply(
            wa_from,
            user_text,
            reply,
            started_at,
            model=model,
            route_kind=decision.kind,
        )
