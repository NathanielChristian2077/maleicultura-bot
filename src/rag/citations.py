import re
from collections.abc import Sequence
from typing import Any

_CITATION_RE = re.compile(r"\[(\d+)\]")


def _meta(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    return "" if value is None else str(value).strip()


def format_source(index: int, doc: Any) -> str:
    metadata = getattr(doc, "metadata", {}) or {}
    title = _meta(metadata, "titulo")
    source = _meta(metadata, "fonte") or _meta(metadata, "doc_id")
    page = _meta(metadata, "pagina")

    label = title or source or f"Documento {index}"
    details: list[str] = []
    if source and source != label:
        details.append(source)
    details.append(f"https://github.com/ProjetoChatMaca/documentos-maleicultura/blob/main/Articles/{source}")
    if page:
        details.append(f"p. {page}")

    suffix = f" ({', '.join(details)})" if details else ""
    return f"[{index}] {label}{suffix}"


def append_sources(reply: str, docs: Sequence[Any]) -> str:
    text = (reply or "").strip()
    if not docs:
        return text

    valid = set(range(1, len(docs) + 1))
    referenced = {
        int(match.group(1))
        for match in _CITATION_RE.finditer(text)
        if int(match.group(1)) in valid
    }
    selected = sorted(referenced) if referenced else list(range(1, len(docs) + 1))
    sources = "; ".join(format_source(index, docs[index - 1]) for index in selected)
    return f"{text}\n\nFontes: {sources}".strip()
