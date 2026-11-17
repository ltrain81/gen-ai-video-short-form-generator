"""Polls SageMaker async generation outputs; re-enqueues until done."""
import os

from shorts import generation, ingest

from .sqs_util import process


def _handle(body: dict, _record: dict) -> None:
    if not generation.poll(body):
        ingest.send(os.environ["GEN_POLL_QUEUE_URL"], body, generation.POLL_DELAY)


def handler(event, _context):
    return process(event, _handle)
