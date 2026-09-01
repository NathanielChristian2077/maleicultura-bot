from collections.abc import Sequence
from typing import Any

NO_CONTEXT_MESSAGE = "Nenhum contexto documental relevante foi recuperado."

RAG_USER_TEMPLATE = """
Contexto documental recuperado:
{context}

Pergunta do produtor:
{question}

Regras para esta resposta:
- Use o contexto recuperado como base da orientação técnica.
- Não invente números, produtos, doses, diagnósticos ou recomendações que não estejam sustentados pelo contexto.
- Responda ao que foi perguntado com linguagem natural e prática.
- Ao usar uma informação de um trecho, cite o identificador correspondente, como [1] ou [2].
- Se os trechos não sustentarem uma conclusão específica, deixe a limitação clara e peça apenas a informação adicional realmente necessária.
""".strip()

NO_EVIDENCE_TEMPLATE = """
Pergunta do produtor:
{question}

Não há evidência documental suficientemente relevante para sustentar uma recomendação técnica específica. Responda de forma cordial e natural, sem inventar orientação. Se uma informação objetiva do produtor puder permitir uma busca mais específica, peça somente essa informação. Caso contrário, explique brevemente que não foi possível confirmar uma orientação segura com as informações disponíveis. Não mencione detalhes internos do sistema.
""".strip()


def _metadata_value(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    return "" if value is None else str(value).strip()


def format_documents(docs: Sequence[Any]) -> str:
    parts: list[str] = []
    for index, doc in enumerate(docs, start=1):
        text = (getattr(doc, "page_content", "") or "").strip()
        if not text:
            continue
        metadata = getattr(doc, "metadata", {}) or {}
        fonte = _metadata_value(metadata, "fonte") or _metadata_value(metadata, "doc_id")
        pagina = _metadata_value(metadata, "pagina")
        titulo = _metadata_value(metadata, "titulo")
        meta_parts = [f"fonte_id: [{index}]"]
        if fonte:
            meta_parts.append(f"fonte: {fonte}")
        if pagina:
            meta_parts.append(f"página: {pagina}")
        if titulo:
            meta_parts.append(f"título: {titulo}")
        parts.append(f"[{' | '.join(meta_parts)}]\n{text}")
    return "\n\n".join(parts).strip()


def build_rag_input(question: str, docs: Sequence[Any]) -> str:
    context = format_documents(docs) or NO_CONTEXT_MESSAGE
    return RAG_USER_TEMPLATE.format(context=context, question=(question or "").strip())


def build_no_evidence_input(question: str) -> str:
    return NO_EVIDENCE_TEMPLATE.format(question=(question or "").strip())
