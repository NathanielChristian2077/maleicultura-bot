import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_app_path(name: str, default: str) -> str:
    raw = os.getenv(name, default)
    if os.path.isabs(raw):
        return raw
    return os.path.abspath(os.path.join(APP_DIR, raw))


def _int_env(name: str, default: str) -> int:
    return int(os.getenv(name, default))


SYSTEM_PROMPT = """
Você é um consultor agrícola especializado em produção e manejo de maçãs, com foco em ajudar produtores rurais, considerando o contexto produtivo da região sul do Brasil. Seu papel é orientar produtores sobre plantio, irrigação, poda, controle de pragas, colheita, comercialização e qualquer outro aspecto da produção de maçãs. Responda e forneça recomendações baseadas em práticas agrícolas comprovadas e adaptadas às condições dadas. Responda de forma clara, concisa, curta e prática, em único parágrafo com poucas frases de maneira simples e resumido, sem markdown ou outras formatações.
""".strip()


# ============================================================
# WhatsApp limits
# ============================================================

MAX_WA_TEXT = 4096


# ============================================================
# API / Runtime config
# ============================================================

GRAPH_DEFAULT_VERSION = os.getenv("GRAPH_API_VERSION", "v24.0")
GPT5_RAG_MODEL = os.getenv("GPT5_RAG_MODEL", "gpt-5-2025-08-07")


# ============================================================
# RAG config
# ============================================================

RAG_EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "text-embedding-3-small")
RAG_CHROMA_COLLECTION = os.getenv("RAG_CHROMA_COLLECTION", "chunks")
RAG_CHROMA_PATH = _resolve_app_path("RAG_CHROMA_PATH", "chroma_db")
RAG_JSONL_PATH = _resolve_app_path(
    "RAG_JSONL_PATH",
    os.path.join("..", "data", "chunks_out.jsonl"),
)
RAG_TOP_K = _int_env("RAG_TOP_K", "3")


# ============================================================
# TTLs
# ============================================================

DEDUP_TTL_SEC = _int_env("DEDUP_TTL_SEC", "600")


# ============================================================
# DynamoDB config
# ============================================================

CONV_TABLE = os.getenv("CONV_TABLE", "conversations")
CONV_TTL_DAYS = _int_env("CONV_TTL_DAYS", "7")


# ============================================================
# Environment helpers
# ============================================================


def env(name: str, default: str = "") -> str:
    fallback_map = {
        "WHATSAPP_VERIFY_TOKEN": ["VERIFY_TOKEN"],
        "WHATSAPP_TOKEN": ["WABA_TOKEN"],
        "WHATSAPP_PHONE_NUMBER_ID": ["PHONE_NUMBER_ID"],
        "GRAPH_API_VERSION": ["GRAPH_VERSION"],
        "DRY_RUN": [],
    }

    val = os.getenv(name)

    if val is None:
        for fb in fallback_map.get(name, []):
            val = os.getenv(fb)
            if val is not None:
                break

    return val if val is not None else default


def clean_token(raw: str) -> str:
    return (raw or "").replace("\r", "").replace("\n", "").strip().strip('"').strip("'")


def cfg() -> dict:
    token = clean_token(env("WHATSAPP_TOKEN"))

    return {
        "VERIFY_TOKEN": env("WHATSAPP_VERIFY_TOKEN"),
        "WABA_TOKEN": token,
        "PHONE_NUMBER_ID": env("WHATSAPP_PHONE_NUMBER_ID"),
        "GRAPH_VERSION": env("GRAPH_API_VERSION", GRAPH_DEFAULT_VERSION),
        "DRY_RUN": env("DRY_RUN", "false").lower() == "true",
    }
