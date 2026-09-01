import time
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from config import CONV_TABLE, CONV_TTL_DAYS, SYSTEM_PROMPT
from utils.logging import log


_ddb = boto3.client("dynamodb")


def _ts_ms() -> int:
    return int(time.time() * 1000)


def _ttl_epoch_seconds(days: int) -> int:
    # DynamoDB TTL usa epoch em segundos.
    return int(time.time()) + days * 86400


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
    """
    Conversation summarization is intentionally disabled.

    The production article and runtime behavior rely on a single system prompt,
    defined in src/config.py as SYSTEM_PROMPT. Keeping summarization disabled
    prevents an auxiliary model instruction from becoming a second prompt path.
    """
    return None


async def maybe_summarize_with_gpt5_rag(wa_from: str) -> None:
    """
    No-op by design.

    Do not add model instructions here. The only system prompt used by the bot
    must remain SYSTEM_PROMPT from src/config.py.
    """
    return None


def build_context_block(
    wa_from: str,
    max_history: int = 20,
) -> tuple[str, list[dict[str, Any]], Optional[str]]:
    history = fetch_messages(wa_from, max_history)
    return SYSTEM_PROMPT, history, None
