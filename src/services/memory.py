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
    return int(time.time()) + days * 86400


def _message_item(wa_from: str, ts: int, role: str, content: str) -> dict[str, Any]:
    return {
        "wa_from": {"S": wa_from},
        "ts": {"N": str(ts)},
        "role": {"S": role},
        "content": {"S": content or ""},
        "ttl": {"N": str(_ttl_epoch_seconds(CONV_TTL_DAYS))},
    }


def save_message(wa_from: str, role: str, content: str) -> None:
    try:
        _ddb.put_item(
            TableName=CONV_TABLE,
            Item=_message_item(wa_from, _ts_ms(), role, content),
        )
    except ClientError as e:
        log("ddb_put_err", error=str(e))


def save_exchange(wa_from: str, user_text: str, assistant_text: str) -> None:
    base_ts = _ts_ms()
    try:
        response = _ddb.batch_write_item(
            RequestItems={
                CONV_TABLE: [
                    {
                        "PutRequest": {
                            "Item": _message_item(wa_from, base_ts, "user", user_text)
                        }
                    },
                    {
                        "PutRequest": {
                            "Item": _message_item(
                                wa_from,
                                base_ts + 1,
                                "assistant",
                                assistant_text,
                            )
                        }
                    },
                ]
            }
        )
        unprocessed = response.get("UnprocessedItems", {}).get(CONV_TABLE, [])
        if unprocessed:
            log("ddb_batch_unprocessed", count=len(unprocessed), wa_from=wa_from)
    except ClientError as e:
        log("ddb_batch_err", error=str(e), wa_from=wa_from)


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
    return None


async def maybe_summarize_with_gpt5_rag(wa_from: str) -> None:
    return None


def build_context_block(
    wa_from: str,
    max_history: int = 20,
) -> tuple[str, list[dict[str, Any]], Optional[str]]:
    history = fetch_messages(wa_from, max_history)
    return SYSTEM_PROMPT, history, None
