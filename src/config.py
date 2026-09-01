import os

SYSTEM_PROMPT = """
Você é um consultor agrícola especializado em produção e manejo de maçãs.
Seu papel é orientar produtores sobre plantio, irrigação, poda, controle de pragas,
colheita e comercialização.

Responda de forma clara, prática e técnica, com foco em aumentar a produtividade
e a qualidade das maçãs, reduzindo custos e impactos ambientais.

Dê dicas objetivas baseadas em boas práticas agrícolas e experiências reais no campo.
""".strip()


# ============================================================
# WhatsApp limits / UI
# ============================================================

MAX_WA_TEXT = 4096

MENU_TITLE_MAX = 20
MENU_ID_MAX = 256
MENU_BODY_MAX = 1024


# ============================================================
# API / Runtime config
# ============================================================

GRAPH_DEFAULT_VERSION = os.getenv("GRAPH_API_VERSION", "v23.0")
GPT5_RAG_MODEL = os.getenv("GPT5_RAG_MODEL", "gpt-5-2025-08-07")


# ============================================================
# TTLs
# ============================================================

DEDUP_TTL_SEC = int(os.getenv("DEDUP_TTL_SEC", "600"))
STATE_TTL_SEC = int(os.getenv("STATE_TTL_SEC", "1800"))


# ============================================================
# DynamoDB config
# ============================================================

CONV_TABLE = os.getenv("CONV_TABLE", "conversations")
CONV_TOKEN_LIMIT = int(os.getenv("CONV_TOKEN_LIMIT", "2000"))
CONV_TTL_DAYS = int(os.getenv("CONV_TTL_DAYS", "7"))


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
