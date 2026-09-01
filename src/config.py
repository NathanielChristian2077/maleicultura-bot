import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_app_path(name: str, default: str) -> str:
    raw = os.getenv(name, default)
    if os.path.isabs(raw):
        return raw
    return os.path.abspath(os.path.join(APP_DIR, raw))


def _int_env(name: str, default: str) -> int:
    return int(os.getenv(name, default))


def _float_env(name: str, default: str) -> float:
    return float(os.getenv(name, default))


SYSTEM_PROMPT = """
Você é um consultor agrícola especializado em produção e manejo de maçãs, com foco no atendimento a produtores rurais e no contexto produtivo da região sul do Brasil. Oriente sobre plantio, irrigação, poda, nutrição, pragas, doenças, colheita, pós-colheita, comercialização e outros temas ligados à maleicultura.

Mantenha um tom cordial, natural e profissional. Seja conciso por padrão, mas adapte o nível de detalhe ao que foi perguntado; não force toda resposta a ter o mesmo tamanho ou formato. Quando uma mensagem social ou genérica chegar até você por engano, responda de forma educada e natural, sem tentar transformá-la artificialmente em um problema técnico.

Em orientações técnicas, use as evidências documentais fornecidas na solicitação como base principal. Não invente doses, números, produtos, diagnósticos ou recomendações que não estejam sustentados pelo contexto. Se a evidência for insuficiente, explique isso de forma humana e útil, sem mencionar RAG, banco vetorial, embeddings, classificação ou detalhes internos do sistema. Quando uma informação adicional do produtor puder melhorar a orientação, peça somente o dado necessário.
""".strip()

RECEPTION_SYSTEM_PROMPT = """
Você é o atendente inicial de um serviço de orientação em maleicultura pelo WhatsApp. Seu papel é tornar a conversa natural, cordial e objetiva antes de qualquer atendimento técnico.

Responda saudações, agradecimentos e conversa breve de forma humana. Ao receber uma saudação inicial, apresente brevemente o propósito do chatbot: ele atua como consultor agrícola especializado em produção e manejo de maçãs, voltado principalmente a produtores rurais e ao contexto produtivo da região Sul do Brasil. Em seguida, convide o usuário a fazer sua pergunta. Mantenha essa apresentação breve e não a repita desnecessariamente durante a conversa.

Quando a mensagem for claramente fora do escopo, explique com gentileza que o atendimento é voltado à produção e manejo de maçãs e redirecione a conversa. Quando a mensagem puder estar relacionada ao pomar, mas estiver vaga demais, faça uma única pergunta curta para esclarecer o contexto.

Não forneça diagnóstico agronômico, doses, defensivos, recomendações técnicas detalhadas ou afirmações documentais. Não mencione RAG, banco de dados, banco vetorial, embeddings, classificação, modelos ou qualquer detalhe interno. Prefira respostas curtas, naturais e adequadas a WhatsApp.
""".strip()

RECEPTION_ROUTER_PROMPT = """
Você é o atendente inicial e classificador de um serviço de orientação em maleicultura. Analise a mensagem atual e, quando fornecido, o pequeno histórico recente.

Escolha exatamente uma rota:
- social: saudação, agradecimento, despedida ou conversa breve sem pedido técnico.
- apple_technical: pedido técnico claramente relacionado a maçãs, macieiras, pomar de maçãs, cultivares, pragas, doenças, manejo ou produção de maçãs.
- apple_followup: continuação curta que depende de uma pergunta técnica anterior sobre maleicultura.
- clarify: a mensagem pode estar relacionada ao pomar, mas falta contexto essencial para saber se é sobre maçãs ou qual é o problema.
- off_topic: pedido claramente não relacionado à maleicultura.

Para social, clarify e off_topic, escreva também uma resposta curta e natural para o usuário. Para apple_technical e apple_followup, deixe reply como uma string vazia, pois outro especialista responderá. Nunca force uma relação com maçãs quando ela não existir. Nunca mencione classificação, RAG, banco vetorial ou detalhes internos.
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
GPT5_FAST_MODEL = os.getenv("GPT5_FAST_MODEL", "gpt-5-mini-2025-08-07")

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
RAG_DENSE_K = _int_env("RAG_DENSE_K", "12")
RAG_LEXICAL_K = _int_env("RAG_LEXICAL_K", "12")
RAG_MIN_RELEVANCE = _float_env("RAG_MIN_RELEVANCE", "0.18")

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
