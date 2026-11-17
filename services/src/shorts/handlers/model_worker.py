"""Model task worker. Reserved concurrency on this function = model call concurrency."""
import os

from shorts import tasks

from .sqs_util import process, receive_count

MAX_RECEIVE = int(os.environ.get("MODEL_MAX_RECEIVE", "4"))


def _handle(body: dict, record: dict) -> None:
    body["final_attempt"] = receive_count(record) >= MAX_RECEIVE
    tasks.run(body)


def handler(event, _context):
    return process(event, _handle)
