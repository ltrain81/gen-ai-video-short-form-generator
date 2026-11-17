"""Agent session ↔ user binding (who the agent acts for).

BMA (verified in the identity spike):
  - the broker creates a BMA session and learns `session.environment.id` (BMA-issued, unguessable)
  - the tool server inside the Runtime microVM reads the same id from the lifecycle state file
  - tool calls reach the tools API signed with the Runtime execution role (SigV4, verified by API Gateway)
  The backend accepts a call only if the IAM caller is the agent runtime role AND the environment id maps
  to a live binding. No credentials are placed in agent instructions or tool config.

  BIND#{kind}#{id}  META  tenantId, sub, projectId, sessionId, expiresAt, ttl
"""
from __future__ import annotations

import time

from . import ddb
from .auth import Forbidden, Principal, load_principal

BINDING_TTL_MS = 9 * 3600 * 1000  # > Runtime max lifetime (8h)


def bind(kind: str, binding_id: str, p: Principal, project_id: str, session_id: str) -> None:
    now = ddb.now_ms()
    ddb.put({"PK": f"BIND#{kind}#{binding_id}", "SK": "META", "tenantId": p.tenant_id, "sub": p.sub,
             "projectId": project_id, "sessionId": session_id, "createdAt": now,
             "expiresAt": now + BINDING_TTL_MS, "ttl": (now + BINDING_TTL_MS) // 1000})


def refresh(kind: str, binding_id: str) -> None:
    now = ddb.now_ms()
    ddb.update(f"BIND#{kind}#{binding_id}", "META", {"expiresAt": now + BINDING_TTL_MS,
                                                    "ttl": (now + BINDING_TTL_MS) // 1000})


def resolve(kind: str, binding_id: str) -> tuple[Principal, dict]:
    b = None
    for attempt in range(4):  # the broker writes the binding right after session creation; tolerate the race
        b = ddb.get(f"BIND#{kind}#{binding_id}", "META", consistent=True) if binding_id else None
        if b or not binding_id:
            break
        time.sleep(0.5 * (attempt + 1))
    if not b or b.get("expiresAt", 0) < ddb.now_ms():
        raise Forbidden("Unknown or expired agent session binding")
    p = load_principal(b["sub"])  # re-checks tenant membership and current groups on every call
    if p.tenant_id != b["tenantId"]:
        raise Forbidden("Binding tenant mismatch")
    return p, b
