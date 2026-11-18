"""Broker Lambda: invoked asynchronously by the chat API for one turn."""
import logging

from shorts import broker

logging.getLogger().setLevel(logging.INFO)


def handler(event, context):
    import os
    if getattr(context, "invoked_function_arn", None):
        os.environ.setdefault("BROKER_FUNCTION", context.invoked_function_arn)
    if event.get("kind") == "topic_batch":  # long-running topic-series planning (API Gateway would time out)
        from .. import batches
        batches.run_topic_batch(event)
        return {"ok": True}
    broker.run_turn(event)
    return {"ok": True}
