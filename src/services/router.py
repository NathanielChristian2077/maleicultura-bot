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
    reasoning_effort: str = "minimal"


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("_", " ")
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(_normalize(text))

_GREETING_EXACT = {"oi", "ola", "opa", "bom dia", "boa tarde", "boa noite", "e ai", "fala", "salve"}
_THANKS = {"obrigado", "obrigada", "valeu", "muito obrigado", "muito obrigada", "agradecido", "agradecida"}
_FOLLOWUP_PREFIXES = ("e para ", "e no ", "e na ", "e se ", "nesse caso", "neste caso", "e quanto a", "e quanto ao", "e essa", "e esse", "e isso", "essa ", "esse ", "isso", "tambem")
_COMPLEX_MARKERS = ("compare", "comparar", "diferenca entre", "planejamento", "passo a passo", "vantagens e desvantagens", "causas e controle", "diagnostico diferencial")
_APPLE_DOMAIN_TERMS = {"maca", "macas", "macieira", "macieiras", "maleicultura", "pomar", "pomares", "gala", "fuji", "eva", "galaxy", "maxi gala", "pink lady", "cripps pink", "grafolita", "grapholita", "bonagota", "lagarta enroladeira", "cydia", "sarna da macieira", "venturia", "marssonina", "bitter pit", "patulina", "mosca das frutas"}


def _contains_domain_term(normalized: str) -> bool:
    padded = f" {normalized} "
    return any(f" {term} " in padded for term in _APPLE_DOMAIN_TERMS)


def _looks_like_greeting(normalized: str, words: Sequence[str]) -> bool:
    if normalized in _GREETING_EXACT:
        return True
    if not words or len(words) > 7:
        return False
    first = words[0]
    if re.fullmatch(r"oi+e*", first) or re.fullmatch(r"ola+", first):
        return True
    return any(normalized.startswith(prefix) for prefix in ("bom dia", "boa tarde", "boa noite"))


def route_message(text: str) -> RouteDecision:
    normalized = _normalize(text)
    words = _words(text)
    if len(words) <= 18 and normalized.startswith(_FOLLOWUP_PREFIXES):
        return RouteDecision("followup", True, True, "full", 200, "minimal")
    has_domain = _contains_domain_term(normalized)
    is_complex = len(words) >= 36 or text.count("?") >= 2 or any(marker in normalized for marker in _COMPLEX_MARKERS)
    if has_domain:
        return RouteDecision("complex" if is_complex else "technical", True, is_complex, "full", 300 if is_complex else 180, "low" if is_complex else "minimal")
    if _looks_like_greeting(normalized, words) or normalized in _THANKS:
        return RouteDecision("social", False, False, "reception", 90, "minimal")
    return RouteDecision("ambiguous", False, True, "reception", 120, "minimal")


def decision_from_semantic_route(route: str) -> RouteDecision:
    if route == "apple_technical":
        return RouteDecision("technical", True, False, "full", 180)
    if route == "apple_followup":
        return RouteDecision("followup", True, True, "full", 200)
    kind = route if route in {"social", "off_topic", "clarify"} else "clarify"
    return RouteDecision(kind, False, kind == "clarify", "reception", 100)


def build_retrieval_query(current_text: str, history: Sequence[dict[str, Any]], decision: RouteDecision) -> str:
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
