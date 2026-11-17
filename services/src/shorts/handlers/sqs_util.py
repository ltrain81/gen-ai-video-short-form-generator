"""SQS batch handling with partial batch failure reporting."""
from __future__ import annotations

import json
import logging
from typing import Callable

log = logging.getLogger()
log.setLevel(logging.INFO)


def process(event: dict, fn: Callable[[dict, dict], None]) -> dict:
    failures = []
    for record in event.get("Records", []):
        try:
            fn(json.loads(record["body"]), record)
        except Exception:  # noqa: BLE001
            log.exception("Record %s failed", record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def receive_count(record: dict) -> int:
    return int(record.get("attributes", {}).get("ApproximateReceiveCount", "1"))
