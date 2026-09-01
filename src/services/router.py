import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Sequence

_WORD_RE = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)


@dataclass(frozen=True)
class RouteDecision:
    kind: str
    use_rag: bool
    include_history: bool
    model_tier: str
    max_output_tokens: int
    static_reply: str | None = None


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(_normalize(text))


_GREETING = {
    "oi",
    "ola",
    "bom dia",
    "boa tarde",
    "boa noite",
    "e ai",
}

_THANKS = {
    "obrigado",
    "obrigada",
    "valeu",
    "muito obrigado",
    "muito obrigada",
    "agradecido",
}

_FOLLOWUP_PREFIXES = (
    "e para ",
    "e no ",
    "e na ",
    "e se ",
    "nesse caso",
    "neste caso",
    "e quanto a",
    "e quanto ao",
    "isso",
    "essa ",
    "esse ",
    "tambem",
)

_COMPLEX_MARKERS = (
    "compare",
    "comparar",
    "diferenca entre",
    "diferença entre",
    "planejamento",
    "passo a passo",
    "vantagens e desvantagens",
    "causas e controle",
    "diagnostico diferencial",
    "diagnóstico diferencial",
)


def route_message(text: str) -> RouteDecision:
    normalized = _normalize(text)
    words = _words(text)

    if normalized in _GREETING:
        return RouteDecision(
            kind="social",
            use_rag=False,
            include_history=False,
            model_tier="none",
            max_output_tokens=0,
            static_reply="Olá! Envie sua dúvida sobre produção ou manejo de maçãs.",
        )

    if normalized in _THANKS:
        return RouteDecision(
            kind="social",
            use_rag=False,
            include_history=False,
            model_tier="none",
            max_output_tokens=0,
            static_reply="Disponha. Quando precisar, envie outra dúvida sobre o pomar.",
        )

    if len(words) <= 16 and normalized.startswith(_FOLLOWUP_PREFIXES):
        return RouteDecision(
            kind="followup",
            use_rag=True,
            include_history=True,
            model_tier="fast",
            max_output_tokens=180,
        )

    is_complex = (
        len(words) >= 36
        or text.count("?") >= 2
        or any(marker in normalized for marker in _COMPLEX_MARKERS)
    )
    if is_complex:
        return RouteDecision(
            kind="complex",
            use_rag=True,
            include_history=True,
            model_tier="full",
            max_output_tokens=280,
        )

    return RouteDecision(
        kind="technical",
        use_rag=True,
        include_history=False,
        model_tier="fast",
        max_output_tokens=140,
    )


def build_retrieval_query(
    current_text: str,
    history: Sequence[dict[str, Any]],
    decision: RouteDecision,
) -> str:
    current = (current_text or "").strip()
    if not decision.include_history or not history:
        return current

    previous_user = ""
    for item in reversed(history):
        if item.get("role") == "user" and (item.get("content") or "").strip():
            previous_user = str(item["content"]).strip()
            break

    if not previous_user or previous_user == current:
        return current

    if decision.kind == "followup":
        return f"{previous_user}\nContinuação: {current}"

    return current
