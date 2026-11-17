"""Strands Agents fallback orchestrator (ORCHESTRATOR=strands).

Runs the agent loop in the broker with Bedrock Converse (model: STRANDS_MODEL_ID). Uses the *same* 13 tools as the
BMA path, executed in-process as the bound user (ACL enforced per call). Conversation history persists in S3
(Strands S3SessionManager, one session per project). Skills are the same SKILL.md files, exposed via
`list_skills` / `read_skill`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from . import agent_tools
from .auth import Principal
from .config import get_config

log = logging.getLogger(__name__)
SKILLS_DIR = Path(__file__).parent / "skills"


def _skills() -> dict[str, tuple[str, str]]:
    out = {}
    for f in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = f.read_text()
        desc = next((ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("description:")), "")
        out[f.parent.name] = (desc, text.split("---", 2)[-1].strip())
    return out


def _tools(ctx: agent_tools.ToolContext, relay) -> list:
    from strands.tools.tools import PythonAgentTool

    def make(name: str, spec: dict):
        def fn(tool_use, **_kw):
            relay.emit({"type": "tool", "name": name, "status": "in_progress"})
            try:
                res = agent_tools.run(ctx, name, tool_use.get("input") or {})
                data = res.data
                if res.images:
                    # Converse does not accept images inside tool results for this model family: describe them.
                    from . import models
                    data = {"result": data, "frames_description": models.describe_frames(
                        res.images, json.dumps(data, ensure_ascii=False, default=str)[:500])}
                content = [{"text": json.dumps(data, ensure_ascii=False, default=str)}]
                relay.tools.append({"name": name, "status": "completed"})
                relay.emit({"type": "tool", "name": name, "status": "completed"})
                return {"toolUseId": tool_use["toolUseId"], "status": "success", "content": content}
            except Exception as exc:  # noqa: BLE001 - errors go back to the model
                relay.tools.append({"name": name, "status": "failed"})
                relay.emit({"type": "tool", "name": name, "status": "failed", "error": str(exc)[:300]})
                return {"toolUseId": tool_use["toolUseId"], "status": "error", "content": [{"text": f"Error: {exc}"}]}
        return PythonAgentTool(name, {"name": name, "description": spec["description"],
                                      "inputSchema": {"json": spec["inputSchema"]}}, fn)

    tools = [make(n, s) for n, s in agent_tools.TOOLS.items()]
    skills = _skills()

    def list_skills(tool_use, **_kw):
        return {"toolUseId": tool_use["toolUseId"], "status": "success",
                "content": [{"text": json.dumps({k: v[0] for k, v in skills.items()}, ensure_ascii=False)}]}

    def read_skill(tool_use, **_kw):
        name = (tool_use.get("input") or {}).get("name", "")
        body = skills.get(name, ("", f"Unknown skill {name}; call list_skills"))[1]
        return {"toolUseId": tool_use["toolUseId"], "status": "success", "content": [{"text": body}]}

    tools.append(PythonAgentTool("list_skills", {"name": "list_skills", "description": "List editing skills (genre/format playbooks).",
                                                 "inputSchema": {"json": {"type": "object", "properties": {}}}}, list_skills))
    tools.append(PythonAgentTool("read_skill", {"name": "read_skill", "description": "Read one editing skill playbook before applying it.",
                                                "inputSchema": {"json": {"type": "object", "properties": {"name": {"type": "string"}},
                                                                          "required": ["name"]}}}, read_skill))
    return tools


def run_turn(p: Principal, project: dict, user_input: str, relay) -> None:
    from strands import Agent
    from strands.models import BedrockModel
    from strands.session import S3SessionManager

    from .broker import _instructions

    ctx = agent_tools.ToolContext(principal=p, project_id=project["projectId"], holder=f"agent:strands:{project['projectId']}")
    model = BedrockModel(model_id=os.environ.get("STRANDS_MODEL_ID", "us.openai.gpt-6.1-sol"),
                         region_name=os.environ.get("AWS_REGION", "us-east-1"), max_tokens=8192)
    session = S3SessionManager(session_id=f"{project['projectId']}", bucket=get_config().media_bucket,
                               prefix=f"agent-sessions/{project['tenantId']}/")
    agent = Agent(model=model, tools=_tools(ctx, relay), session_manager=session, callback_handler=None,
                  system_prompt=_instructions(p) + "\n# Skills\nCall list_skills, then read_skill for the one that fits.\n")

    async def go():
        async for ev in agent.stream_async(user_input):
            if isinstance(ev, dict) and ev.get("data"):
                relay.delta(ev["data"])
    asyncio.run(go())
    from . import projects
    projects.release_lock(project["projectId"], ctx.holder)
