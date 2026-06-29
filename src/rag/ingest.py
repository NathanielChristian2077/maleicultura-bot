import json
import os
import sys
import time
from collections.abc import Iterable
from typing import Any

from config import (
    RAG_CHROMA_COLLECTION,
    RAG_CHROMA_PATH,
    RAG_EMBED_MODEL,
    RAG_JSONL_PATH,
    env,
)


def count_lines(path: str) -> int:
    with open(path, "r", encoding="utf-8") as f:
        return sum(1 for _ in f)


def load_jsonl_stream(path: str) -> Iterable[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def ensure_db(mode: str, embeddings: Any) -> Any:
    from langchain_chroma import Chroma

    os.makedirs(RAG_CHROMA_PATH, exist_ok=True)

    if mode == "create":
        print("[ingest] Rebuilding vector DB from scratch...")
        Chroma(
            collection_name=RAG_CHROMA_COLLECTION,
            persist_directory=RAG_CHROMA_PATH,
            embedding_function=embeddings,
        ).delete_collection()
    elif mode != "append":
        raise ValueError("mode must be 'create' or 'append'")

    if mode == "append":
        print("[ingest] Appending to existing vector DB...")

    return Chroma(
        collection_name=RAG_CHROMA_COLLECTION,
        persist_directory=RAG_CHROMA_PATH,
        embedding_function=embeddings,
    )


def _metadata_from_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in (
        "doc_id",
        "titulo",
        "pagina",
        "fonte",
        "path_pdf",
        "chunk_index",
        "tipo",
    ):
        value = chunk.get(key)
        if value is not None:
            metadata[key] = value
    return metadata


def ingest(mode: str = "create", batch_size: int = 128) -> None:
    if not env("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required to create embeddings")

    if not os.path.exists(RAG_JSONL_PATH):
        raise FileNotFoundError(f"RAG JSONL not found: {RAG_JSONL_PATH}")

    from langchain_openai import OpenAIEmbeddings

    started_at = time.time()
    print(f"[ingest] Mode: {mode}")
    print(f"[ingest] JSONL: {RAG_JSONL_PATH}")
    print(f"[ingest] Chroma path: {RAG_CHROMA_PATH}")
    print(f"[ingest] Collection: {RAG_CHROMA_COLLECTION}")

    embeddings = OpenAIEmbeddings(model=RAG_EMBED_MODEL, api_key=env("OPENAI_API_KEY"))
    vectorstore = ensure_db(mode, embeddings)

    existing_ids: set[str] = set()
    if mode == "append":
        print("[ingest] Loading existing chunk IDs for dedup...")
        existing_docs = vectorstore.get(include=["metadatas"])
        existing_ids = set(existing_docs.get("ids") or [])
        print(f"[ingest] Existing chunks: {len(existing_ids)}")

    total_lines = count_lines(RAG_JSONL_PATH)
    print(f"[ingest] Total chunks in file: {total_lines}")

    buffer_texts: list[str] = []
    buffer_ids: list[str] = []
    buffer_meta: list[dict[str, Any]] = []
    total_added = 0
    total_skipped = 0

    for index, chunk in enumerate(load_jsonl_stream(RAG_JSONL_PATH), start=1):
        chunk_id = str(chunk.get("chunk_id") or "").strip()
        text = str(chunk.get("texto") or "").strip()

        if not chunk_id or not text:
            total_skipped += 1
            continue

        if chunk_id in existing_ids:
            total_skipped += 1
            continue

        buffer_texts.append(text)
        buffer_ids.append(chunk_id)
        buffer_meta.append(_metadata_from_chunk(chunk))

        if len(buffer_texts) >= batch_size:
            vectorstore.add_texts(
                texts=buffer_texts, ids=buffer_ids, metadatas=buffer_meta
            )
            total_added += len(buffer_texts)
            buffer_texts.clear()
            buffer_ids.clear()
            buffer_meta.clear()
            print(
                f"[ingest] {index}/{total_lines} chunks processed; added={total_added}; skipped={total_skipped}"
            )

    if buffer_texts:
        vectorstore.add_texts(texts=buffer_texts, ids=buffer_ids, metadatas=buffer_meta)
        total_added += len(buffer_texts)

    elapsed = time.time() - started_at
    print("[ingest] Completed.")
    print(f"[ingest] Added chunks: {total_added}")
    print(f"[ingest] Skipped chunks: {total_skipped}")
    print(f"[ingest] Total time: {elapsed:.2f}s ({elapsed / 60:.2f}min)")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "create"
    ingest(mode=mode)
