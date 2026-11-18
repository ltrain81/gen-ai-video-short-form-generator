import pytest

from shorts import agent_tools as A


class P:
    sub = "u1"
    tenant_id = "t1"


def test_series_tools_target_only_own_children(monkeypatch):
    metas = {"PROJ#kid": {"projectId": "kid", "parentId": "par"}, "PROJ#other": {"projectId": "other", "parentId": "x"}}
    monkeypatch.setattr(A.ddb, "get", lambda pk, sk, consistent=False: metas.get(pk))
    monkeypatch.setattr(A.projects, "require_project", lambda p, pid, action: ({"projectId": pid}, "owner"))
    seen = {}
    monkeypatch.setitem(A.HANDLERS, "get_timeline", lambda ctx, args: seen.update(pid=ctx.project_id, parent=ctx.parent_id, args=args) or A.ToolResult({}))
    ctx = A.ToolContext(principal=P(), project_id="par", holder="agent:e")
    A.run(ctx, "get_timeline", {"projectId": "kid", "full": True})
    assert seen == {"pid": "kid", "parent": "par", "args": {"full": True}}
    with pytest.raises(ValueError):  # not a child of this series
        A.run(ctx, "get_timeline", {"projectId": "other"})
    with pytest.raises(ValueError):  # tool not series-capable
        A.run(ctx, "search_archive", {"projectId": "kid", "query": "x"})
    assert "projectId" in A.TOOLS["edit_timeline"]["inputSchema"]["properties"]
