import asyncio
import time
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from config import CONV_TABLE, CONV_TOKEN_LIMIT, CONV_TTL_DAYS, GPT5_RAG_MODEL, SYSTEM_PROMPT, env
from utils.logging import log


_ddb = boto3.client("dynamodb")


def _ts_ms() -> int:
    return int(time.time() * 1000)


def _ttl_epoch_seconds(days: int) -> int:
    # DynamoDB TTL usa epoch em segundos.
    return int(time.time()) + days * 86400


def _approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text.split()) / 0.75))


def save_message(wa_from: str, role: str, content: str) -> None:
    try:
        _ddb.put_item(
            TableName=CONV_TABLE,
            Item={
                "wa_from": {"S": wa_from},
                "ts": {"N": str(_ts_ms())},
                "role": {"S": role},
                "content": {"S": content or ""},
                "ttl": {"N": str(_ttl_epoch_seconds(CONV_TTL_DAYS))},
            },
        )
    except ClientError as e:
        log("ddb_put_err", error=str(e))


def fetch_messages(wa_from: str, limit: int = 50) -> list[dict[str, Any]]:
    try:
        resp = _ddb.query(
            TableName=CONV_TABLE,
            KeyConditionExpression="wa_from = :w",
            ExpressionAttributeValues={":w": {"S": wa_from}},
            Limit=limit,
            ScanIndexForward=False,
        )
        items = list(reversed(resp.get("Items", [])))
        return [
            {
                "ts": int(it["ts"]["N"]),
                "role": it["role"]["S"],
                "content": it["content"]["S"],
            }
            for it in items
        ]
    except ClientError as e:
        log("ddb_query_err", error=str(e))
        return []


def latest_summary(wa_from: str, limit: int = 120) -> Optional[str]:
    hist = fetch_messages(wa_from, limit)
    for rec in reversed(hist):
        if rec["role"] == "system_summary":
            return rec["content"]
    return None


async def maybe_summarize_with_gpt5_rag(wa_from: str) -> None:
    history = fetch_messages(wa_from, 120)
    joined = "\n".join(f"{r['role']}: {r['content']}" for r in history)
    total = _approx_tokens(joined)

    if total <= CONV_TOKEN_LIMIT:
        return

    log("token_limit_hit", wa_from=wa_from, tokens=total)

    from openai import OpenAI

    client = OpenAI(api_key=env("OPENAI_API_KEY"))
    instructions = (
        "Resuma o diálogo recebido de forma breve, preservando o contexto e as "
        "informações importantes para as próximas respostas."
    )

    try:
        res = await asyncio.to_thread(
            lambda: client.responses.create(
                model=GPT5_RAG_MODEL,
                reasoning={"effort": "minimal"},
                instructions=instructions,
                input=joined,
                max_output_tokens=500,
            )
        )
        summary = getattr(res, "output_text", "") or ""
        summary = summary.strip()

        if summary:
            save_message(wa_from, "system_summary", summary[:2000])
            log("summary_created", wa_from=wa_from, length=len(summary))
    except Exception as e:
        log("summary_exception", wa_from=wa_from, error=str(e))


def build_context_block(
    wa_from: str,
    max_history: int = 20,
) -> tuple[str, list[dict[str, Any]], Optional[str]]:
    history = fetch_messages(wa_from, max_history)
    summary = latest_summary(wa_from)
    return SYSTEM_PROMPT, history, summary
