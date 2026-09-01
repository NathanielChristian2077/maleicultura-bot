import json
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

LEXICAL_DB_FILENAME = "lexical.sqlite3"
_TOKEN_RE = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)
_STOPWORDS = {
    "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos",
    "e", "em", "essa", "esse", "esta", "este", "eu", "na", "nas", "no", "nos",
    "o", "os", "ou", "para", "por", "qual", "que", "se", "um", "uma",
}


@dataclass(frozen=True)
class LexicalHit:
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    rank: int


def lexical_db_path(root_path: str) -> str:
    return os.path.join(root_path, LEXICAL_DB_FILENAME)


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def initialize_lexical_index(root_path: str, rebuild: bool = False) -> sqlite3.Connection:
    os.makedirs(root_path, exist_ok=True)
    path = lexical_db_path(root_path)
    conn = _connect(path)

    if rebuild:
        conn.executescript(
            """
            DROP TRIGGER IF EXISTS chunks_ai;
            DROP TRIGGER IF EXISTS chunks_ad;
            DROP TRIGGER IF EXISTS chunks_au;
            DROP TABLE IF EXISTS chunks_fts;
            DROP TABLE IF EXISTS chunks;
            """
        )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text,
            content='chunks',
            content_rowid='rowid',
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
        END;

        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text)
            VALUES ('delete', old.rowid, old.text);
        END;

        CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text)
            VALUES ('delete', old.rowid, old.text);
            INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
        END;
        """
    )
    conn.commit()
    return conn


def upsert_lexical_documents(
    conn: sqlite3.Connection,
    records: Iterable[tuple[str, str, dict[str, Any]]],
) -> None:
    conn.executemany(
        """
        INSERT INTO chunks(chunk_id, text, metadata_json)
        VALUES (?, ?, ?)
        ON CONFLICT(chunk_id) DO UPDATE SET
            text=excluded.text,
            metadata_json=excluded.metadata_json
        """,
        (
            (chunk_id, text, json.dumps(metadata, ensure_ascii=False))
            for chunk_id, text, metadata in records
        ),
    )
    conn.commit()


def _fts_query(question: str) -> str:
    tokens: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_RE.findall((question or "").lower()):
        token = token.strip("_")
        if len(token) < 3 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        tokens.append(token.replace('"', '""'))
        if len(tokens) >= 10:
            break

    return " OR ".join(f'"{token}"' for token in tokens)


def search_lexical(root_path: str, question: str, limit: int) -> list[LexicalHit]:
    path = lexical_db_path(root_path)
    if not os.path.exists(path) or limit <= 0:
        return []

    query = _fts_query(question)
    if not query:
        return []

    conn = _connect(path)
    try:
        rows = conn.execute(
            """
            SELECT c.chunk_id, c.text, c.metadata_json
            FROM chunks_fts
            JOIN chunks AS c ON c.rowid = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
            ORDER BY bm25(chunks_fts)
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    finally:
        conn.close()

    hits: list[LexicalHit] = []
    for rank, (chunk_id, text, metadata_json) in enumerate(rows, start=1):
        try:
            metadata = json.loads(metadata_json)
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        hits.append(
            LexicalHit(
                chunk_id=str(chunk_id),
                text=str(text),
                metadata=metadata,
                rank=rank,
            )
        )
    return hits
