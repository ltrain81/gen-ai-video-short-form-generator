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

from . import agent_tools
from .auth import Principal
from .config import get_config

log = logging.getLogger(__name__)


def _tools(ctx: agent_tools.ToolContext, relay) -> list:
    from strands.tools.tools import PythonAgentTool

    def make(name: str, spec: dict):
        def fn(tool_use, **_kw):
            key = str(tool_use.get("toolUseId") or name)
            args = tool_use.get("input") or {}
            relay.tool_started(key, name, args)
            try:
                res = agent_tools.run(ctx, name, args)
                data = res.data
                if res.images:
                    # Converse does not accept images inside tool results for this model family: describe them.
                    from . import models
                    data = {"result": data, "frames_description": models.describe_frames(
                        res.images, json.dumps(data, ensure_ascii=False, default=str)[:500])}
                content = [{"text": json.dumps(data, ensure_ascii=False, default=str)}]
                relay.tool_done(key, name, args, data if not isinstance(data, (bytes, bytearray)) else None, "completed")
                return {"toolUseId": tool_use["toolUseId"], "status": "success", "content": content}
            except Exception as exc:  # noqa: BLE001 - errors go back to the model
                relay.tool_done(key, name, args, None, "failed", exc)
                return {"toolUseId": tool_use["toolUseId"], "status": "error", "content": [{"text": f"Error: {exc}"}]}
        return PythonAgentTool(name, {"name": name, "description": spec["description"],
                                      "inputSchema": {"json": spec["inputSchema"]}}, fn)

    return [make(n, s) for n, s in agent_tools.TOOLS.items()]


def run_turn(p: Principal, project: dict, user_input: str, relay) -> None:
    from strands import Agent
    from strands.models import BedrockModel
    from strands.session import S3SessionManager

    from .broker import _instructions

    ctx = agent_tools.ToolContext(principal=p, project_id=project["projectId"], holder=f"agent:strands:{project['projectId']}")
    relay.model = os.environ.get("STRANDS_MODEL_ID", "us.openai.gpt-6.1-sol")
    model = BedrockModel(model_id=relay.model,
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
