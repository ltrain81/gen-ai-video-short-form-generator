"""Human-readable step summaries for the chat's "what the AI did" panel (transparency).

Built from the tool name, its arguments and its result. Kept language-neutral (values, counts, times) so the web app
adds the localized verb. Never includes ids or binding details.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

AGENT_MODELS = {
    # id: (label, tier)
    "openai.gpt-6.1-sol": ("GPT-6.1 Sol", "best"),
    "openai.gpt-6.1-terra": ("GPT-6.1 Terra", "balanced"),
    "openai.gpt-6-luna": ("GPT-6 Luna", "fast"),
    "openai.gpt-5.6-sol": ("GPT-5.6 Sol", "best"),
    "openai.gpt-5.6-luna": ("GPT-5.6 Luna", "fast"),
}
EFFORTS = ("low", "medium", "high", "xhigh")


def _t(sec: Any) -> str:
    try:
        s = float(sec)
    except (TypeError, ValueError):
        return "?"
    return f"{int(s // 60)}:{int(s % 60):02d}"


def _load(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    if isinstance(v, list) and v and isinstance(v[0], dict) and "text" in v[0]:  # MCP content blocks
        return _load(v[0]["text"])
    if isinstance(v, dict) and isinstance(v.get("content"), list):
        return _load(v["content"])
    return v


def _q(s: Any, n: int = 60) -> str:
    s = str(s or "").strip().replace("\n", " ")
    return f"“{s[:n]}{'…' if len(s) > n else ''}”" if s else ""


def summarize(name: str, args: Any, output: Any = None) -> str:
    a = _load(args) if args is not None else {}
    a = a if isinstance(a, dict) else {}
    o = _load(output)
    try:
        if name == "search_archive":
            n = len(o.get("results", [])) if isinstance(o, dict) else (len(o) if isinstance(o, list) else None)
            q = _q(a.get("text") or a.get("query"))
            return f"{q}{' → ' + str(n) if n is not None else ''}"
        if name in {"get_scenes", "read_transcript"}:
            rng = f"{_t(a.get('startSec'))}–{_t(a.get('endSec'))}" if a.get("startSec") is not None else ""
            n = None
            if isinstance(o, dict):
                n = len(o.get("scenes") or o.get("segments") or o.get("words") or [])
            return " ".join(x for x in (rng, f"({n})" if n else "") if x)
        if name == "look":
            times = a.get("times") or ([a["atSec"]] if a.get("atSec") is not None else [])
            if not times and a.get("startSec") is not None:
                return f"{_t(a.get('startSec'))}–{_t(a.get('endSec'))}"
            shown = ", ".join(_t(x) for x in times[:4])
            return shown + (f" (+{len(times) - 4})" if len(times) > 4 else "")
        if name == "ask_video":
            return _q(a.get("question"), 80)
        if name == "snap":
            return f"{_t(a.get('startSec') or a.get('timeSec'))} → {a.get('to') or a.get('mode') or ''}".strip()
        if name == "analyze_framing":
            return f"{_t(a.get('startSec'))}–{_t(a.get('endSec'))}"
        if name == "edit_timeline":
            ops = a.get("ops") or []
            c = Counter(str(op.get("op")) for op in ops if isinstance(op, dict))
            parts = [f"{k}×{v}" if v > 1 else k for k, v in c.most_common(6)]
            msg = a.get("message")
            res = ""
            if isinstance(o, dict):
                failed = [r for r in o.get("results", []) if not r.get("ok")]
                if o.get("durationSec") is not None:
                    res = f" → {o['durationSec']}s"
                if failed:
                    res += f" ⚠ {len(failed)}"
            return f"{', '.join(parts)}{(' · ' + _q(msg, 50)) if msg else ''}{res}"
        if name == "show_ui":
            kinds = [b.get("type") for b in (a.get("blocks") or []) if isinstance(b, dict)]
            return f"{_q(a.get('title'), 40)} [{', '.join(kinds[:6])}]".strip()
        if name == "ask_user":
            return _q(a.get("question"), 60)
        if name in {"read_skill"}:
            return str(a.get("name") or "")
        if name in {"estimate_generation", "generate_media"}:
            return f"{a.get('kind', '')} {_q(a.get('prompt') or a.get('text'), 40)}".strip()
        if name == "preview_frames":
            ts_ = a.get("times") or []
            return ", ".join(_t(x) for x in ts_[:6]) + (f" (+{len(ts_) - 6})" if len(ts_) > 6 else "") if ts_ else "auto"
        if name == "analyze_audio" and isinstance(o, dict):
            sil = len(o.get("silences") or [])
            return " · ".join(x for x in (f"silences {sil}" if sil else "", f"{o['integratedLufs']} LUFS" if o.get("integratedLufs") is not None else "",
                                           f"{o['bpm']} BPM" if o.get("bpm") else "") if x)
        if name in {"render_video"}:
            return str(a.get("quality") or "preview")
        if name in {"export_timeline"}:
            return str(a.get("format") or "")
        if name == "restore_version":
            return f"v{a.get('version')}"
        if name == "remember":
            return f"{a.get('key')}"
        if name == "get_asset_overview" and isinstance(o, dict):
            title = o.get("title") or (o.get("asset") or {}).get("title")
            dur = o.get("durationSec") or (o.get("asset") or {}).get("durationSec")
            return " · ".join(x for x in (_q(title, 40), _t(dur) if dur else "") if x)
    except Exception:  # noqa: BLE001 - summaries are best-effort
        return ""
    return ""


def detail(args: Any, limit: int = 600) -> str:
    """Compact JSON of the arguments for the expandable detail view (ids shortened)."""
    a = _load(args)
    try:
        s = json.dumps(a, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        s = str(a)
    return s[:limit] + ("…" if len(s) > limit else "")
