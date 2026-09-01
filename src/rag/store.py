import asyncio
import hashlib
import os
import shutil
import time
from typing import Any

from config import (
    RAG_CHROMA_COLLECTION,
    RAG_CHROMA_PATH,
    RAG_DENSE_K,
    RAG_EMBED_MODEL,
    RAG_LEXICAL_K,
    RAG_MIN_RELEVANCE,
    RAG_TOP_K,
    env,
)
from rag.lexical import LexicalHit, search_lexical
from utils.logging import log

_vectorstore: Any | None = None
_RUNTIME_CHROMA_PATH: str | None = None
_RRF_K = 60


def _has_chroma_files(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    with os.scandir(path) as entries:
        return any(entries)


def _runtime_chroma_path() -> str:
    global _RUNTIME_CHROMA_PATH
    if _RUNTIME_CHROMA_PATH:
        return _RUNTIME_CHROMA_PATH

    packaged_path = RAG_CHROMA_PATH
    if not os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        _RUNTIME_CHROMA_PATH = packaged_path
        return packaged_path

    runtime_path = "/tmp/chroma_db"
    if _has_chroma_files(runtime_path):
        _RUNTIME_CHROMA_PATH = runtime_path
        return runtime_path

    if not _has_chroma_files(packaged_path):
        _RUNTIME_CHROMA_PATH = packaged_path
        return packaged_path

    started_at = time.perf_counter()
    try:
        if os.path.exists(runtime_path):
            shutil.rmtree(runtime_path)
        shutil.copytree(packaged_path, runtime_path)
        log(
            "rag_chroma_copied_to_tmp",
            source=packaged_path,
            target=runtime_path,
            seconds=round(time.perf_counter() - started_at, 3),
        )
        _RUNTIME_CHROMA_PATH = runtime_path
        return runtime_path
    except Exception as exc:
        log(
            "rag_chroma_copy_exception",
            source=packaged_path,
            target=runtime_path,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        _RUNTIME_CHROMA_PATH = packaged_path
        return packaged_path


def load_vectorstore() -> Any | None:
    global _vectorstore
    if _vectorstore is not None:
        return _vectorstore

    if not env("OPENAI_API_KEY"):
        log("rag_disabled", reason="missing_openai_api_key")
        return None

    chroma_path = _runtime_chroma_path()
    if not _has_chroma_files(chroma_path):
        log("rag_disabled", reason="missing_chroma_db", path=chroma_path)
        return None

    try:
        from langchain_chroma import Chroma
        from langchain_openai import OpenAIEmbeddings

        started_at = time.perf_counter()
        embeddings = OpenAIEmbeddings(
            model=RAG_EMBED_MODEL,
            api_key=env("OPENAI_API_KEY"),
        )
        _vectorstore = Chroma(
            collection_name=RAG_CHROMA_COLLECTION,
            persist_directory=chroma_path,
            embedding_function=embeddings,
        )
        log(
            "rag_store_loaded",
            path=chroma_path,
            collection=RAG_CHROMA_COLLECTION,
            top_k=RAG_TOP_K,
            dense_k=RAG_DENSE_K,
            lexical_k=RAG_LEXICAL_K,
            seconds=round(time.perf_counter() - started_at, 3),
        )
        return _vectorstore
    except Exception as exc:
        log("rag_load_exception", error_type=type(exc).__name__, error=str(exc))
        return None


def _doc_key(doc: Any) -> str:
    metadata = getattr(doc, "metadata", {}) or {}
    chunk_id = str(metadata.get("chunk_id") or "").strip()
    if chunk_id:
        return chunk_id
    text = (getattr(doc, "page_content", "") or "").strip()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _dense_search(vectorstore: Any, question: str) -> list[tuple[Any, float]]:
    try:
        results = vectorstore.similarity_search_with_relevance_scores(
            question,
            k=RAG_DENSE_K,
        )
        return [(doc, float(score)) for doc, score in results]
    except Exception as exc:
        log(
            "rag_dense_exception",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return []


def _fuse_results(
    dense_results: list[tuple[Any, float]],
    lexical_results: list[LexicalHit],
) -> list[Any]:
    from langchain_core.documents import Document

    candidates: dict[str, dict[str, Any]] = {}

    for rank, (doc, relevance) in enumerate(dense_results, start=1):
        key = _doc_key(doc)
        item = candidates.setdefault(
            key,
            {
                "doc": doc,
                "rrf": 0.0,
                "channels": set(),
                "dense_relevance": 0.0,
                "dense_rank": None,
                "lexical_rank": None,
            },
        )
        item["rrf"] += 1.0 / (_RRF_K + rank)
        item["channels"].add("dense")
        item["dense_relevance"] = max(item["dense_relevance"], relevance)
        item["dense_rank"] = rank

    for hit in lexical_results:
        key = hit.chunk_id
        item = candidates.get(key)
        if item is None:
            item = {
                "doc": Document(page_content=hit.text, metadata=dict(hit.metadata)),
                "rrf": 0.0,
                "channels": set(),
                "dense_relevance": 0.0,
                "dense_rank": None,
                "lexical_rank": None,
            }
            candidates[key] = item
        item["rrf"] += 1.0 / (_RRF_K + hit.rank)
        item["channels"].add("lexical")
        item["lexical_rank"] = hit.rank

    accepted: list[dict[str, Any]] = []
    for item in candidates.values():
        channels = item["channels"]
        dense_relevance = item["dense_relevance"]
        lexical_rank = item["lexical_rank"]

        is_relevant = (
            len(channels) >= 2
            or dense_relevance >= RAG_MIN_RELEVANCE
            or (lexical_rank is not None and lexical_rank <= 2)
        )
        if not is_relevant:
            continue

        item["score"] = item["rrf"] + max(0.0, dense_relevance) * 0.01
        metadata = getattr(item["doc"], "metadata", {}) or {}
        metadata = dict(metadata)
        metadata["retrieval_channels"] = ",".join(sorted(channels))
        metadata["retrieval_score"] = round(float(item["score"]), 6)
        item["doc"].metadata = metadata
        accepted.append(item)

    accepted.sort(key=lambda item: item["score"], reverse=True)
    return [item["doc"] for item in accepted[:RAG_TOP_K]]


async def retrieve_documents(question: str) -> list[Any]:
    vectorstore = load_vectorstore()
    if vectorstore is None:
        return []

    clean_question = (question or "").strip()
    if not clean_question:
        return []

    started_at = time.perf_counter()
    chroma_path = _runtime_chroma_path()

    dense_task = asyncio.to_thread(_dense_search, vectorstore, clean_question)
    lexical_task = asyncio.to_thread(
        search_lexical,
        chroma_path,
        clean_question,
        RAG_LEXICAL_K,
    )
    dense_results, lexical_results = await asyncio.gather(dense_task, lexical_task)
    docs = _fuse_results(dense_results, lexical_results)

    log(
        "rag_retrieval",
        docs=len(docs),
        dense_candidates=len(dense_results),
        lexical_candidates=len(lexical_results),
        seconds=round(time.perf_counter() - started_at, 3),
    )
    return docs
