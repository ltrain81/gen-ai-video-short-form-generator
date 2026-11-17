"""Polls long-running jobs (Marengo async, Transcribe). Re-enqueues with delay until finished."""
from shorts import ingest, tasks
from shorts.config import get_config

from .sqs_util import process

MAX_POLLS = 360  # ~2h at 20s


def _handle(body: dict, _record: dict) -> None:
    done = tasks.marengo_poll(body) if body["poll"] == "marengo" else tasks.transcribe_poll(body)
    if done:
        return
    body["polls"] = body.get("polls", 0) + 1
    if body["polls"] > MAX_POLLS:
        stage = f"{body['poll']}:{body['chunk']}"
        ingest.on_stage_done(body["contentKey"], stage, "failed", "Timed out waiting for job")
        return
    ingest.send(get_config().poll_queue_url, body, tasks.POLL_DELAY_SEC)


def handler(event, _context):
    return process(event, _handle)
