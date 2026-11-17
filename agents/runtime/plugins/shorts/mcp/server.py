"""stdio MCP server inside the AgentCore Runtime microVM — a thin, authenticated proxy to the Shorts tools API.

Identity (see services/src/shorts/bindings.py): requests are SigV4-signed with the Runtime execution role and carry
this session's BMA environment id, read from the lifecycle state file. No secrets are passed in tool config.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

TOOLS_URL = os.environ["SHORTS_TOOLS_URL"].rstrip("/")
REGION = os.environ.get("AWS_REGION", "us-east-1")
STATE_FILES = [Path(os.environ.get("BMA_STATE_DIR", "")) / "state.json",
               Path.home() / ".bma" / "state.json", Path("/home/app/.bma/state.json"), Path("/mnt/home/.bma/state.json")]
_session = boto3.Session()


def environment_id() -> str:
    for p in STATE_FILES:
        try:
            return json.loads(p.read_text())["attachment"]["environment_id"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
    raise RuntimeError("BMA environment id not found")


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = AWSRequest(method=method, url=TOOLS_URL + path, data=data, headers={"Content-Type": "application/json"})
    SigV4Auth(_session.get_credentials().get_frozen_credentials(), "execute-api", REGION).add_auth(req)
    r = urllib.request.Request(req.url, data=data, headers=dict(req.headers), method=method)
    with urllib.request.urlopen(r, timeout=300) as resp:
        return json.loads(resp.read())


def reply(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    msg["error" if error else "result"] = error or result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    tools = None
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        if rid is None:
            continue  # notifications
        try:
            if method == "initialize":
                reply(rid, {"protocolVersion": params.get("protocolVersion", "2025-03-26"),
                            "capabilities": {"tools": {}}, "serverInfo": {"name": "shorts", "version": "2.0.0"}})
            elif method == "tools/list":
                tools = tools or call("GET", "/agent/tools")["tools"]
                reply(rid, {"tools": tools})
            elif method == "tools/call":
                out = call("POST", "/agent/tools/call", {"binding": {"kind": "bma", "id": environment_id()},
                                                        "name": params.get("name"),
                                                        "arguments": params.get("arguments") or {}})
                reply(rid, out)
            elif method == "ping":
                reply(rid, {})
            else:
                reply(rid, error={"code": -32601, "message": f"Unknown method {method}"})
        except Exception as exc:  # noqa: BLE001
            reply(rid, {"content": [{"type": "text", "text": f"Tool transport error: {exc}"}], "isError": True})


if __name__ == "__main__":
    main()
