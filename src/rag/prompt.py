from collections.abc import Sequence
from typing import Any

NO_CONTEXT_MESSAGE = "Nenhum contexto encontrado no banco vetorial."

RAG_USER_TEMPLATE = """
Contexto recuperado do banco vetorial:
{context}

Pergunta do produtor:
{question}
""".strip()


def _metadata_value(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    if value is None:
        return ""
    return str(value).strip()


def format_documents(docs: Sequence[Any]) -> str:
    parts: list[str] = []

    for index, doc in enumerate(docs, start=1):
        text = (getattr(doc, "page_content", "") or "").strip()
        if not text:
            continue

        metadata = getattr(doc, "metadata", {}) or {}
        fonte = _metadata_value(metadata, "fonte") or _metadata_value(
            metadata,
            "doc_id",
        )
        pagina = _metadata_value(metadata, "pagina")
        titulo = _metadata_value(metadata, "titulo")

        meta_parts = [f"trecho {index}"]
        if fonte:
            meta_parts.append(f"fonte: {fonte}")
        if pagina:
            meta_parts.append(f"página: {pagina}")
        if titulo:
            meta_parts.append(f"título: {titulo}")

        parts.append(f"[{' | '.join(meta_parts)}]\n{text}")

    return "\n\n".join(parts).strip()


def build_rag_input(question: str, docs: Sequence[Any]) -> str:
    context = format_documents(docs)
    if not context:
        context = NO_CONTEXT_MESSAGE

    return RAG_USER_TEMPLATE.format(
        context=context,
        question=(question or "").strip(),
    )
