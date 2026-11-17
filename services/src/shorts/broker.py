"""Session broker: runs one chat turn of a project against the orchestrator and relays events to the browser.

Orchestrator backends (ORCHESTRATOR env): "bma" (Bedrock Managed Agents with OpenAI, primary) or "strands"
(AgentCore Runtime, fallback). Both use the same tools API.

  PROJ#{p}  MSG#{ts}#{id}   chat transcript (role, text, tools, questionId)
  PROJ#{p}  META.agent      {backend, sessionId, environmentId, busyUntil}
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path

from . import bindings, ddb, events, ingest, projects
from .auth import Principal, load_principal

log = logging.getLogger(__name__)

PROMPT = (Path(__file__).parent / "prompts" / "orchestrator.md").read_text()
TURN_TIMEOUT_SEC = 840
FLUSH_SEC = 0.35


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise RuntimeError(f"{name} not set")
    return v


# ------------------------------------------------------------------ transcript
def save_message(project_id: str, role: str, text: str, **extra) -> dict:
    ts = ddb.now_ms()
    item = {"PK": f"PROJ#{project_id}", "SK": f"MSG#{ts:015d}#{uuid.uuid4().hex[:6]}", "role": role,
            "text": text[:20000], "createdAt": ts, **{k: v for k, v in extra.items() if v is not None}}
    ddb.put(item)
    return item


def messages(project_id: str, limit: int = 200) -> list[dict]:
    items = ddb.query(f"PROJ#{project_id}", "MSG#", forward=False, limit=limit)
    return [{k: i.get(k) for k in ("role", "text", "createdAt", "tools", "questionId", "selection", "error")}
            for i in reversed(items)]


def compose_input(text: str, selection: dict | None, answer: dict | None) -> str:
    parts = []
    if answer:
        parts.append(f"[Answer to question {answer.get('questionId')}]: {json.dumps(answer.get('value'), ensure_ascii=False)}")
    if selection:
        parts.append("[The user has selected in the editor]: " + json.dumps(selection, ensure_ascii=False))
    parts.append(text)
    return "\n".join(parts)


# ------------------------------------------------------------------ busy flag
def try_start(project_id: str, holder: str) -> bool:
    now = ddb.now_ms()
    try:
        ddb.update_raw(f"PROJ#{project_id}", "META", "SET agentBusy = :b",
                       values={":b": {"holder": holder, "until": now + TURN_TIMEOUT_SEC * 1000}, ":now": now},
                       condition="attribute_not_exists(agentBusy) OR agentBusy.#u < :now",
                       names={"#u": "until"})
        return True
    except Exception as exc:
        if ddb.conditional_failed(exc):
            return False
        raise


def finish(project_id: str) -> None:
    ddb.update_raw(f"PROJ#{project_id}", "META", "REMOVE agentBusy")


# ------------------------------------------------------------------ BMA backend
def _openai_client():
    from aws_bedrock_token_generator import provide_token
    from openai import OpenAI
    region = _env("AWS_REGION", "us-east-1")
    return OpenAI(api_key=lambda: provide_token(region=region),
                  base_url=f"https://bedrock-mantle.{region}.api.aws/openai/v1")


def _mcp_tool() -> dict:
    return {"type": "mcp", "server_label": "shorts",
            "transport": {"type": "stdio", "command": "/opt/bma/.venv/bin/python",
                          "args": ["/opt/bma/plugins/shorts/mcp/server.py"], "cwd": "/opt/bma",
                          "env": {}, "env_vars": ["SHORTS_TOOLS_URL", "AWS_REGION"]}}


def _instructions(p: Principal) -> str:
    tenant = ddb.get(f"TENANT#{p.tenant_id}", "META") or {}
    lang = ingest.LANGUAGES.get(tenant.get("language", ingest.DEFAULT_LANGUAGE), "Korean")
    return PROMPT + f"\n# Workspace\n- Workspace language: {lang}\n"


class Relay:
    """Batches text deltas and forwards structured events to the browser channel."""

    def __init__(self, project_id: str, sub: str, turn: str):
        self.chan = events.channel(project_id, sub)
        self.turn = turn
        self.buf = ""
        self.text = ""
        self.last = time.time()
        self.tools: list[dict] = []

    def delta(self, s: str) -> None:
        self.buf += s
        self.text += s
        if time.time() - self.last > FLUSH_SEC:
            self.flush()

    def flush(self) -> None:
        if self.buf:
            events.publish(self.chan, [{"type": "assistant.delta", "turn": self.turn, "text": self.buf}])
            self.buf = ""
        self.last = time.time()

    def emit(self, e: dict) -> None:
        self.flush()
        events.publish(self.chan, [{"turn": self.turn, **e}])


def run_bma_turn(p: Principal, project: dict, user_input: str, relay: Relay) -> None:
    client = _openai_client()
    sessions = client.beta.agents.sessions
    agent_state = project.get("agent") or {}
    sid = agent_state.get("sessionId") if agent_state.get("backend") == "bma" else None
    if sid:
        try:
            s = sessions.retrieve(sid).model_dump(mode="json", warnings=False)
            if s.get("status") == "failed":
                sid = None
        except Exception:  # noqa: BLE001 - expired/deleted session → start a new one
            sid = None
    if sid:
        stream = sessions.events.stream(sid, extra_query={"stream": "true"})
        sessions.events.create(sid, events=[{"type": "agent.session.input.message", "input": [
            {"role": "user", "content": [{"type": "input_text", "text": user_input}]}]}])
        bindings.refresh("bma", agent_state["environmentId"])
    else:
        prior = "" if not project.get("currentVersion") else (
            "\n[Context: this project already has a timeline (version "
            f"{project['currentVersion']}); call get_context before changing it.]")
        stream = sessions.create(
            agent={"model": _env("BMA_MODEL_ID", "openai.gpt-5.6-luna"), "instructions": _instructions(p),
                   "tools": [_mcp_tool()]},
            environment={"type": "aws_bedrock_agentcore", "runtime_arn": _env("BMA_RUNTIME_ARN"),
                         "runtime_qualifier": "DEFAULT", "workspace_directory": "/home/app/workspace",
                         "capability_directories": ["/opt/bma/plugins"]},
            input=user_input + prior, stream=True, extra_body={"role_arn": _env("BMA_SESSION_ROLE_ARN")})
    with stream:
        for ev in stream:
            d = ev.model_dump(mode="json", warnings=False)
            kind = d.get("type", "")
            if kind == "agent.session.created" or (not sid and d.get("session")):
                s = d.get("session") or {}
                if s.get("id") and not sid:
                    sid = s["id"]
                    env_id = (s.get("environment") or {}).get("id")
                    if not env_id:
                        env_id = sessions.retrieve(sid).model_dump(mode="json")["environment"]["id"]
                    bindings.bind("bma", env_id, p, project["projectId"], sid)
                    ddb.update(f"PROJ#{project['projectId']}", "META", {"agent": {
                        "backend": "bma", "sessionId": sid, "environmentId": env_id}})
            if kind.endswith("output_text.delta"):
                relay.delta(d.get("delta", ""))
            elif kind.endswith("turn.item.done") or kind.endswith("turn.item.added"):
                item = d.get("item") or {}
                if item.get("type") == "mcp_call":
                    entry = {"type": "tool", "name": item.get("name"), "status": item.get("status"),
                             "error": (item.get("error") or None)}
                    if kind.endswith("done"):
                        relay.tools.append({"name": item.get("name"), "status": item.get("status")})
                    relay.emit(entry)
            elif kind.split(".")[-1] in {"completed", "failed", "cancelled"} and ".turn." in kind:
                turn = d.get("turn") or {}
                if turn.get("error"):
                    raise RuntimeError(str(turn["error"])[:500])
                break


# ------------------------------------------------------------------ entry
def run_turn(event: dict) -> None:
    project_id, sub = event["projectId"], event["sub"]
    p = load_principal(sub)
    project, _ = projects.require_project(p, project_id, "edit")
    turn = event.get("turn") or uuid.uuid4().hex[:10]
    relay = Relay(project_id, sub, turn)
    relay.emit({"type": "turn.started"})
    error = None
    try:
        backend = os.environ.get("ORCHESTRATOR", "bma")
        user_input = compose_input(event.get("text", ""), event.get("selection"), event.get("answer"))
        if backend == "strands":
            from . import strands_backend
            strands_backend.run_turn(p, project, user_input, relay)
        else:
            run_bma_turn(p, project, user_input, relay)
    except Exception as exc:  # noqa: BLE001
        log.exception("Turn failed")
        error = str(exc)[:500]
    finally:
        relay.flush()
        save_message(project_id, "assistant", relay.text, tools=relay.tools or None, error=error, turn=turn)
        agent = (ddb.get(f"PROJ#{project_id}", "META") or {}).get("agent") or {}
        if agent.get("environmentId"):
            projects.release_lock(project_id, f"agent:{agent['environmentId']}")
        finish(project_id)
        relay.emit({"type": "turn.failed", "error": error} if error else {"type": "turn.completed"})
