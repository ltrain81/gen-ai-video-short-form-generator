"""Ingest coordinator: S3 ObjectCreated (via EventBridge→SQS) and internal ingest events."""
from shorts import ingest

from .sqs_util import process


def _handle(body: dict, _record: dict) -> None:
    if body.get("detail-type") == "Object Created":
        detail = body["detail"]
        ingest.on_object_created(detail["bucket"]["name"], detail["object"]["key"])
    elif body.get("type") == "prepare.done":
        ingest.on_prepared(body)
    elif body.get("type") == "prepare.failed":
        ingest.on_prepare_failed(body)
    elif body.get("type") == "render.request":
        ingest.run_media_worker({"job": "render", "projectId": body["projectId"], "renderId": body["renderId"]})
    elif body.get("type") == "stage.done":
        ingest.on_stage_done(body["contentKey"], body["stage"], body.get("status", "done"), body.get("error"),
                             body.get("lang"))
    else:
        raise ValueError(f"Unknown ingest event: {list(body)[:5]}")


def handler(event, _context):
    return process(event, _handle)
