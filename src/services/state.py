import time
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from config import CONV_TABLE, DEDUP_TTL_SEC
from utils.logging import log


_seen_wamids: dict[str, float] = {}
_ddb = boto3.client("dynamodb")


def dedup_gc() -> None:
    now = time.time()
    expired = [k for k, ts in _seen_wamids.items() if now - ts > DEDUP_TTL_SEC]
    for k in expired:
        _seen_wamids.pop(k, None)


def _ttl_epoch_seconds() -> int:
    return int(time.time()) + DEDUP_TTL_SEC


def seen_before(wamid: Optional[str]) -> bool:
    dedup_gc()

    if not wamid:
        return False

    now = time.time()

    if wamid in _seen_wamids:
        return True

    dedup_key = f"dedup#{wamid}"

    try:
        _ddb.put_item(
            TableName=CONV_TABLE,
            Item={
                "wa_from": {"S": dedup_key},
                "ts": {"N": "0"},
                "role": {"S": "dedup"},
                "content": {"S": wamid},
                "ttl": {"N": str(_ttl_epoch_seconds())},
            },
            ConditionExpression=(
                "attribute_not_exists(wa_from) AND attribute_not_exists(ts)"
            ),
        )

        _seen_wamids[wamid] = now
        return False

    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")

        if code == "ConditionalCheckFailedException":
            _seen_wamids[wamid] = now
            return True

        log(
            "dedup_ddb_exception",
            error_type=type(exc).__name__,
            error=str(exc),
            wamid=wamid,
        )

        # Fallback: do not block processing if DynamoDB has a transient issue.
        _seen_wamids[wamid] = now
        return False