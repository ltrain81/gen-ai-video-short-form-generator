"""Agent tools API (API Gateway HTTP API, AWS_IAM authorizer).

Accepted callers: only the agent runtime execution role(s) (`AGENT_ROLE_NAMES`), verified by SigV4 at the gateway.
The request names its binding (BMA environment id). The binding resolves to the user the agent acts for; every
tool then enforces that user's ACL.

  GET  /agent/tools                       → MCP tool list
  POST /agent/tools/call  {binding:{kind,id}, name, arguments} → MCP tools/call result
"""
import base64
import json
import logging
import os

from shorts import agent_tools, bindings
from shorts.auth import AuthError, Forbidden

log = logging.getLogger()
log.setLevel(logging.INFO)
ROLE_NAMES = {r for r in os.environ.get("AGENT_ROLE_NAMES", "").split(",") if r}


def _caller_role(event: dict) -> str:
    arn = (((event.get("requestContext") or {}).get("authorizer") or {}).get("iam") or {}).get("userArn", "")
    # arn:aws:sts::<acct>:assumed-role/<role-name>/<session>
    parts = arn.split(":assumed-role/", 1)
    return parts[1].split("/", 1)[0] if len(parts) == 2 else ""


def _resp(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


def handler(event, _context):
    if _caller_role(event) not in ROLE_NAMES:
        return _resp(403, {"message": "Caller is not an agent runtime"})
    method = event["requestContext"]["http"]["method"]
    if method == "GET":
        return _resp(200, {"tools": list(agent_tools.TOOLS.values())})
    try:
        body = json.loads(event.get("body") or "{}")
        b = body.get("binding") or {}
        principal, binding = bindings.resolve(str(b.get("kind", "")), str(b.get("id", "")))
        ctx = agent_tools.ToolContext(principal=principal, project_id=binding["projectId"],
                                      holder=f"agent:{b.get('id')}")
        result = agent_tools.run(ctx, body["name"], body.get("arguments") or {})
        content = [{"type": "text", "text": json.dumps(result.data, ensure_ascii=False, default=str)}]
        for img in result.images or []:
            content.append({"type": "image", "data": base64.b64encode(img).decode(), "mimeType": "image/jpeg"})
        return _resp(200, {"content": content, "isError": False})
    except (AuthError, Forbidden) as exc:
        return _resp(403, {"message": str(exc)})
    except Exception as exc:  # noqa: BLE001 - tool errors go back to the model as tool errors
        status = getattr(exc, "status", None)
        if not isinstance(status, int) and not isinstance(exc, (ValueError, KeyError)):
            log.exception("Tool failure")
        return _resp(200, {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True})
