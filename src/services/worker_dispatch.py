import json
import os
from typing import Any

import boto3

from utils.logging import log


_lambda = boto3.client("lambda")


def dispatch_worker(payload: dict[str, Any]) -> bool:
    worker_name = os.getenv("WORKER_FUNCTION_NAME")

    if not worker_name:
        log("worker_dispatch_missing_function_name")
        return False

    response = _lambda.invoke(
        FunctionName=worker_name,
        InvocationType="Event",
        Payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )

    status_code = response.get("StatusCode")

    log(
        "worker_dispatched",
        worker_name=worker_name,
        status_code=status_code,
        kind=payload.get("kind"),
        wa_from=payload.get("wa_from"),
    )

    return status_code == 202