"""Broker Lambda: invoked asynchronously by the chat API for one turn."""
import logging

from shorts import broker

logging.getLogger().setLevel(logging.INFO)


def handler(event, _context):
    broker.run_turn(event)
    return {"ok": True}
