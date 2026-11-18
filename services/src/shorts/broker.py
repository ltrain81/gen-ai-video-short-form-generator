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

from . import bindings, ddb, events, ingest, projects, steps
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
    return [{k: i.get(k) for k in ("role", "text", "createdAt", "tools", "steps", "model", "effort", "questionId",
                                   "selection", "error")}
            for i in reversed(items)]


def compose_input(text: str, selection: dict | None, answer: dict | None) -> str:
    parts = []
    if answer:
        qid = str(answer.get('questionId') or '')
        label = "UI" if qid.startswith("u") else "Answer to question"
        verb = " submitted" if qid.startswith("u") else ""
        parts.append(f"[{label} {qid}{verb}]: {json.dumps(answer.get('value'), ensure_ascii=False)}")
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
        self.steps: list[dict] = []  # ordered: {kind: tool|thought, ...} — persisted for transparency
        self._started: dict[str, float] = {}
        self.model: str | None = None
        self.session_id: str | None = None
        self.effort: str | None = None

    def thought(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        step = {"kind": "thought", "text": text[:1500]}
        self.steps.append(step)
        self.emit({"type": "thought", "text": step["text"]})

    def tool_started(self, key: str, name: str, args) -> None:
        self._started[key] = time.time()
        self.emit({"type": "tool", "name": name, "status": "in_progress", "summary": steps.summarize(name, args)})

    def tool_done(self, key: str, name: str, args, output, status: str, error=None) -> None:
        ms = int((time.time() - self._started.pop(key, time.time())) * 1000)
        step = {"kind": "tool", "name": name, "status": status, "summary": steps.summarize(name, args, output),
                "detail": steps.detail(args), "ms": ms, **({"error": str(error)[:300]} if error else {})}
        self.steps.append(step)
        self.tools.append({"name": name, "status": status})
        self.emit({"type": "tool", **{k: v for k, v in step.items() if k != "kind"}})

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


def agent_settings(project: dict) -> tuple[str, str]:
    model = project.get("agentModel") or _env("BMA_MODEL_ID", "openai.gpt-6.1-sol")
    effort = project.get("agentEffort") or os.environ.get("BMA_REASONING_EFFORT", "high")
    return model, effort


RECENT_VERBATIM = 8


def recap(project_id: str, limit: int = RECENT_VERBATIM) -> str:
    """Context for a fresh session (old sessions expire on the service side): a rolling memory of the whole
    conversation (decisions, preferences, rejected ideas), the latest messages verbatim and the edit history."""
    msgs = messages(project_id, limit=400)[:-1]  # the current user message is sent separately
    if not msgs:
        return ""
    older, recent = msgs[:-limit], msgs[-limit:]
    project = ddb.get(f"PROJ#{project_id}", "META") or {}
    mem = project.get("memory") or {}
    upto = int(mem.get("upTo") or 0)
    new_older = [m for m in older if int(m.get("createdAt") or 0) > upto]
    text = mem.get("text") or ""
    if new_older:
        try:
            from . import models
            lang = ingest.LANGUAGES.get((ddb.get(f"TENANT#{project.get('tenantId')}", "META") or {}).get("language", ingest.DEFAULT_LANGUAGE), "Korean")
            convo = "\n".join(f"{m['role']}: {(m.get('text') or '').strip()[:1500]}" for m in new_older if (m.get("text") or "").strip())
            text = models.update_memory(text, convo, lang)
            ddb.update(f"PROJ#{project_id}", "META", {"memory": {"text": text, "upTo": int(new_older[-1]["createdAt"])}})
        except Exception:  # noqa: BLE001 - memory is a bonus; the verbatim tail still goes in
            log.warning("Updating project memory failed", exc_info=True)
    versions_ = [v for v in projects.versions(project_id)[:15] if v.get("message")]
    parts = ["\n[Your previous session for this project ended. Continue seamlessly from here."]
    if text:
        parts.append("Project memory (summary of the whole conversation so far):\n" + text)
    if versions_:
        parts.append("Recent edit history (newest first): " + "; ".join(
            f"v{v['version']} {v.get('author')}: {v['message'][:80]}" for v in versions_))
    parts.append("Latest messages:\n" + "\n".join(f"{m['role']}: {(m.get('text') or '').strip()[:600]}"
                                                    for m in recent if (m.get("text") or "").strip()))
    return "\n".join(parts) + "\n]"


SESSION_MAX_AGE_SEC = 7 * 3600  # AgentCore compute lifetime is 8 h; start fresh before it expires


def _retire_session(sessions, sid: str | None) -> None:
    """Delete a session we no longer use (docs: delete sessions the application no longer needs)."""
    if not sid:
        return
    try:
        sessions.delete(sid)
    except Exception:  # noqa: BLE001 - best effort cleanup
        log.info("Could not delete BMA session %s", sid, exc_info=True)


def diagnose_session(sid: str | None) -> None:
    """Log the service-side error details of a failed turn (session.error + the latest items)."""
    if not sid:
        return
    try:
        sessions = _openai_client().beta.agents.sessions
        s = sessions.retrieve(sid).model_dump(mode="json", warnings=False)
        items = sessions.items.list(sid, limit=10, order="desc").model_dump(mode="json", warnings=False).get("data", [])
        log.warning("BMA session %s status=%s error=%s last_items=%s", sid, s.get("status"),
                    json.dumps(s.get("error"), default=str)[:1500],
                    json.dumps([{k: it.get(k) for k in ("type", "status", "name", "error", "turn_id")} for it in items],
                               default=str)[:2500])
    except Exception:  # noqa: BLE001
        log.info("Could not diagnose BMA session %s", sid, exc_info=True)


def run_bma_turn(p: Principal, project: dict, user_input: str, relay: Relay, fresh: bool = False) -> None:
    client = _openai_client()
    sessions = client.beta.agents.sessions
    agent_state = project.get("agent") or {}
    model, effort = agent_settings(project)
    relay.model, relay.effort = model, effort
    runtime_version = os.environ.get("BMA_RUNTIME_VERSION", "")
    sid = agent_state.get("sessionId") if agent_state.get("backend") == "bma" and not fresh else None
    stale = None
    if sid:
        if (agent_state.get("model"), agent_state.get("effort")) != (model, effort):
            stale = "settings changed"
        elif runtime_version and agent_state.get("runtimeVersion") != runtime_version:
            stale = "runtime image updated"  # a running session keeps its old processes after a Runtime update
        elif time.time() - float(agent_state.get("createdAt") or 0) > SESSION_MAX_AGE_SEC:
            stale = "near the 8 h compute lifetime"
    if stale:
        log.info("Replacing BMA session %s: %s", sid, stale)
        _retire_session(sessions, sid)
        sid = None
    stream = None
    if sid:
        try:
            s = sessions.retrieve(sid).model_dump(mode="json", warnings=False)
            if s.get("status") == "failed":
                raise RuntimeError(f"session failed: {json.dumps(s.get('error'), default=str)[:300]}")
            if s.get("status") == "in_progress":
                raise RuntimeError("session still busy with a previous turn")
            stream = sessions.events.stream(sid, extra_query={"stream": "true"})
            sessions.events.create(sid, events=[{"type": "agent.session.input.message", "input": [
                {"role": "user", "content": [{"type": "input_text", "text": user_input}]}]}])
            bindings.refresh("bma", agent_state["environmentId"])
        except Exception:  # noqa: BLE001 - expired/broken session (the service may answer 500) → start fresh
            log.warning("Resuming BMA session %s failed; starting a new one", sid, exc_info=True)
            if stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
            _retire_session(sessions, sid)
            stream, sid = None, None
    if not sid:
        prior = "" if not project.get("currentVersion") else (
            "\n[Context: this project already has a timeline (version "
            f"{project['currentVersion']}); call get_context before changing it.]")
        agent_cfg = {"model": model, "instructions": _instructions(p), "tools": [_mcp_tool()],
                     "reasoning": {"effort": effort, "summary": "auto"}}
        kwargs = dict(environment={"type": "aws_bedrock_agentcore", "runtime_arn": _env("BMA_RUNTIME_ARN"),
                                   "runtime_qualifier": "DEFAULT", "workspace_directory": "/home/app/workspace",
                                   "capability_directories": ["/opt/bma/plugins"]},
                      input=user_input + prior + recap(project["projectId"]), stream=True,
                      extra_body={"role_arn": _env("BMA_SESSION_ROLE_ARN")})
        try:
            stream = sessions.create(agent=agent_cfg, **kwargs)
        except Exception as exc:  # noqa: BLE001
            # `reasoning` is outside the documented preview surface: if it is ever rejected, run without it.
            if "reasoning" not in str(exc):
                raise
            log.warning("reasoning config rejected; creating the session without it")
            agent_cfg.pop("reasoning")
            relay.effort = None
            stream = sessions.create(agent=agent_cfg, **kwargs)
        relay.session_id = None
    thinking: dict[str, str] = {}
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
                        "backend": "bma", "sessionId": sid, "environmentId": env_id, "model": model, "effort": effort,
                        "runtimeVersion": runtime_version, "createdAt": int(time.time())}})
            relay.session_id = sid
            if kind.startswith("agent.session.environment.") and kind.split(".")[-1] in {"expired", "failed", "disconnected"}:
                raise RuntimeError(f"execution environment {kind.split('.')[-1]}")
            if kind.endswith("output_text.delta"):
                relay.delta(d.get("delta", ""))
            elif kind.endswith("reasoning_summary_text.delta"):
                key = str(d.get("item_id") or "r")
                thinking[key] = thinking.get(key, "") + str(d.get("delta") or "")
            elif kind.endswith("reasoning_summary_text.done"):
                key = str(d.get("item_id") or "r")
                relay.thought(d.get("text") or thinking.pop(key, ""))
                thinking.pop(key, None)
            elif kind.endswith("turn.item.done") or kind.endswith("turn.item.added"):
                item = d.get("item") or {}
                if item.get("type") == "mcp_call":
                    key = str(item.get("id") or item.get("name"))
                    if kind.endswith("added"):
                        relay.tool_started(key, item.get("name"), item.get("arguments"))
                    else:
                        relay.tool_done(key, item.get("name"), item.get("arguments"), item.get("output"),
                                        item.get("status") or "completed", item.get("error"))
                elif item.get("type") == "reasoning" and kind.endswith("done"):
                    texts = [x.get("text", "") for x in (item.get("summary") or []) if isinstance(x, dict)]
                    if texts and not any(x.get("kind") == "thought" and x.get("text") == "\n".join(texts).strip()
                                         for x in relay.steps[-3:]):
                        relay.thought("\n".join(texts))
            elif kind.split(".")[-1] in {"completed", "failed", "cancelled"} and ".turn." in kind:
                turn = d.get("turn") or {}
                if turn.get("error") or kind.endswith("failed"):
                    raise RuntimeError(str(turn.get("error") or kind)[:500])
                break


