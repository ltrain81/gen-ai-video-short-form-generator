"""Realtime events to the browser via AppSync Events (publish with SigV4/IAM; browsers subscribe with Cognito).

Channel per project & user: /projects/{projectId}/{sub}. The namespace's onSubscribe handler only lets a
Cognito identity subscribe to channels ending in its own sub.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

from . import safeurl

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

log = logging.getLogger(__name__)
MAX_EVENTS_PER_PUBLISH = 5
MAX_EVENT_BYTES = 240_000


def channel(project_id: str, sub: str) -> str:
    return f"/projects/{project_id}/{sub}"


def publish(chan: str, events: list[dict]) -> None:
    domain = os.environ.get("EVENTS_HTTP_DOMAIN")
    if not domain:
        log.debug("EVENTS_HTTP_DOMAIN not set; dropping %d events", len(events))
        return
    session = boto3.Session()
    creds = session.get_credentials().get_frozen_credentials()
    region = os.environ.get("AWS_REGION", session.region_name or "us-east-1")
    for i in range(0, len(events), MAX_EVENTS_PER_PUBLISH):
        payload = []
        for e in events[i:i + MAX_EVENTS_PER_PUBLISH]:
            s = json.dumps(e, ensure_ascii=False, default=str)
            if len(s.encode()) > MAX_EVENT_BYTES:  # large payloads: tell the client to refetch
                s = json.dumps({"type": e.get("type"), "truncated": True, "ref": e.get("ref")})
            payload.append(s)
        body = json.dumps({"channel": chan, "events": payload}).encode()
        req = AWSRequest(method="POST", url=f"https://{domain}/event", data=body,
                         headers={"Content-Type": "application/json", "Host": domain})
        SigV4Auth(creds, "appsync", region).add_auth(req)
        try:
            with safeurl.urlopen(urllib.request.Request(req.url, data=body, headers=dict(req.headers),
                                                        method="POST"), timeout=10) as r:
                r.read()
        except Exception:  # noqa: BLE001 - realtime is best-effort; state is always refetchable
            log.exception("AppSync publish failed")
