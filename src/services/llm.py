import asyncio
import json
import time
import traceback
from dataclasses import dataclass
from typing import Any

from config import GPT5_FAST_MODEL, GPT5_RAG_MODEL, RECEPTION_ROUTER_PROMPT, RECEPTION_SYSTEM_PROMPT, SYSTEM_PROMPT, env
from rag.citations import append_sources
from rag.prompt import build_no_evidence_input, build_rag_input
from rag.store import retrieve_documents
from services.memory import build_context_block, fetch_messages
from services.router import RouteDecision, build_retrieval_query, decision_from_semantic_route, route_message
from utils.logging import log

_client: Any | None = None

_ROUTE_FORMAT = {
    "type": "json_schema",
    "name": "maleicultura_reception_route",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "route": {"type": "string", "enum": ["social", "apple_technical", "apple_followup", "clarify", "off_topic"]},
            "reply": {"type": "string"},
        },
        "required": ["route", "reply"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class SemanticReceptionResult:
    decision: RouteDecision
    reply: str
    history: list[dict[str, Any]]


def _openai_client() -> Any:
    global _client
    if _client is None:
        from openai import AsyncOpenAI
        _client = AsyncOpenAI(api_key=env("OPENAI_API_KEY"))
    return _client


def _finalize_reply(wa_from: str, user_text: str, reply: str, started_at: float, model: str, route_kind: str) -> str:
    latency = time.perf_counter() - started_at
    log("llm_latency", model=model, route=route_kind, wa_from=wa_from, seconds=round(latency, 3), input_chars=len(user_text or ""), output_chars=len(reply or ""))
    return (reply or "").strip()


def _log_llm_exception(wa_from: str, user_text: str, started_at: float, exc: Exception, model: str, route_kind: str) -> None:
    latency = time.perf_counter() - started_at
    log("llm_exception", model=model, route=route_kind, wa_from=wa_from, seconds=round(latency, 3), input_chars=len(user_text or ""), error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())


def _format_history(history: list[dict[str, Any]], limit: int = 6) -> str:
    recent = [r for r in history if r.get("role") in ("user", "assistant")][-limit:]
    return "\n".join(f"{'Usuário' if rec['role'] == 'user' else 'Assistente'}: {rec['content']}" for rec in recent if (rec.get("content") or "").strip())


def _format_instruction_context(system: str, history: list[dict[str, Any]]) -> str:
    formatted_history = _format_history(history, limit=8)
    return system if not formatted_history else f"{system}\n\nHistórico recente:\n{formatted_history}"


async def _fetch_recent_history(wa_from: str, limit: int = 8) -> list[dict[str, Any]]:
    return await asyncio.to_thread(fetch_messages, wa_from, limit)


async def _context_for_route(wa_from: str, decision: RouteDecision, existing_history: list[dict[str, Any]] | None = None) -> tuple[str, list[dict[str, Any]]]:
    if not decision.include_history:
        return SYSTEM_PROMPT, []
    if existing_history is not None:
        history = existing_history
    else:
        _system, history, _summary = await asyncio.to_thread(build_context_block, wa_from, 12)
    return _format_instruction_context(SYSTEM_PROMPT, history), history


async def _reception_reply(user_text: str) -> str:
    client = _openai_client()
    res = await client.responses.create(model=GPT5_FAST_MODEL, reasoning={"effort": "minimal"}, instructions=RECEPTION_SYSTEM_PROMPT, input=user_text, max_output_tokens=90, store=False)
    reply = (getattr(res, "output_text", "") or "").strip()
    return reply or "Olá! Posso ajudar com dúvidas sobre produção e manejo de maçãs."


async def _semantic_reception_route(wa_from: str, user_text: str) -> SemanticReceptionResult:
    history = await _fetch_recent_history(wa_from, limit=6)
    history_text = _format_history(history, limit=6)
    classifier_input = f"Histórico recente:\n{history_text}\n\nMensagem atual:\n{user_text}" if history_text else f"Mensagem atual:\n{user_text}"
    try:
        client = _openai_client()
        res = await client.responses.create(
            model=GPT5_FAST_MODEL,
            reasoning={"effort": "minimal"},
            instructions=RECEPTION_ROUTER_PROMPT,
            input=classifier_input,
            text={"format": _ROUTE_FORMAT},
            max_output_tokens=180,
            store=False,
        )
        payload = json.loads((getattr(res, "output_text", "") or "").strip())
        semantic_route = str(payload.get("route") or "clarify")
        reply = str(payload.get("reply") or "").strip()
        decision = decision_from_semantic_route(semantic_route)
        log("semantic_route", wa_from=wa_from, route=semantic_route, use_rag=decision.use_rag, history_messages=len(history))
        return SemanticReceptionResult(decision, reply, history)
    except Exception as exc:
        log("semantic_route_exception", wa_from=wa_from, error_type=type(exc).__name__, error=str(exc))
        decision = decision_from_semantic_route("clarify")
        fallback = "Posso te ajudar com isso se for sobre macieiras ou o pomar. Você pode me dizer se a dúvida é sobre produção de maçãs?"
        return SemanticReceptionResult(decision, fallback, history)


async def handler_gpt5_rag(wa_from: str, user_text: str, decision: RouteDecision | None = None) -> str:
    started_at = time.perf_counter()
    decision = decision or route_message(user_text)
    log("message_routed", wa_from=wa_from, route=decision.kind, use_rag=decision.use_rag, include_history=decision.include_history, model_tier=decision.model_tier, max_output_tokens=decision.max_output_tokens)

    if decision.kind == "social":
        try:
            reply = await _reception_reply(user_text)
            return _finalize_reply(wa_from, user_text, reply, started_at, GPT5_FAST_MODEL, "social")
        except Exception as exc:
            _log_llm_exception(wa_from, user_text, started_at, exc, GPT5_FAST_MODEL, "social")
            return "Olá! Posso ajudar com dúvidas sobre produção e manejo de maçãs."

    semantic_history: list[dict[str, Any]] | None = None
    if decision.kind == "ambiguous":
        semantic = await _semantic_reception_route(wa_from, user_text)
        decision = semantic.decision
        semantic_history = semantic.history
        if not decision.use_rag:
            reply = semantic.reply
            if not reply:
                try:
                    reply = await _reception_reply(user_text)
                except Exception:
                    reply = "Posso ajudar com dúvidas sobre produção e manejo de maçãs. Conte um pouco mais sobre o que você precisa."
            return _finalize_reply(wa_from, user_text, reply, started_at, GPT5_FAST_MODEL, decision.kind)

    model = GPT5_RAG_MODEL
    try:
        instructions, history = await _context_for_route(wa_from, decision, existing_history=semantic_history)
        retrieval_query = build_retrieval_query(user_text, history, decision)
        docs = await retrieve_documents(retrieval_query) if decision.use_rag else []
        model_input = build_rag_input(user_text, docs) if docs else build_no_evidence_input(user_text)
        log("rag_prompt_built", wa_from=wa_from, route=decision.kind, docs=len(docs), input_chars=len(model_input), retrieval_query_chars=len(retrieval_query), evidence="found" if docs else "insufficient")
        client = _openai_client()
        res = await client.responses.create(model=model, reasoning={"effort": decision.reasoning_effort}, instructions=instructions, input=model_input, max_output_tokens=decision.max_output_tokens, store=False)
        reply = getattr(res, "output_text", "") or ""
        if not reply.strip():
            reply = "Desculpe, não consegui formular uma resposta útil para essa mensagem."
        elif docs:
            reply = append_sources(reply, docs)
        return _finalize_reply(wa_from, user_text, reply, started_at, model, decision.kind)
    except Exception as exc:
        _log_llm_exception(wa_from, user_text, started_at, exc, model, decision.kind)
        reply = "Desculpe, tive um problema ao processar sua mensagem. Você pode tentar novamente em alguns instantes."
        return _finalize_reply(wa_from, user_text, reply, started_at, model, decision.kind)