def _ensure_brand(p: Principal, project_id: str) -> None:
    """Brand kits are applied deterministically after each turn (not left to the model)."""
    project = ddb.get(f"PROJ#{project_id}", "META") or {}
    if not project.get("brandKitId"):
        return
    tl = projects.load_timeline(project)
    has = any((i.get("provenance") or {}).get("reason") == "brand" for t in tl["tracks"] for i in t["items"])
    if has and tl["settings"].get("brandKitId") == project["brandKitId"]:
        return
    if not any(t["items"] for t in tl["tracks"] if t["kind"] == "video"):
        return
    from . import agent_tools
    holder = f"agent:{((project.get('agent') or {}).get('environmentId')) or 'broker'}"
    try:
        agent_tools.apply_brand_kit(agent_tools.ToolContext(principal=p, project_id=project_id, holder=holder), {})
    except Exception:  # noqa: BLE001 - never fail the turn because of branding
        log.exception("Brand kit application failed")


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
        # Per-project override (UI/advanced) falls back to the deployment default.
        backend = (project.get("orchestrator") or os.environ.get("ORCHESTRATOR", "bma")).lower()
        user_input = compose_input(event.get("text", ""), event.get("selection"), event.get("answer"))
        if backend == "strands":
            from . import strands_backend
            strands_backend.run_turn(p, project, user_input, relay)
        else:
            try:
                run_bma_turn(p, project, user_input, relay)
            except Exception as first:  # noqa: BLE001
                # A broken service-side session fails every turn ("internal_error"): record why, drop it and retry
                # once in a fresh session that gets a recap of the chat (the timeline itself is persistent).
                log.warning("BMA turn failed (%s); retrying in a fresh session", str(first)[:200])
                diagnose_session(relay.session_id)
                _retire_session(_openai_client().beta.agents.sessions, relay.session_id)
                old_env = ((ddb.get(f"PROJ#{project_id}", "META") or {}).get("agent") or {}).get("environmentId")
                if old_env:
                    projects.release_lock(project_id, f"agent:{old_env}")
                ddb.update(f"PROJ#{project_id}", "META", {"agent": {"backend": "bma"}})
                relay.thought("이전 세션에 문제가 있어 새 세션으로 이어서 진행합니다 / Continuing in a fresh session")
                fresh_project = ddb.get(f"PROJ#{project_id}", "META", consistent=True) or project
                run_bma_turn(p, fresh_project, user_input, relay, fresh=True)
    except Exception as exc:  # noqa: BLE001
        log.exception("Turn failed")
        error = str(exc)[:500]
        # Never resume a session whose turn failed.
        agent_now = (ddb.get(f"PROJ#{project_id}", "META") or {}).get("agent") or {}
        if agent_now.get("backend") == "bma" and agent_now.get("sessionId"):
            ddb.update(f"PROJ#{project_id}", "META", {"agent": {"backend": "bma", "environmentId": agent_now.get("environmentId")}})
        _ensure_brand(p, project_id)
    finally:
        relay.flush()
        save_message(project_id, "assistant", relay.text, tools=relay.tools or None, steps=relay.steps or None,
                     model=relay.model, effort=relay.effort, error=error, turn=turn)
        if relay.text.strip():
            ddb.update(f"PROJ#{project_id}", "META", {"lastMessage": relay.text.strip()[:200], "lastMessageAt": ddb.now_ms()})
        meta = ddb.get(f"PROJ#{project_id}", "META") or {}
        agent = meta.get("agent") or {}
        if agent.get("environmentId"):
            holder = f"agent:{agent['environmentId']}"
            projects.release_lock(project_id, holder)
            for cid in meta.get("childIds") or []:  # series: the orchestrator edited its shorts too
                projects.release_lock(cid, holder)
        finish(project_id)
        relay.emit({"type": "turn.failed", "error": error} if error else {"type": "turn.completed"})
