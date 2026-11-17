"""Interactive chat widgets (`show_ui`): the agent describes a small UI declaratively; the web app renders it with
its own components (no agent-authored HTML/JS), the user's input comes back as the next user message.

  PROJ#{p} / UI#{id}   {spec, createdAt, answered?, answer?}
"""
from __future__ import annotations

import os
import base64
from typing import Any

from . import acl, ddb

MAX_BLOCKS = 12
MAX_ITEMS = 24
BLOCK_TYPES = {
    # display
    "markdown", "kv", "table", "media", "timeline",
    # input
    "choice", "clips", "compare", "slider", "range", "text_input", "select", "toggle", "color", "order",
    "frame_point", "assets", "caption_style", "text_list", "rating",
}
INPUT_TYPES = BLOCK_TYPES - {"markdown", "kv", "table", "media", "timeline"}


class WidgetError(ValueError):
    pass


def _s(v: Any, n: int = 400) -> str:
    return str(v if v is not None else "")[:n]


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _asset_ids(block: dict) -> list[str]:
    ids: list[str] = []
    for key in ("assetId",):
        if block.get(key):
            ids.append(str(block[key]))
    for coll in ("items", "clips", "options", "segments"):
        for it in block.get(coll) or []:
            if isinstance(it, dict) and it.get("assetId"):
                ids.append(str(it["assetId"]))
    return ids


def validate(spec: dict, principal, default_asset: str | None = None) -> dict:
    """Normalize the spec, cap sizes, and make sure every referenced asset is visible to this user."""
    if not isinstance(spec, dict):
        raise WidgetError("spec must be an object")
    blocks = spec.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise WidgetError("blocks must be a non-empty array")
    if len(blocks) > MAX_BLOCKS:
        raise WidgetError(f"At most {MAX_BLOCKS} blocks")
    out_blocks, seen = [], set()
    accessible = None
    for i, b in enumerate(blocks):
        if not isinstance(b, dict) or b.get("type") not in BLOCK_TYPES:
            raise WidgetError(f"blocks[{i}].type must be one of {sorted(BLOCK_TYPES)}")
        bid = _s(b.get("id") or f"b{i}", 40)
        if bid in seen:
            raise WidgetError(f"Duplicate block id {bid}")
        seen.add(bid)
        nb = {k: v for k, v in b.items() if k not in {"id"}}
        nb["id"] = bid
        for coll in ("items", "clips", "options", "rows"):
            if isinstance(nb.get(coll), list):
                nb[coll] = nb[coll][:MAX_ITEMS if coll != "rows" else 50]
        # Media items need an assetId; with a single-asset project the agent often omits it — fill it in.
        for coll in ("items", "clips", "options", "segments"):
            for it in nb.get(coll) or []:
                if isinstance(it, dict) and "startSec" in it and not it.get("assetId"):
                    if not default_asset:
                        raise WidgetError(f"blocks[{i}].{coll}[] items with startSec need an assetId")
                    it["assetId"] = default_asset
        if nb["type"] in {"range", "frame_point", "media"} and not nb.get("assetId") and default_asset:
            nb["assetId"] = default_asset
        for k in ("min", "max", "step", "default", "startSec", "endSec", "atSec"):
            if isinstance(nb.get(k), str):
                try:
                    nb[k] = float(nb[k])
                except ValueError:
                    pass
        for coll in ("items", "clips", "options", "segments"):
            for it in nb.get(coll) or []:
                if isinstance(it, dict):
                    for k in ("startSec", "endSec", "score"):
                        if isinstance(it.get(k), str):
                            try:
                                it[k] = float(it[k])
                            except ValueError:
                                pass
        ids = _asset_ids(nb)
        if ids:
            accessible = accessible if accessible is not None else acl.accessible_asset_ids(principal)
            denied = [a for a in ids if a not in accessible]
            if denied:
                raise WidgetError(f"Unknown or inaccessible assets: {', '.join(denied[:5])}")
        if nb["type"] in {"choice", "select"} and not nb.get("options"):
            raise WidgetError(f"blocks[{i}] ({nb['type']}) needs options")
        if nb["type"] == "slider":
            lo, hi = _num(nb.get("min"), 0), _num(nb.get("max"), 100)
            if hi <= lo:
                raise WidgetError(f"blocks[{i}] slider needs max > min")
        if nb["type"] in {"range", "frame_point"} and not nb.get("assetId"):
            raise WidgetError(f"blocks[{i}] ({nb['type']}) needs assetId")
        out_blocks.append(nb)
    interactive = any(b["type"] in INPUT_TYPES for b in out_blocks)
    return {"title": _s(spec.get("title"), 200), "description": _s(spec.get("description"), 1000),
            "blocks": out_blocks, "submitLabel": _s(spec.get("submitLabel"), 60) or None,
            "allowSkip": bool(spec.get("allowSkip", False)), "interactive": interactive}


def create(project_id: str, spec: dict) -> dict:
    uid = "u" + base64.urlsafe_b64encode(os.urandom(6)).decode().rstrip("=")
    item = {"PK": f"PROJ#{project_id}", "SK": f"UI#{uid}", "uiId": uid, "spec": spec, "createdAt": ddb.now_ms(),
            "ttl": ddb.now_ms() // 1000 + 30 * 86400}
    ddb.put(item)
    return item


def answer(project_id: str, ui_id: str, value: Any) -> None:
    """Record the user's input so the widget renders as answered after a reload. Unknown ids are ignored."""
    if not str(ui_id).startswith(("u", "q")):
        return
    sk = f"UI#{ui_id}" if str(ui_id).startswith("u") else f"QUESTION#{ui_id}"
    if ddb.get(f"PROJ#{project_id}", sk):
        ddb.update(f"PROJ#{project_id}", sk, {"answered": True, "answer": value, "answeredAt": ddb.now_ms()})


def history(project_id: str, limit: int = 100) -> list[dict]:
    """Widgets + legacy ask_user questions, oldest first, for re-rendering the chat after a reload."""
    out = []
    for it in ddb.query(f"PROJ#{project_id}", "UI#", forward=False, limit=limit):
        out.append({"uiId": it["uiId"], "spec": it["spec"], "createdAt": it["createdAt"],
                    "answered": bool(it.get("answered")), "answer": it.get("answer")})
    for it in ddb.query(f"PROJ#{project_id}", "QUESTION#", forward=False, limit=limit):
        out.append({"uiId": it["questionId"], "question": {k: it.get(k) for k in
                    ("questionId", "question", "kind", "options", "clips", "multiple", "estimate")},
                    "createdAt": it["createdAt"], "answered": bool(it.get("answered")), "answer": it.get("answer")})
    return sorted(out, key=lambda x: x["createdAt"])
