import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from collections.abc import Iterable
from typing import Any

from config import (
    RAG_CHROMA_COLLECTION,
    RAG_CHROMA_PATH,
    RAG_EMBED_MODEL,
    RAG_JSONL_PATH,
    env,
)
from rag.lexical import initialize_lexical_index, upsert_lexical_documents


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
        try:
            Chroma(
                collection_name=RAG_CHROMA_COLLECTION,
                persist_directory=RAG_CHROMA_PATH,
                embedding_function=embeddings,
            ).delete_collection()
        except ValueError:
            pass
    elif mode != "append":
        raise ValueError("mode must be 'create' or 'append'")

    if mode == "append":
        print("[ingest] Appending to existing vector DB...")

    return Chroma(
        collection_name=RAG_CHROMA_COLLECTION,
        persist_directory=RAG_CHROMA_PATH,
        embedding_function=embeddings,
    )


def clean_chunk_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").replace("\u00ad", "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")

    cleaned_lines: list[str] = []
    previous_nonempty = ""
    blank_pending = False

    for raw_line in value.split("\n"):
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not line:
            blank_pending = bool(cleaned_lines)
            continue

        if line == previous_nonempty:
            continue

        if blank_pending and cleaned_lines and cleaned_lines[-1] != "":
            cleaned_lines.append("")
        cleaned_lines.append(line)
        previous_nonempty = line
        blank_pending = False

    return "\n".join(cleaned_lines).strip()


def _page_human(value: Any) -> Any:
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()) + 1
    return value


def _fallback_title(chunk: dict[str, Any]) -> str:
    title = str(chunk.get("titulo") or "").strip()
    if title:
        return title
    source = str(chunk.get("fonte") or chunk.get("doc_id") or "Documento").strip()
    return re.sub(r"\.pdf$", "", source, flags=re.IGNORECASE)


def _metadata_from_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in (
        "chunk_id",
        "doc_id",
        "fonte",
        "path_pdf",
        "chunk_index",
        "tipo",
    ):
        value = chunk.get(key)
        if value is not None:
            metadata[key] = value

    metadata["titulo"] = _fallback_title(chunk)
    if chunk.get("pagina") is not None:
        metadata["pagina_indice"] = chunk.get("pagina")
        metadata["pagina"] = _page_human(chunk.get("pagina"))
    return metadata


def contextualize_chunk(text: str, metadata: dict[str, Any]) -> str:
    header: list[str] = []
    if metadata.get("titulo"):
        header.append(f"Título: {metadata['titulo']}")
    if metadata.get("fonte"):
        header.append(f"Fonte: {metadata['fonte']}")
    if metadata.get("pagina") not in (None, ""):
        header.append(f"Página: {metadata['pagina']}")

    return ("\n".join(header) + "\n\n" + text).strip() if header else text


def _content_fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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
    lexical_conn = initialize_lexical_index(RAG_CHROMA_PATH, rebuild=mode == "create")

    existing_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    if mode == "append":
        print("[ingest] Loading existing chunks for dedup...")
        existing_docs = vectorstore.get(include=["documents"])
        existing_ids = set(existing_docs.get("ids") or [])
        seen_fingerprints = {
            _content_fingerprint(text)
            for text in (existing_docs.get("documents") or [])
            if text
        }
        print(f"[ingest] Existing chunks: {len(existing_ids)}")

    total_lines = count_lines(RAG_JSONL_PATH)
    print(f"[ingest] Total chunks in file: {total_lines}")

    buffer_texts: list[str] = []
    buffer_ids: list[str] = []
    buffer_meta: list[dict[str, Any]] = []
    total_added = 0
    total_skipped = 0

    def flush() -> int:
        if not buffer_texts:
            return 0
        vectorstore.add_texts(texts=buffer_texts, ids=buffer_ids, metadatas=buffer_meta)
        upsert_lexical_documents(
            lexical_conn,
            zip(buffer_ids, buffer_texts, buffer_meta),
        )
        count = len(buffer_texts)
        buffer_texts.clear()
        buffer_ids.clear()
        buffer_meta.clear()
        return count

    try:
        for index, chunk in enumerate(load_jsonl_stream(RAG_JSONL_PATH), start=1):
            chunk_id = str(chunk.get("chunk_id") or "").strip()
            clean_text = clean_chunk_text(str(chunk.get("texto") or ""))

            if not chunk_id or not clean_text:
                total_skipped += 1
                continue

            metadata = _metadata_from_chunk({**chunk, "chunk_id": chunk_id})
            contextual_text = contextualize_chunk(clean_text, metadata)
            fingerprint = _content_fingerprint(contextual_text)

            if chunk_id in existing_ids or fingerprint in seen_fingerprints:
                total_skipped += 1
                continue

            seen_fingerprints.add(fingerprint)
            buffer_texts.append(contextual_text)
            buffer_ids.append(chunk_id)
            buffer_meta.append(metadata)

            if len(buffer_texts) >= batch_size:
                total_added += flush()
                print(
                    f"[ingest] {index}/{total_lines} chunks processed; "
                    f"added={total_added}; skipped={total_skipped}"
                )

        total_added += flush()
    finally:
        lexical_conn.close()

    elapsed = time.time() - started_at
    print("[ingest] Completed.")
    print(f"[ingest] Added chunks: {total_added}")
    print(f"[ingest] Skipped chunks: {total_skipped}")
    print(f"[ingest] Total time: {elapsed:.2f}s ({elapsed / 60:.2f}min)")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "create"
    ingest(mode=mode)
