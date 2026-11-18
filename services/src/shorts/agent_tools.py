"""Agent tools — a deliberately small, general set. Genre know-how lives in skills (SKILL.md), not here.

Every tool runs as the bound user (ACL enforced) inside the bound project. Results are compact JSON; tools
that look at media may also return JPEG images (multimodal models read them directly).
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from . import acl, ddb, events, frames, ingest, models, projects, recipes, search, storage, timeline_ops, ui_widgets
from .auth import Principal
from .config import get_config

log = logging.getLogger(__name__)


@dataclass
class ToolContext:
    principal: Principal
    project_id: str
    holder: str  # lock holder id for agent edits, e.g. "agent:<env id>"
    parent_id: str | None = None  # set when a series orchestrator works on one of its child projects


@dataclass
class ToolResult:
    data: Any
    images: list[bytes] | None = None


TOOLS: dict[str, dict] = {}
HANDLERS: dict[str, Callable[[ToolContext, dict], ToolResult]] = {}


def tool(name: str, description: str, properties: dict, required: list[str] | None = None):
    def deco(fn):
        TOOLS[name] = {"name": name, "description": description,
                       "inputSchema": {"type": "object", "properties": properties, "required": required or [],
                                       "additionalProperties": False}}
        HANDLERS[name] = fn
        return fn
    return deco


# Tools that can act on a child project of a series (the orchestrator edits every short itself).
SERIES_TOOLS = {"get_timeline", "get_item", "edit_timeline", "preview_frames", "render_video", "list_renders",
                "list_versions", "restore_version", "export_timeline"}


def run(ctx: ToolContext, name: str, args: dict) -> ToolResult:
    if name not in HANDLERS:
        raise ValueError(f"Unknown tool {name}")
    args = dict(args or {})
    target = args.pop("projectId", None)
    if target and target != ctx.project_id:
        if name not in SERIES_TOOLS:
            raise ValueError(f"{name} does not take projectId")
        child = ddb.get(f"PROJ#{target}", "META")
        if not child or child.get("parentId") != ctx.project_id:
            raise ValueError("projectId must be a short (child project) of this series — see list_series")
        projects.require_project(ctx.principal, target, "edit")
        ctx = ToolContext(principal=ctx.principal, project_id=target, holder=ctx.holder, parent_id=ctx.project_id)
    return HANDLERS[name](ctx, args)


# ------------------------------------------------------------------ helpers
def _asset(ctx: ToolContext, asset_id: str, action: str = "use") -> dict:
    asset = ddb.get(f"ASSET#{asset_id}", "META")
    acl.require(ctx.principal, asset, action)
    if asset.get("status") != "ready":
        raise ValueError(f"Asset {asset_id} is not ready (status={asset.get('status')})")
    return asset


def _manifest(asset: dict) -> dict:
    return (ddb.get(f"CONTENT#{asset['contentKey']}", "META") or {}).get("manifest", {})


def _lang(asset: dict) -> str:
    return asset.get("analysisLanguage") or ingest.DEFAULT_LANGUAGE


def _json(key: str, default=None):
    try:
        return json.loads(storage.s3().get_object(Bucket=get_config().media_bucket, Key=key)["Body"].read())
    except storage.s3().exceptions.NoSuchKey:
        return default


def _analysis(asset: dict, name: str):
    return _json(f"{storage.content_prefix(asset['contentKey'])}/analysis/{_lang(asset)}/{name}.json", [])


def _transcript(asset: dict) -> dict:
    words, sentences = [], []
    prefix = f"{storage.content_prefix(asset['contentKey'])}/transcribe/0"
    listing = storage.s3().list_objects_v2(Bucket=get_config().media_bucket, Prefix=prefix).get("Contents", [])
    for obj in sorted(listing, key=lambda o: o["Key"]):
        t = _json(obj["Key"], {})
        words.extend(t.get("words", []))
        sentences.extend(t.get("sentences", []))
    return {"words": words, "sentences": sentences}


def _shots(asset: dict) -> list[float]:
    """Shot boundaries (source seconds) from Marengo dynamic segmentation of visual clips."""
    starts = set()
    listing = storage.s3().list_objects_v2(Bucket=get_config().media_bucket,
                                           Prefix=f"{storage.content_prefix(asset['contentKey'])}/vectors/marengo-")
    for obj in listing.get("Contents", []):
        for v in _json(obj["Key"], []):
            md = v["metadata"]
            if md.get("modality") == "visual" and md.get("scope") == "clip":
                starts.add(round(md["startSec"], 2))
    return sorted(starts)


def _in(seg: dict, start: float | None, end: float | None, s_key="start_sec", e_key="end_sec") -> bool:
    return (start is None or seg[e_key] >= start) and (end is None or seg[s_key] <= end)


def _proxy_url(asset: dict) -> str:
    key = _manifest(asset).get("proxyKey")
    if not key:
        raise ValueError("Asset has no video proxy")
    return storage.presign_get(key, 900)


def _publish(ctx: ToolContext, event: dict) -> None:
    events.publish(events.channel(ctx.project_id, ctx.principal.sub), [{**event, "projectId": ctx.project_id}])
    if ctx.parent_id:  # the series workspace (parent page) shows children live too
        events.publish(events.channel(ctx.parent_id, ctx.principal.sub), [{**event, "projectId": ctx.project_id}])


# ================================================================== context
@tool("get_context", "Project brief: synopsis, current timeline summary, the assets attached to the project and "
      "workspace language. Call first in every new turn.", {})
def get_context(ctx: ToolContext, _args: dict) -> ToolResult:
    project, _ = projects.require_project(ctx.principal, ctx.project_id, "view")
    tl = projects.load_timeline(project)
    attached = project.get("assetIds", [])
    assets = []
    for a in ddb.batch_get((f"ASSET#{aid}", "META") for aid in attached):
        if acl.effective_role(ctx.principal, a):
            assets.append({k: a.get(k) for k in ("assetId", "kind", "aiTitle", "title", "durationSec", "width",
                                                 "height", "pageCount", "status", "aiSummary")})
    tenant = ddb.get(f"TENANT#{ctx.principal.tenant_id}", "META") or {}
    brand = ddb.get(f"TENANT#{ctx.principal.tenant_id}", f"BRAND#{project['brandKitId']}") if project.get("brandKitId") else None
    return ToolResult({"project": {"title": project.get("title"), "synopsis": project.get("synopsis"),
                                   "version": project["currentVersion"], "aspect": tl["settings"]["aspect"],
                                   "recipe": project.get("recipe"), "recipeParams": project.get("recipeParams")},
                       "brandKit": ({k: v for k, v in brand.items() if k not in {"PK", "SK"}} if brand else None),
                       "workspaceLanguage": ingest.LANGUAGES.get(tenant.get("language", "ko"), "Korean"),
                       "attachedAssets": assets, "timeline": timeline_ops.summarize(tl),
                       "series": _series_summary(project),
                       "preferences": (ddb.get(f"USER#{ctx.principal.sub}", "PREFS") or {}).get("prefs", {})})


def _series_summary(project: dict) -> dict | None:
    if project.get("childIds"):
        return {"role": "orchestrator", "shorts": _children(project),
                "note": "This project orchestrates a series. Edit a short with edit_timeline/preview_frames/get_timeline "
                        "and projectId=<short projectId>; this project's own timeline stays empty."}
    if project.get("parentId"):
        return {"role": "short", "parentId": project["parentId"], "topic": project.get("topic")}
    return None


def _children(project: dict) -> list[dict]:
    out = []
    for c in ddb.batch_get((f"PROJ#{cid}", "META") for cid in project.get("childIds") or []):
        out.append({"projectId": c["projectId"], "topic": c.get("topic"), "title": c.get("title"),
                    "sourceRange": c.get("seriesRange"), "version": c.get("currentVersion"),
                    "durationSec": c.get("durationSec"), "aspect": c.get("aspect")})
    order = {cid: i for i, cid in enumerate(project.get("childIds") or [])}
    return sorted(out, key=lambda c: order.get(c["projectId"], 0))


# ================================================================== find
@tool("search_archive",
      "Semantic search over every video/image/document the user can access. Returns assets with matching moments "
      "(startSec/endSec). Tune `weights` per content: speech-led (lecture, interview) → transcription high; "
      "visual-led (nature, sports, b-roll) → visual high, transcription low.",
      {"query": {"type": "string", "description": "What to find, in any language"},
       "imageAssetId": {"type": "string", "description": "Find visually similar to this image asset"},
       "kinds": {"type": "array", "items": {"type": "string", "enum": ["video", "image", "audio", "document"]}},
       "weights": {"type": "object", "description": "Modality weights 0-3",
                   "properties": {m: {"type": "number"} for m in ("visual", "transcription", "audio", "description",
                                                                   "image", "page")}},
       "assetIds": {"type": "array", "items": {"type": "string"}, "description": "Restrict to these assets"},
       "limit": {"type": "integer", "minimum": 1, "maximum": 60},
       "perAsset": {"type": "integer", "minimum": 1, "maximum": 30, "description": "Max moments per asset (default 6)"}})
def search_archive(ctx: ToolContext, args: dict) -> ToolResult:
    image = None
    if args.get("imageAssetId"):
        a = _asset(ctx, args["imageAssetId"])
        key = _manifest(a).get("imageKey") or _manifest(a).get("thumbnailKey")
        image = storage.s3().get_object(Bucket=get_config().media_bucket, Key=key)["Body"].read()
    results = search.search(ctx.principal, text=args.get("query"), image=image, kinds=args.get("kinds"),
                            asset_ids=args.get("assetIds"), limit=int(args.get("limit", 10)),
                            weights=args.get("weights"), per_asset=int(args.get("perAsset", 6)))
    return ToolResult([{"assetId": r["asset"]["assetId"], "title": r["asset"]["displayTitle"],
                        "kind": r["asset"].get("kind"), "durationSec": r["asset"].get("durationSec"),
                        "moments": [{k: s.get(k) for k in ("startSec", "endSec", "modality", "modalities", "page",
                                                            "similarity", "text") if s.get(k) is not None}
                                    for s in r["segments"]]} for r in results])


@tool("get_asset_overview",
      "Summary, chapters and a content profile (speech density, shot rate, on-screen text, genre) of one asset. "
      "Use the profile to decide whether to edit by speech or by visuals.",
      {"assetId": {"type": "string"}}, ["assetId"])
def get_asset_overview(ctx: ToolContext, args: dict) -> ToolResult:
    a = _asset(ctx, args["assetId"])
    summary = ddb.get(f"CONTENT#{a['contentKey']}", f"ANALYSIS#summary#{_lang(a)}") or {}
    summary = {k: v for k, v in summary.items() if k not in {"PK", "SK"}}
    out = {"assetId": a["assetId"], "kind": a.get("kind"), "durationSec": a.get("durationSec"),
           "width": a.get("width"), "height": a.get("height"), "people": a.get("people", []),
           "tags": a.get("tags", []), "summary": summary,
           "chapters": [{k: c.get(k) for k in ("start_sec", "end_sec", "title", "summary")}
                        for c in _analysis(a, "chapters") or []],
           "profile": content_profile(a)}
    if a.get("kind") == "document":
        out["pageCount"] = a.get("pageCount")
    return ToolResult(out)


def content_profile(a: dict) -> dict:
    cached = ddb.get(f"CONTENT#{a['contentKey']}", f"PROFILE#{_lang(a)}")
    if cached:
        return cached["profile"]
    dur = float(a.get("durationSec") or 0)
    if a.get("kind") != "video" or dur <= 0:
        return {"kind": a.get("kind")}
    tr = _transcript(a)
    scenes = _analysis(a, "scenes") or []
    shots = _shots(a)
    wpm = len(tr["words"]) / (dur / 60)
    spm = len(shots) / (dur / 60)
    text_share = sum(1 for s in scenes if (s.get("on_screen_text") or "").strip()) / max(1, len(scenes))
    shot_types = {}
    for s in scenes:
        shot_types[s.get("shot_type", "other")] = shot_types.get(s.get("shot_type", "other"), 0) + 1
    speakers = len({w.get("spk") for w in tr["words"] if w.get("spk")})
    primary = "speech" if wpm > 110 and spm < 12 else "visual" if wpm < 40 else "mixed"
    summary = ddb.get(f"CONTENT#{a['contentKey']}", f"ANALYSIS#summary#{_lang(a)}") or {}
    profile = {"primarySignal": primary, "wordsPerMinute": round(wpm), "shotsPerMinute": round(spm, 1),
               "onScreenTextShare": round(text_share, 2), "speakers": speakers,
               "shotTypes": dict(sorted(shot_types.items(), key=lambda kv: -kv[1])[:5]),
               "genre": summary.get("genre"), "suggestedFormats": summary.get("suggested_formats", [])}
    ddb.put({"PK": f"CONTENT#{a['contentKey']}", "SK": f"PROFILE#{_lang(a)}", "profile": profile})
    return profile


@tool("get_scenes", "Timestamped scene list of a video range (summary, on-screen text, shot type, highlight "
      "score 1-5). Long ranges are paged (nextStartSec).", {"assetId": {"type": "string"}, "startSec": {"type": "number"},
                                                            "endSec": {"type": "number"}, "limit": {"type": "integer"}},
      ["assetId"])
def get_scenes(ctx: ToolContext, args: dict) -> ToolResult:
    a = _asset(ctx, args["assetId"])
    scenes = [s for s in _analysis(a, "scenes") or [] if _in(s, args.get("startSec"), args.get("endSec"))]
    limit = int(args.get("limit", 200))
    out = {"scenes": scenes[:limit], "total": len(scenes)}
    if len(scenes) > limit:
        out["nextStartSec"] = scenes[limit].get("start_sec") or scenes[limit].get("startSec")
        out["note"] = "truncated; call again with startSec=nextStartSec"
    return ToolResult(out)


@tool("read_transcript", "Transcript sentences (with speaker labels) of a time range. Use for speech-led edits; "
      "cut on sentence boundaries. Long ranges are paged (nextStartSec).",
      {"assetId": {"type": "string"}, "startSec": {"type": "number"}, "endSec": {"type": "number"},
       "limit": {"type": "integer"}}, ["assetId"])
def read_transcript(ctx: ToolContext, args: dict) -> ToolResult:
    a = _asset(ctx, args["assetId"])
    sents = [s for s in _transcript(a)["sentences"] if _in(s, args.get("startSec"), args.get("endSec"), "s", "e")]
    limit = int(args.get("limit", 600))
    out = {"sentences": [{"s": s["s"], "e": s["e"], "spk": s.get("spk"), "text": s["text"]} for s in sents[:limit]],
           "total": len(sents)}
    if len(sents) > limit:
        out["nextStartSec"] = sents[limit]["s"]
        out["note"] = "truncated; call again with startSec=nextStartSec"
    return ToolResult(out)


@tool("look", "See frames of a video range (contact sheet, row-major) or a document page / image. Use it to judge "
      "composition, on-screen slides/boards, or whether a moment is visually strong.",
      {"assetId": {"type": "string"}, "startSec": {"type": "number"}, "endSec": {"type": "number"},
       "frames": {"type": "integer", "minimum": 1, "maximum": 36}, "page": {"type": "integer"}}, ["assetId"])
def look(ctx: ToolContext, args: dict) -> ToolResult:
    a = _asset(ctx, args["assetId"])
    m = _manifest(a)
    if a.get("kind") in {"image", "document"}:
        imgs = m.get("images", [])
        img = next((i for i in imgs if i["index"] == int(args.get("page", 0))), imgs[0] if imgs else None)
        data = storage.s3().get_object(Bucket=get_config().media_bucket, Key=img["key"])["Body"].read()
        return ToolResult({"assetId": a["assetId"], "page": img["index"]}, [data])
    start = float(args.get("startSec", 0))
    end = float(args.get("endSec", min(start + 30, a.get("durationSec") or start + 30)))
    times = frames.spread(start, end, min(36, int(args.get("frames", 8))))
    cols = 4 if len(times) <= 16 else 6
    sheet = frames.contact_sheet(_proxy_url(a), times, cols=cols)
    return ToolResult({"assetId": a["assetId"], "frameTimes": times, "layout": f"{cols} columns, row-major"}, [sheet])


@tool("ask_video", "Ask the video-understanding model (Pegasus) a question about a video or a time range of it. "
      "Use for details analysis does not cover (who scores, what is written on the board, exact moment of X).",
      {"assetId": {"type": "string"}, "question": {"type": "string"}, "startSec": {"type": "number"},
       "endSec": {"type": "number"}}, ["assetId", "question"])
def ask_video(ctx: ToolContext, args: dict) -> ToolResult:
    """Long videos are analysed in chunks: ask every chunk the range overlaps (in parallel) and merge."""
    from concurrent.futures import ThreadPoolExecutor
    a = _asset(ctx, args["assetId"])
    chunks = _manifest(a).get("chunks", [])
    if not chunks:
        raise ValueError("This asset has no video analysis chunks")
    start = float(args["startSec"]) if args.get("startSec") is not None else None
    end = float(args["endSec"]) if args.get("endSec") is not None else None
    hit = [c for c in chunks if (start is None or c["endSec"] > start) and (end is None or c["startSec"] < end)] or chunks[:1]
    hit = hit[:8]
    instr = " " + models.language_instruction(ingest.LANGUAGES.get(_lang(a), "English")) + \
        " Give timestamps in seconds from the start of this clip."

    def one(c: dict) -> dict:
        off = c["startSec"]
        s0 = None if start is None or start <= off else start - off
        e0 = None if end is None or end >= c["endSec"] else end - off
        try:
            ans = models.pegasus_ask(storage.s3_uri(c["key"]), args["question"] + instr, s0, e0)
        except Exception as exc:  # noqa: BLE001 - one failing chunk should not lose the others
            ans = f"(error: {exc})"
        return {"chunkStartSec": off, "chunkEndSec": c["endSec"], "answer": ans}

    with ThreadPoolExecutor(max_workers=min(4, len(hit))) as ex:
        parts = list(ex.map(one, hit))
    return ToolResult({"answers": parts, "note": "Timestamps in each answer are relative to its chunkStartSec — add it "
                                                 "to get source time."})


# ================================================================== decide
@tool("snap", "Snap a cut point to the nearest natural boundary: sentence end (speech-led), shot change "
      "(visual-led) or audio beat/onset (music-led).",
      {"assetId": {"type": "string"}, "time": {"type": "number"},
       "to": {"type": "string", "enum": ["sentence", "word", "shot", "beat"]},
       "direction": {"type": "string", "enum": ["nearest", "before", "after"]}}, ["assetId", "time", "to"])
def snap(ctx: ToolContext, args: dict) -> ToolResult:
    a = _asset(ctx, args["assetId"])
    t, mode, direction = float(args["time"]), args["to"], args.get("direction", "nearest")
    if mode in {"sentence", "word"}:
        tr = _transcript(a)
        units = tr["sentences"] if mode == "sentence" else tr["words"]
        cands = sorted({round(u["s"], 3) for u in units} | {round(u["e"], 3) for u in units})
    elif mode == "shot":
        cands = _shots(a)
    else:
        cands = _beats(a)
    if not cands:
        return ToolResult({"time": t, "snapped": False, "reason": f"no {mode} boundaries available"})
    pool = [c for c in cands if (direction != "before" or c <= t) and (direction != "after" or c >= t)] or cands
    best = min(pool, key=lambda c: abs(c - t))
    return ToolResult({"time": best, "snapped": True, "movedBy": round(best - t, 3)})


def _beats(a: dict) -> list[float]:
    """Onsets from the stored waveform envelope (simple spectral-free onset detector)."""
    wf = _json(f"{storage.content_prefix(a['contentKey'])}/waveform.json", None)
    if not wf:
        return []
    peaks, rate = wf["peaks"], wf["peaksPerSec"]
    out, last = [], -1.0
    for i in range(2, len(peaks)):
        local = sum(peaks[max(0, i - rate // 2):i]) / max(1, min(i, rate // 2))
        if peaks[i] > 0.2 and peaks[i] > local * 1.6 and i / rate - last > 0.25:
            last = i / rate
            out.append(round(last, 3))
    return out


@tool("plan_topic_series",
      "Plan a SERIES of shorts from one long video, one short per topic (e.g. a 1-hour AWS networking lecture → "
      "'NAT Gateway' 50 s, 'Internet Gateway' 30 s, …). Fuses every signal: transcript keyword hits, Marengo search "
      "inside the video (speech, visual, scene descriptions, audio), Pegasus chapters/scenes, then Pegasus confirms the "
      "exact range. Weights follow the video's content profile (speech-led → transcript; visual-led → visual). Returns "
      "per-topic ranges with confidence and evidence. Show them to the user (show_ui clips block) before create_series.",
      {"assetId": {"type": "string"}, "topics": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
       "lengthSec": {"type": "number", "description": "Target length per short (default 45)"},
       "verify": {"type": "boolean", "description": "Pegasus refinement (default true)"}}, ["assetId", "topics"])
def plan_topic_series(ctx: ToolContext, args: dict) -> ToolResult:
    from . import batches
    _asset(ctx, args["assetId"])
    plan = batches.plan_topics(ctx.principal, args["assetId"], list(args["topics"]), float(args.get("lengthSec") or 45),
                               bool(args.get("verify", True)))
    return ToolResult({"plan": plan, "next": "Confirm with the user (show_ui clips with these ranges), then create_series."})


@tool("create_series",
      "Create the shorts of a series as child projects of THIS project (tabs in the user's workspace). Items usually "
      "come from plan_topic_series, adjusted with the user. Then build every short YOURSELF: edit_timeline / "
      "preview_frames / get_timeline with projectId=<child> — you see all of them and keep them consistent (style, "
      "captions, intro/outro, no duplicate moments). Set delegate=true only if the user wants parallel independent "
      "runs (faster, but no shared oversight).",
      {"assetId": {"type": "string"}, "items": {"type": "array", "items": {"type": "object", "properties": {
          "topic": {"type": "string"}, "startSec": {"type": "number"}, "endSec": {"type": "number"}}}, "maxItems": 10},
       "aspect": {"type": "string"}, "delegate": {"type": "boolean"}, "instruction": {"type": "string"},
       "userConfirmed": {"type": "boolean"}},
      ["assetId", "items", "userConfirmed"])
def create_series(ctx: ToolContext, args: dict) -> ToolResult:
    if not args.get("userConfirmed"):
        raise ValueError("Show the planned shorts to the user and get a yes first")
    asset = _asset(ctx, args["assetId"])
    parent, _ = projects.require_project(ctx.principal, ctx.project_id, "edit")
    if parent.get("parentId"):
        raise ValueError("A short cannot hold its own series; create the series from the parent project")
    aspect = args.get("aspect") or parent.get("aspect") or "9:16"
    created = []
    for it in list(args["items"])[:10]:
        topic = str(it.get("topic") or "").strip()[:120]
        title = topic or f"{asset.get('title') or asset.get('aiTitle') or asset.get('filename')} #{len(created) + 1}"
        child = projects.create(ctx.principal, title[:200], aspect, parent.get("synopsis", ""), [asset["assetId"]],
                                parent_id=ctx.project_id)
        ddb.update(f"PROJ#{child['projectId']}", "META", {
            "topic": topic or None, "seriesRange": [float(it.get("startSec", 0)), float(it.get("endSec", 0))],
            **({"brandKitId": parent["brandKitId"]} if parent.get("brandKitId") else {}),
            **({"folderId": parent["folderId"]} if parent.get("folderId") else {})})
        created.append({"projectId": child["projectId"], "topic": topic, "sourceRange": [it.get("startSec"), it.get("endSec")]})
    if args.get("delegate"):
        from . import batches
        for c in created:
            batches.launch_child(ctx.principal, c["projectId"], asset["assetId"], c, args.get("instruction") or "")
    _publish(ctx, {"type": "series.updated", "count": len(created)})
    return ToolResult({"shorts": created, "next": "Build each short with edit_timeline(projectId=…) + preview_frames(projectId=…)"
                       if not args.get("delegate") else "Each short is being edited by its own run; review them with list_series."})


@tool("list_series", "The shorts of this series: topic, planned source range, version, length — to review and "
      "compare them, or pick which one to change.", {})
def list_series(ctx: ToolContext, _args: dict) -> ToolResult:
    project, _ = projects.require_project(ctx.principal, ctx.project_id, "view")
    return ToolResult({"shorts": _children(project)})


@tool("analyze_audio",
      "Audio analysis of a time range: silences (for removing pauses/dead air), loudness (LUFS), beat times and an "
      "estimated tempo (BPM) for cutting to music.",
      {"assetId": {"type": "string"}, "startSec": {"type": "number"}, "endSec": {"type": "number"},
       "silenceDb": {"type": "number", "description": "Silence threshold in dB (default -35)"},
       "minSilenceSec": {"type": "number", "description": "Minimum silence length (default 0.4)"}}, ["assetId"])
def analyze_audio(ctx: ToolContext, args: dict) -> ToolResult:
    import re
    import subprocess
    a = _asset(ctx, args["assetId"])
    m = _manifest(a)
    key = m.get("audioKey") or m.get("proxyKey") or a.get("originalKey")
    url = storage.presign_get(key, 900)
    start = float(args.get("startSec", 0))
    end = float(args.get("endSec", a.get("durationSec") or start + 600))
    end = min(end, start + 3600)
    db, dmin = float(args.get("silenceDb", -35)), float(args.get("minSilenceSec", 0.4))
    from . import safeurl
    safeurl.check(url)
    p = subprocess.run([frames.ffmpeg_bin(), "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",  # nosec B603
                        "-i", url, "-vn", "-af", f"silencedetect=noise={db}dB:d={dmin},ebur128=framelog=quiet",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=240)
    log_ = p.stderr
    silences, cur = [], None
    for line in log_.splitlines():
        if "silence_start" in line:
            cur = float(re.search(r"silence_start: (-?[\d.]+)", line).group(1)) + start
        elif "silence_end" in line and cur is not None:
            e = float(re.search(r"silence_end: ([\d.]+)", line).group(1)) + start
            silences.append({"startSec": round(max(start, cur), 3), "endSec": round(e, 3), "dur": round(e - max(start, cur), 3)})
            cur = None
    if cur is not None:
        silences.append({"startSec": round(cur, 3), "endSec": round(end, 3), "dur": round(end - cur, 3)})
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", log_)
    lra = re.findall(r"LRA:\s+([\d.]+) LU", log_)
    beats = [b for b in _beats(a) if start <= b <= end]
    bpm = None
    if len(beats) > 8:
        gaps = sorted(b2 - b1 for b1, b2 in zip(beats, beats[1:]) if 0.25 < b2 - b1 < 1.5)
        if gaps:
            med = gaps[len(gaps) // 2]
            bpm = round(60 / med, 1)
            while bpm < 70:
                bpm *= 2
            while bpm > 180:
                bpm /= 2
    return ToolResult({"range": [start, end], "silences": silences[:300], "silenceTotalSec": round(sum(x["dur"] for x in silences), 2),
                       "integratedLufs": float(lufs[-1]) if lufs else None, "loudnessRangeLu": float(lra[-1]) if lra else None,
                       "beats": beats[:400], "bpm": bpm})


@tool("export_timeline", "Export the current timeline for another editor (OpenTimelineIO, FCPXML for Final Cut / "
      "DaVinci / Premiere, or CMX3600 EDL). The user gets a download link in the chat.",
      {"format": {"type": "string", "enum": ["otio", "fcpxml", "edl"]}}, ["format"])
def export_timeline(ctx: ToolContext, args: dict) -> ToolResult:
    from . import export as exporter
    project, _ = projects.require_project(ctx.principal, ctx.project_id, "view")
    r = exporter.export_project(ctx.principal, project, args["format"])
    key = f"exports/{project['tenantId']}/{project['projectId']}/{r['filename']}"
    storage.s3().put_object(Bucket=get_config().media_bucket, Key=key, Body=r["content"].encode("utf-8"),
                            ContentType=r.get("contentType", "application/octet-stream"))
    url = storage.presign_get(key, 3600, r["filename"])
    _publish(ctx, {"type": "download", "url": url, "filename": r["filename"]})
    return ToolResult({"filename": r["filename"], "status": "download link shown to the user"})


@tool("analyze_framing",
      "Decide how a 16:9 (or any) source should fill the target aspect for a time range: per-shot subject focus "
      "point and a layout (crop = follow the subject, split = speaker + slide/board stacked, fit_blur = whole frame on "
      "blurred background, letterbox). Returns a reframe value ready for edit_timeline `update`.",
      {"assetId": {"type": "string"}, "startSec": {"type": "number"}, "endSec": {"type": "number"},
       "aspect": {"type": "string", "description": "Target frame 'W:H' — the canvas, or the box the clip will fill "
                  "(e.g. '1:1' for a square box inside a 9:16 canvas). Default: the canvas aspect."},
       "intent": {"type": "string", "description": "What must stay visible, e.g. 'follow the ball', "
                                                    "'keep lecturer and the board'"},
       "maxZoom": {"type": "number", "description": "Upper bound for zoom-in (default 1.6; up to 4)"},
       "maxShots": {"type": "integer", "description": "Max shots analysed (default 12, up to 30)"}},
      ["assetId", "startSec", "endSec"])
def analyze_framing(ctx: ToolContext, args: dict) -> ToolResult:
    a = _asset(ctx, args["assetId"])
    start, end = float(args["startSec"]), float(args["endSec"])
    shots = [s for s in _shots(a) if start <= s < end]
    bounds = sorted({start, *shots, end})
    segs = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)
            if bounds[i + 1] - bounds[i] > 0.3][:max(1, min(30, int(args.get("maxShots", 12))))]
    times = [round((s + e) / 2, 3) for s, e in segs] or [round((start + end) / 2, 3)]
    imgs = frames.frames(_proxy_url(a), times, 480)
    aspect = args.get("aspect") or projects.load_timeline(projects.require_project(ctx.principal, ctx.project_id, "view")[0])["settings"].get("aspect", "9:16")
    plan = models.framing_plan(imgs, times, aspect, args.get("intent", ""), a.get("width"), a.get("height"),
                               max_zoom=max(1.0, min(4.0, float(args.get("maxZoom", 1.6)))))
    path, layouts = [], []
    for (s, e), f in zip(segs or [(start, end)], plan):
        x, y, scale = float(f.get("x", 0.5)), float(f.get("y", 0.5)), float(f.get("zoom", 1.0))
        path += [{"t": round(s, 3), "x": x, "y": y, "scale": scale}, {"t": round(e - 0.02, 3), "x": x, "y": y, "scale": scale}]
        layouts.append({"startSec": s, "endSec": e, "layout": f.get("layout", "crop"), "subject": f.get("subject")})
    dominant = max({l["layout"] for l in layouts}, key=lambda k: sum(1 for l in layouts if l["layout"] == k))
    return ToolResult({"reframe": {"mode": "auto", "layout": dominant, "path": path}, "shots": layouts,
                       "note": "path t is source time; apply with edit_timeline update {reframe}"},
                      None)


# ================================================================== act
@tool("get_timeline", "Current timeline: tracks, items with ids, timings, layout/style essentials, provenance, user pins. "
      "full=true returns every property (raw JSON) — use it before relative edits ('a bit bigger').",
      {"full": {"type": "boolean"}})
def get_timeline(ctx: ToolContext, args: dict) -> ToolResult:
    project, _ = projects.require_project(ctx.principal, ctx.project_id, "view")
    return ToolResult({"version": project["currentVersion"],
                       **timeline_ops.summarize(projects.load_timeline(project), full=bool(args.get("full")))})


@tool("get_item", "Every property of one timeline item (and its track), e.g. to read a style/transform before changing it.",
      {"itemId": {"type": "string"}}, ["itemId"])
def get_item(ctx: ToolContext, args: dict) -> ToolResult:
    project, _ = projects.require_project(ctx.principal, ctx.project_id, "view")
    tl = projects.load_timeline(project)
    tr, it, idx = timeline_ops.find_item(tl, args["itemId"])
    return ToolResult({"track": {k: v for k, v in tr.items() if k != "items"}, "index": idx, "item": it,
                       "pinned": [p for p in tl.get("pins", []) if p.startswith(f"items/{it['id']}")]})


@tool("preview_frames",
      "Render real output frames of the CURRENT timeline at the given times (same compositor as the final video: "
      "layouts, overlays, effects, text, captions, shapes). Use it to check your own edit before telling the user it's "
      "done, and to compare options.",
      {"times": {"type": "array", "items": {"type": "number"}, "maxItems": 8,
                 "description": "Timeline seconds; default: 4 evenly spaced"},
       "width": {"type": "integer", "minimum": 160, "maximum": 1080}})
def preview_frames(ctx: ToolContext, args: dict) -> ToolResult:
    import os
    from . import render, renders
    if not os.environ.get("FFMPEG_BIN"):
        os.environ["FFMPEG_BIN"] = frames.ffmpeg_bin()
    project, _ = projects.require_project(ctx.principal, ctx.project_id, "view")
    tl = projects.load_timeline(project)
    timeline_ops.relayout(tl)
    dur = timeline_ops.duration(tl)
    if dur <= 0:
        raise ValueError("The timeline is empty")
    times = [min(max(0.0, float(t)), max(0.0, dur - 0.05)) for t in (args.get("times") or [])][:8]
    if not times:
        times = [round(dur * (k + 0.5) / 4, 2) for k in range(4)]
    width = int(args.get("width", 360 if len(times) > 2 else 540))
    imgs = render.render_stills(tl, renders.media_map(tl, "preview"), times, width)
    return ToolResult({"frames": [{"t": t} for t in times], "durationSec": round(dur, 2),
                       "note": "one image per time, in order"}, imgs)


@tool("render_video", "Start rendering the current version to a video file (preview = fast 540p, final = full quality). "
      "The user sees progress in Export → Renders. Returns the render id.",
      {"quality": {"type": "string", "enum": ["preview", "final"]}})
def render_video(ctx: ToolContext, args: dict) -> ToolResult:
    from . import renders
    project, _ = projects.require_project(ctx.principal, ctx.project_id, "edit")
    r = renders.request(project, args.get("quality", "preview"), ctx.principal.sub)
    _publish(ctx, {"type": "render.requested", "renderId": r["renderId"], "quality": r["quality"]})
    return ToolResult({"renderId": r["renderId"], "status": r["status"], "quality": r["quality"]})


@tool("list_renders", "Renders of this project with status/progress (and download links when done).", {})
def list_renders(ctx: ToolContext, _args: dict) -> ToolResult:
    from . import renders
    project, _ = projects.require_project(ctx.principal, ctx.project_id, "view")
    out = renders.list_for(project)[:10]
    for r in out:
        r.pop("urls", None)  # links are shown to the user in the UI
    return ToolResult(out)


@tool("list_versions", "Saved versions of the timeline (who changed what).", {})
def list_versions(ctx: ToolContext, _args: dict) -> ToolResult:
    project, _ = projects.require_project(ctx.principal, ctx.project_id, "view")
    return ToolResult(projects.versions(ctx.project_id)[:30])


@tool("restore_version", "Restore an earlier timeline version (creates a new version; nothing is lost).",
      {"version": {"type": "integer"}}, ["version"])
def restore_version(ctx: ToolContext, args: dict) -> ToolResult:
    project, _ = projects.require_project(ctx.principal, ctx.project_id, "edit")
    if not projects.acquire_lock(project, ctx.holder):
        raise ValueError("The user is editing the timeline right now")
    project = ddb.get(f"PROJ#{ctx.project_id}", "META", consistent=True)
    tl = projects.load_timeline(project, int(args["version"]))
    version = projects.save(ctx.principal, project, tl, project["currentVersion"], "agent",
                            f"restore v{args['version']}", holder=ctx.holder)["currentVersion"]
    _publish(ctx, {"type": "timeline.updated", "version": version, "message": f"restore v{args['version']}"})
    return ToolResult({"version": version})


OPS_DOC = (
    "Ops (applied in order, each atomically; rejected ops are reported, others still apply). Everything below is "
    "rendered by the final renderer and shown in the preview — compose freely.\n"
    "CLIPS: add_clip {assetId, srcIn, srcOut, track?, index? (main track), start? (other tracks), duration? (images), "
    "page? (document page), speed?, reason} · remove {itemId} · trim {itemId, srcIn?, srcOut?, duration?} · "
    "split {itemId, at} · move {itemId, index? | track? + start?}\n"
    "UPDATE: update {itemId, set:{...}} — any of: srcIn, srcOut, assetId (swap media), speed (0.1–16), mode "
    "(normal|reverse|freeze), volume (0–4), fadeIn, fadeOut, opacity (0–1), start/duration (free tracks), "
    "transitionIn {type, duration ≤3}, transform, reframe, kenBurns {from:{x,y,scale},to:{...}}, effects, text, style, "
    "position, animation, rotation (text, degrees), shape fields.\n"
    "  reframe (main/overlay clips) = {layout, path?, box?, fit?, background?}: layout crop (cover the canvas, follow "
    "path [{t (source sec), x, y, scale≥1}]) | box (clip fills ANY rectangle: box {x,y,w,h} 0..1 of the canvas, top-left "
    "origin; fit cover|contain; background black|blur|#rrggbb — e.g. a 1:1 square centred in 9:16 = box {x:0, y:0.2188, "
    "w:1, h:0.5625}; top two-thirds = {x:0,y:0,w:1,h:0.667}) | split (subject top + whole frame bottom; splitRatio) | "
    "fit_blur | letterbox (pad uses settings.background).\n"
    "  transform (overlay layers = PIP; main = flip/rotate) = {x, y (centre 0..1), scale (width fraction; <1 = PIP), "
    "rotation (deg), flipH, flipV, crop {l,t,r,b fractions}}.\n"
    "  effects = [{type: color, brightness(-1..1), contrast(1), saturation(1), gamma(1), temperature(-1 warm..1 cool), "
    "tint} | blur {radius px} | vignette {amount 0..1} | sharpen {amount} | denoise {amount} | grayscale | sepia | invert "
    "| pixelate {size} | mirror | chromaKey {color, similarity, smoothness} (overlays: green screen) | eq {low, mid, high "
    "-1..1} (audio) | audioDenoise {amount} | pitch {factor}].\n"
    "  transitionIn / transitionOut {type, duration}: on main clips = xfade between neighbours (fade|dissolve|dipToBlack|"
    "dipToWhite|wipeLeft|wipeUp|slideLeft|zoom|blur or ANY ffmpeg xfade name: circleopen, radial, pixelize, slideright, "
    "smoothup, diagtl, squeezeh, coverleft, revealup, …; transitionOut on the last clip fades out). On overlay layers, "
    "text and shapes = enter/exit motion: fade, slideLeft|slideRight|slideUp|slideDown, zoom (all also fade).\n"
    "  keyframes {prop: [{t (sec from item start), v, ease linear|easeIn|easeOut|easeInOut|hold}]}: overlay layers x, y, "
    "scale, rotation, opacity; main clips scale (≥1), x, y (pan/zoom) and rotation; text x, y, scale, rotation, opacity; "
    "shapes x, y, opacity; any clip volume (audio automation).\n"
    "  speedCurve [{t (source sec from srcIn), v (speed)}] = speed ramp (e.g. slow-mo into the goal then back to 1×).\n"
    "  lut effect {type: lut, assetId (an uploaded .cube file), intensity 0..1}.\n"
    "  kenBurns null on images = static (no movement).\n"
    "TEXT: add_text {text, start, duration, track?, role?, position {x, y, anchor center|top|bottom, maxWidth}, "
    "style {font, size (fraction of height), weight, color, background (box colour, rgba ok), padding, stroke {color, "
    "width}, shadow {color, x, y, blur}, align left|center|right, italic, uppercase, letterSpacing}, animation {in: none|"
    "fade|pop|slideUp|slideDown|typewriter|wordByWord, out: none|fade}, rotation, opacity}\n"
    "SHAPES: add_shape {shape rect|roundRect|ellipse|progressBar, box {x,y,w,h}, fill (rgba ok), stroke?, radius? "
    "(roundRect 0..1), opacity?, start, duration, track?} — boxes behind titles, bars, frames, dividers, progress bars.\n"
    "CAPTIONS: captions {preset clean|bold|karaoke|pop|boxed|minimal, style? (wordAnim pop|bounce|grow animates the "
    "active word), y?, source? (track id, e.g. 'a2' for a voiceover/TTS track; default main)} (word-timed from speech) · add_caption {text | words [{s,e,w}], start, duration, track?, preset?, style?, position {x, y}} (any "
    "caption, e.g. for voiceover/TTS) · update {itemId, set:{words|style|position|preset}}.\n"
    "TRACKS: add_track {kind video|overlay|text|caption|audio, name?, index?, audioRole? dialogue|music|voiceover|sfx, "
    "volume?} · update_track {trackId, set:{name, volume, muted, hidden, locked, audioRole}} · move_track {trackId, "
    "index} (stacking: index 0 = bottom) · remove_track {trackId} · clear_track {track}. Overlay clips are silent "
    "unless volume > 0. update_track {trackId: 'v1', set:{magnetic:false}} makes the main track free-positioned (items keep "
    "their start; gaps show the background; add_clip/move take `start`).\n"
    "PROJECT: settings {set:{aspect ('9:16', '21:9', any 'W:H'), width+height (custom px, 240–4096), fps, "
    "background, loudness (LUFS), safeZone, ducking {enabled, threshold, ratio, attack, release}}} · meta {set:{title, "
    "description, hashtags, thumbnailAt}} · marker {t, label, kind} · brand {kit}\n"
    "Track v1 is the magnetic main track (sequential, no gaps). Default tracks: o1 B-roll/PIP, t1 text, c1 captions, "
    "a1 music, a2 voiceover — add more as needed. Music ducks under dialogue and voiceover. Pinned paths (user edits) "
    "cannot be changed — work around them. After a visual change, verify with preview_frames.")


@tool("edit_timeline", "Apply edit operations to the project timeline and save a new version. " + OPS_DOC,
      {"ops": {"type": "array", "items": {"type": "object"}}, "message": {"type": "string"}}, ["ops"])
def edit_timeline(ctx: ToolContext, args: dict) -> ToolResult:
    project, _ = projects.require_project(ctx.principal, ctx.project_id, "edit")
    if not projects.acquire_lock(project, ctx.holder):
        raise ValueError("The user is editing the timeline right now; ask them or try again later")
    project = ddb.get(f"PROJ#{ctx.project_id}", "META", consistent=True)
    tl = projects.load_timeline(project)
    ops = list(args.get("ops") or [])[:200]
    ids = set().union(set(), *[timeline_ops.asset_refs(o) for o in ops if isinstance(o, dict)]) | {
        i["assetId"] for t in tl["tracks"] for i in t["items"] if i.get("assetId")}
    accessible = acl.accessible_asset_ids(ctx.principal)
    assets = {a["assetId"]: a for a in ddb.batch_get((f"ASSET#{i}", "META") for i in ids if i in accessible)
              if a.get("status") == "ready"}
    for o in ops:
        if o.get("op") == "captions" and "transcripts" not in o:
            src = timeline_ops.track(tl, o["source"]) if o.get("source") not in (None, "main") else timeline_ops.main_track(tl)
            o["transcripts"] = {aid: _transcript(assets[aid]) for aid in {i.get("assetId") for i in src["items"]}
                                if aid in assets}
    new_tl, results = timeline_ops.apply(tl, ops, "agent", assets)
    applied = [r for r in results if r["ok"]]
    version = project["currentVersion"]
    if applied:
        version = projects.save(ctx.principal, project, new_tl, project["currentVersion"], "agent",
                                args.get("message", ""), holder=ctx.holder)["currentVersion"]
        _publish(ctx, {"type": "timeline.updated", "version": version, "message": args.get("message", "")})
    return ToolResult({"version": version, "results": results, "durationSec": round(timeline_ops.duration(new_tl), 2)})


@tool("ask_user",
      "Ask the user something and END YOUR TURN right after. kind: text | choice (options) | clips (candidate "
      "moments to pick, e.g. search results) | confirm (e.g. before a costly generation; include estimate) | range.",
      {"question": {"type": "string"},
       "kind": {"type": "string", "enum": ["text", "choice", "clips", "confirm", "range"]},
       "options": {"type": "array", "items": {"type": "string"}},
       "clips": {"type": "array", "items": {"type": "object", "properties": {
           "assetId": {"type": "string"}, "startSec": {"type": "number"}, "endSec": {"type": "number"},
           "label": {"type": "string"}}}},
       "multiple": {"type": "boolean"}, "estimate": {"type": "object"}}, ["question", "kind"])
def ask_user(ctx: ToolContext, args: dict) -> ToolResult:
    qid = "q" + base64.urlsafe_b64encode(__import__("os").urandom(6)).decode().rstrip("=")
    q = {"type": "question", "questionId": qid, **{k: args.get(k) for k in
                                                   ("question", "kind", "options", "clips", "multiple", "estimate")}}
    ddb.put({"PK": f"PROJ#{ctx.project_id}", "SK": f"QUESTION#{qid}", **q, "createdAt": ddb.now_ms(),
             "ttl": ddb.now_ms() // 1000 + 7 * 86400})
    _publish(ctx, q)
    return ToolResult({"questionId": qid, "status": "shown to user",
                       "instruction": "End your turn now. The answer arrives as the next user message."})


@tool("show_ui",
      "Show an interactive widget in the chat (rendered by the app; no HTML). Prefer this over plain-text questions "
      "whenever the user should SEE or PICK something: candidate clips (thumbnails + playable ranges), A/B versions, "
      "styles, lengths, colors, ordering, a point on a frame, a time range, proofreading lines. Compose up to 12 blocks; "
      "every input block needs a stable `id` — the answer arrives as the next user message "
      "'[UI <uiId> submitted]: {blockId: value, ...}'. If any block takes input, END YOUR TURN after calling this. "
      "Display-only widgets (markdown/kv/table/media/timeline) do not end the turn. Block types: "
      "markdown{text} · kv{items:[{label,value}]} · table{columns:[str], rows:[[...]], selectable?, multiple?} · "
      "media{assetId, startSec?, endSec?, caption?} · timeline{segments:[{assetId,startSec,endSec,label}]} (proposed edit) · "
      "choice{label, options:[{value,label,description?,assetId?,startSec?,endSec?,color?}], multiple?, style?:'buttons'|'cards'|'list'} · "
      "clips{label, clips:[{assetId,startSec,endSec,label?,reason?,score?}], multiple?, min?, max?} · "
      "compare{label, options:[{value,label,assetId,startSec?,endSec?,description?}]} (side-by-side, pick one) · "
      "slider{label,min,max,step?,default?,unit?} · range{label,assetId,startSec,endSec,min?,max?} (trim on a player) · "
      "text_input{label,placeholder?,default?,multiline?} · select{label,options:[{value,label}],default?} · "
      "toggle{label,default?} · color{label,default?,swatches?:[hex]} · order{label,items:[{value,label,assetId?,startSec?,endSec?}]} (drag to reorder) · "
      "frame_point{label,assetId,atSec} (user clicks a point → {x,y} 0..1, e.g. subject or logo position) · "
      "assets{label,kinds?:[video|image|audio|document],multiple?} (pick from the archive) · "
      "caption_style{label,default?,sample?} (visual preset picker) · text_list{label,items:[{id,text,startSec?}]} (edit lines) · "
      "rating{label,max?}. Always include assetId on every clip/option/segment that has times. Top level: {title?, description?, blocks, submitLabel?, allowSkip?}.",
      {"title": {"type": "string"}, "description": {"type": "string"},
       "blocks": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
       "submitLabel": {"type": "string"}, "allowSkip": {"type": "boolean"}}, ["blocks"])
def show_ui(ctx: ToolContext, args: dict) -> ToolResult:
    project = ddb.get(f"PROJ#{ctx.project_id}", "META") or {}
    ids = project.get("assetIds") or []
    spec = ui_widgets.validate(args, ctx.principal, ids[0] if len(ids) == 1 else None)
    item = ui_widgets.create(ctx.project_id, spec)
    _publish(ctx, {"type": "ui", "uiId": item["uiId"], "spec": spec, "createdAt": item["createdAt"]})
    if spec["interactive"]:
        return ToolResult({"uiId": item["uiId"], "status": "shown to user",
                           "instruction": "End your turn now. The user's input arrives as the next user message."})
    return ToolResult({"uiId": item["uiId"], "status": "shown"})


@tool("remember", "Save a durable preference of this user (e.g. caption style, default length, tone) for future "
      "projects.", {"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"])
def remember(ctx: ToolContext, args: dict) -> ToolResult:
    prefs = (ddb.get(f"USER#{ctx.principal.sub}", "PREFS") or {}).get("prefs", {})
    prefs[str(args["key"])[:64]] = str(args["value"])[:500]
    ddb.put({"PK": f"USER#{ctx.principal.sub}", "SK": "PREFS", "prefs": dict(list(prefs.items())[-50:])})
    return ToolResult({"saved": True})


# ================================================================== skills / recipes / brand
@tool("list_skills", "List editing skills (built-in + this workspace's own playbooks) with one-line descriptions.", {})
def list_skills(ctx: ToolContext, _args: dict) -> ToolResult:
    return ToolResult([{"name": s["name"], "description": s["description"], "workspace": not s["builtin"]}
                       for s in recipes.list_skills(ctx.principal.tenant_id)])


@tool("read_skill", "Read one skill playbook before applying it. Workspace skills override built-ins of the same name.",
      {"name": {"type": "string"}}, ["name"])
def read_skill(ctx: ToolContext, args: dict) -> ToolResult:
    sk = recipes.get_skill(ctx.principal.tenant_id, args["name"])
    if not sk:
        raise ValueError(f"Unknown skill {args['name']}; call list_skills")
    return ToolResult({"name": sk["name"], "body": sk["body"]})


@tool("apply_brand_kit", "Apply the project's (or a named) brand kit: logo overlay, intro/outro, caption & text styling. "
      "Safe to call again after edits (replaces previous brand elements).",
      {"brandKitId": {"type": "string", "description": "Defaults to the project's brand kit"}})
def apply_brand_kit(ctx: ToolContext, args: dict) -> ToolResult:
    from . import brand
    project, _ = projects.require_project(ctx.principal, ctx.project_id, "edit")
    kit = brand.resolve(ctx.principal, args.get("brandKitId") or project.get("brandKitId"))
    return edit_timeline(ctx, {"ops": [{"op": "brand", "kit": kit}], "message": f"brand kit {kit.get('name', '')}"})


# ================================================================== generation (mode=full)
@tool("estimate_generation", "Estimate time and cost of generating media (GPU cold starts included). Always show the "
      "estimate to the user with ask_user kind=confirm before generate_media.",
      {"kind": {"type": "string", "enum": ["image", "image_edit", "video", "speech", "music"]},
       "seconds": {"type": "number"}, "provider": {"type": "string", "enum": ["voxcpm", "polly", "elevenlabs"]}}, ["kind"])
def estimate_generation(ctx: ToolContext, args: dict) -> ToolResult:
    from . import generation
    if not generation.enabled():
        return ToolResult({"available": False, "reason": "Generation mode is not deployed in this environment"})
    return ToolResult(generation.estimate(args["kind"], args))


@tool("generate_media",
      "Generate a new asset when the archive has nothing suitable: image (text→image), image_edit (keep a character/"
      "product consistent from reference images), video (text/image→5 s clip), speech (voiceover), music. ONLY after the "
      "user confirmed the estimate (set userConfirmed=true). Never use photos of real people as references. The result "
      "becomes an archive asset (ai_generated) and is analyzed like other footage; poll list_generation_jobs.",
      {"kind": {"type": "string", "enum": ["image", "image_edit", "video", "speech", "music"]},
       "prompt": {"type": "string", "description": "Visual/music description, or the narration text for speech"},
       "referenceAssetIds": {"type": "array", "items": {"type": "string"}},
       "aspect": {"type": "string"}, "seconds": {"type": "number"}, "voice": {"type": "string"},
       "provider": {"type": "string", "enum": ["voxcpm", "polly", "elevenlabs"]},
       "userConfirmed": {"type": "boolean"}}, ["kind", "prompt", "userConfirmed"])
def generate_media(ctx: ToolContext, args: dict) -> ToolResult:
    from . import generation
    if not args.get("userConfirmed"):
        raise ValueError("Ask the user to confirm the estimate first (ask_user kind=confirm), then retry with userConfirmed=true")
    params = {k: v for k, v in args.items() if k not in {"kind", "userConfirmed"}}
    if args["kind"] == "speech":
        params["text"] = params.pop("prompt")
    return ToolResult(generation.start(ctx.principal, ctx.project_id, args["kind"], params))


@tool("list_generation_jobs", "Status of this project's generation jobs (assetId appears when done; the asset is "
      "usable once its status is ready).", {})
def list_generation_jobs(ctx: ToolContext, _args: dict) -> ToolResult:
    from . import generation
    return ToolResult(generation.list_jobs(ctx.project_id))


# Series tools accept `projectId` (a short of this series). Added to the schemas once every tool is registered.
for _n in SERIES_TOOLS:
    if _n in TOOLS:
        TOOLS[_n]["inputSchema"]["properties"]["projectId"] = {
            "type": "string", "description": "Series only: the short (child project) to act on; default this project"}
