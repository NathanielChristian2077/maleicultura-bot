import os
import time
from typing import Any

from config import (
    RAG_CHROMA_COLLECTION,
    RAG_CHROMA_PATH,
    RAG_EMBED_MODEL,
    RAG_TOP_K,
    env,
)
from utils.logging import log

_retriever: Any | None = None


def _has_chroma_files(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    with os.scandir(path) as entries:
        return any(entries)


def load_retriever() -> Any | None:
    global _retriever

    if _retriever is not None:
        return _retriever
    if not env("OPENAI_API_KEY"):
        log("rag_disabled", reason="missing_openai_api_key")
        return None

    if not _has_chroma_files(RAG_CHROMA_PATH):
        log("rag_disabled", reason="missing_chroma_db", path=RAG_CHROMA_PATH)
        return None

    try:
        from langchain_chroma import Chroma
        from langchain_openai import OpenAIEmbeddings

        started_at = time.perf_counter()
        embeddings = OpenAIEmbeddings(
            model=RAG_EMBED_MODEL, api_key=env("OPENAI_API_KEY")
        )
        vectorstore = Chroma(
            collection_name=RAG_CHROMA_COLLECTION,
            persist_directory=RAG_CHROMA_PATH,
            embedding_function=embeddings,
        )
        retriever = vectorstore.as_retriever(search_kwargs={"k": RAG_TOP_K})

        log(
            "rag_store_loaded",
            path=RAG_CHROMA_PATH,
            collection=RAG_CHROMA_COLLECTION,
            top_k=RAG_TOP_K,
            seconds=round(time.perf_counter() - started_at, 3),
        )
        _retriever = retriever
        return retriever
    except Exception as exc:
        log("rag_load_exception", error_type=type(exc).__name__, error=str(exc))
        return None


async def retrieve_documents(question: str) -> list[Any]:
    retriever = load_retriever()
    if retriever is None:
        return []

    started_at = time.perf_counter()
    try:
        docs = await retriever.ainvoke((question or "").strip())
        log(
            "rag_retrieval",
            docs=len(docs),
            seconds=round(time.perf_counter() - started_at, 3),
        )
        return list(docs)
    except Exception as exc:
        log(
            "rag_retrieval_exception",
            error_type=type(exc).__name__,
            error=str(exc),
            seconds=round(time.perf_counter() - started_at, 3),
        )
        return []
