"""Minimal router for API Gateway HTTP API (payload v2) Lambda proxy events."""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Callable

from .auth import AuthError, Forbidden, Principal, principal_from_event

log = logging.getLogger(__name__)


class BadRequest(Exception):
    status = 400


class Conflict(Exception):
    status = 409


class NotFound(Exception):
    status = 404


Handler = Callable[["Request"], object]


class Request:
    def __init__(self, event: dict, params: dict, principal: Principal):
        self.event = event
        self.params = params
        self.principal = principal
        self.query = event.get("queryStringParameters") or {}
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        try:
            self.body = json.loads(raw) if raw else {}
        except ValueError:
            raise BadRequest("Body must be JSON")
        if not isinstance(self.body, dict):
            raise BadRequest("Body must be a JSON object")

    def require(self, *names: str) -> list:
        missing = [n for n in names if self.body.get(n) in (None, "")]
        if missing:
            raise BadRequest(f"Missing fields: {', '.join(missing)}")
        return [self.body[n] for n in names]


class Router:
    def __init__(self):
        self.routes: list[tuple[str, re.Pattern, Handler]] = []

    def route(self, method: str, path: str):
        pattern = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$")

        def deco(fn: Handler) -> Handler:
            self.routes.append((method, pattern, fn))
            return fn
        return deco

    def handle(self, event: dict) -> dict:
        method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
        path = event.get("rawPath", "/")
        try:
            for m, pattern, fn in self.routes:
                match = pattern.match(path)
                if match and m == method:
                    principal = principal_from_event(event)
                    result = fn(Request(event, match.groupdict(), principal))
                    return respond(200 if result is not None else 204, result)
            raise NotFound("Route not found")
        except (AuthError, Forbidden, BadRequest, Conflict, NotFound) as exc:
            return respond(exc.status, {"message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status", None)
            if isinstance(status, int):
                return respond(status, {"message": str(exc)})
            if isinstance(exc, ValueError):
                return respond(400, {"message": str(exc)})
            log.exception("Unhandled error on %s %s", method, path)
            return respond(500, {"message": "Internal error"})


def respond(status: int, body: object | None) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body, ensure_ascii=False, default=str) if body is not None else "",
    }
