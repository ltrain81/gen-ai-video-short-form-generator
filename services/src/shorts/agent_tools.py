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


def run(ctx: ToolContext, name: str, args: dict) -> ToolResult:
    if name not in HANDLERS:
        raise ValueError(f"Unknown tool {name}")
    return HANDLERS[name](ctx, args or {})


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
    events.publish(events.channel(ctx.project_id, ctx.principal.sub), [event])


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
                       "preferences": (ddb.get(f"USER#{ctx.principal.sub}", "PREFS") or {}).get("prefs", {})})


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
       "limit": {"type": "integer", "minimum": 1, "maximum": 30}})
def search_archive(ctx: ToolContext, args: dict) -> ToolResult:
    image = None
    if args.get("imageAssetId"):
        a = _asset(ctx, args["imageAssetId"])
        key = _manifest(a).get("imageKey") or _manifest(a).get("thumbnailKey")
        image = storage.s3().get_object(Bucket=get_config().media_bucket, Key=key)["Body"].read()
    results = search.search(ctx.principal, text=args.get("query"), image=image, kinds=args.get("kinds"),
                            asset_ids=args.get("assetIds"), limit=int(args.get("limit", 10)),
                            weights=args.get("weights"), per_asset=6)
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
      "score 1-5).", {"assetId": {"type": "string"}, "startSec": {"type": "number"}, "endSec": {"type": "number"}},
      ["assetId"])
def get_scenes(ctx: ToolContext, args: dict) -> ToolResult:
    a = _asset(ctx, args["assetId"])
    scenes = [s for s in _analysis(a, "scenes") or [] if _in(s, args.get("startSec"), args.get("endSec"))]
    return ToolResult(scenes[:150])


@tool("read_transcript", "Transcript sentences (with speaker labels) of a time range. Use for speech-led edits; "
      "cut on sentence boundaries.", {"assetId": {"type": "string"}, "startSec": {"type": "number"},
                                      "endSec": {"type": "number"}}, ["assetId"])
def read_transcript(ctx: ToolContext, args: dict) -> ToolResult:
    a = _asset(ctx, args["assetId"])
    sents = [s for s in _transcript(a)["sentences"] if _in(s, args.get("startSec"), args.get("endSec"), "s", "e")]
    return ToolResult([{"s": s["s"], "e": s["e"], "spk": s.get("spk"), "text": s["text"]} for s in sents[:400]])


@tool("look", "See frames of a video range (contact sheet, row-major) or a document page / image. Use it to judge "
      "composition, on-screen slides/boards, or whether a moment is visually strong.",
      {"assetId": {"type": "string"}, "startSec": {"type": "number"}, "endSec": {"type": "number"},
       "frames": {"type": "integer", "minimum": 1, "maximum": 16}, "page": {"type": "integer"}}, ["assetId"])
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
    times = frames.spread(start, end, int(args.get("frames", 8)))
    sheet = frames.contact_sheet(_proxy_url(a), times, cols=4)
    return ToolResult({"assetId": a["assetId"], "frameTimes": times, "layout": "4 columns, row-major"}, [sheet])


@tool("ask_video", "Ask the video-understanding model (Pegasus) a question about a video or a time range of it. "
      "Use for details analysis does not cover (who scores, what is written on the board, exact moment of X).",
      {"assetId": {"type": "string"}, "question": {"type": "string"}, "startSec": {"type": "number"},
       "endSec": {"type": "number"}}, ["assetId", "question"])
def ask_video(ctx: ToolContext, args: dict) -> ToolResult:
    a = _asset(ctx, args["assetId"])
    chunks = _manifest(a).get("chunks", [])
    start, end = args.get("startSec"), args.get("endSec")
    chunk = next((c for c in chunks if start is None or c["startSec"] <= float(start) < c["endSec"]), chunks[0])
    off = chunk["startSec"]
    answer = models.pegasus_ask(storage.s3_uri(chunk["key"]),
                                args["question"] + " " + models.language_instruction(
                                    ingest.LANGUAGES.get(_lang(a), "English")) +
                                " Give timestamps in seconds from the start of this clip.",
                                None if start is None else max(0.0, float(start) - off),
                                None if end is None else min(chunk["endSec"], float(end)) - off)
    return ToolResult({"answer": answer, "timestampOffsetSec": off,
                       "note": "Add timestampOffsetSec to timestamps in the answer to get source time."})


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


@tool("analyze_framing",
      "Decide how a 16:9 (or any) source should fill the target aspect for a time range: per-shot subject focus "
      "point and a layout (crop = follow the subject, split = speaker + slide/board stacked, fit_blur = whole frame on "
      "blurred background, letterbox). Returns a reframe value ready for edit_timeline `update`.",
      {"assetId": {"type": "string"}, "startSec": {"type": "number"}, "endSec": {"type": "number"},
       "aspect": {"type": "string", "enum": ["9:16", "1:1", "4:5", "16:9"]},
       "intent": {"type": "string", "description": "What must stay visible, e.g. 'follow the ball', "
                                                    "'keep lecturer and the board'"}},
      ["assetId", "startSec", "endSec", "aspect"])
def analyze_framing(ctx: ToolContext, args: dict) -> ToolResult:
    a = _asset(ctx, args["assetId"])
    start, end = float(args["startSec"]), float(args["endSec"])
    shots = [s for s in _shots(a) if start <= s < end]
    bounds = sorted({start, *shots, end})
    segs = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1) if bounds[i + 1] - bounds[i] > 0.3][:12]
    times = [round((s + e) / 2, 3) for s, e in segs] or [round((start + end) / 2, 3)]
    imgs = frames.frames(_proxy_url(a), times, 480)
    plan = models.framing_plan(imgs, times, args["aspect"], args.get("intent", ""), a.get("width"), a.get("height"))
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
@tool("get_timeline", "Current timeline (compact): tracks, items with ids, timings, provenance, user pins.", {})
def get_timeline(ctx: ToolContext, _args: dict) -> ToolResult:
    project, _ = projects.require_project(ctx.principal, ctx.project_id, "view")
    return ToolResult({"version": project["currentVersion"], **timeline_ops.summarize(projects.load_timeline(project))})


OPS_DOC = (
    "Ops (applied in order, each atomically; rejected ops are reported, others still apply):\n"
    "- add_clip {assetId, srcIn, srcOut, track?='v1', index?, start? (non-main tracks), duration? (images), page? "
    "(document page), reason, analysisRef?}\n"
    "- remove {itemId} | trim {itemId, srcIn?, srcOut?, duration?} | split {itemId, at (timeline sec)} | "
    "move {itemId, index? (main track), track?, start?}\n"
    "- update {itemId, set:{transform|reframe|kenBurns|volume|fadeIn|fadeOut|effects|keyframes|opacity|"
    "transitionIn|transitionOut|speed|text|style|position|animation|...}}\n"
    "- add_text {text, start, duration, role(title|lowerThird|callout|cta|step), position?, style?, animation?}\n"
    "- captions {preset(clean|bold|karaoke|pop|boxed|minimal)} — word-timed captions from transcripts\n"
    "- settings {set:{aspect|fps|background|loudness|safeZone|recipe}} | meta {set:{title|description|hashtags|"
    "thumbnailAt}} | marker {t, label, kind} | clear_track {track}\n"
    "Track v1 is the magnetic main track (no gaps). o1 = B-roll overlay, t1 text, c1 captions, a1 music, a2 voiceover. "
    "Pinned paths (user edits) cannot be changed — work around them.")


@tool("edit_timeline", "Apply edit operations to the project timeline and save a new version. " + OPS_DOC,
      {"ops": {"type": "array", "items": {"type": "object"}}, "message": {"type": "string"}}, ["ops"])
def edit_timeline(ctx: ToolContext, args: dict) -> ToolResult:
    project, _ = projects.require_project(ctx.principal, ctx.project_id, "edit")
    if not projects.acquire_lock(project, ctx.holder):
        raise ValueError("The user is editing the timeline right now; ask them or try again later")
    project = ddb.get(f"PROJ#{ctx.project_id}", "META", consistent=True)
    tl = projects.load_timeline(project)
    ops = list(args.get("ops") or [])[:200]
    ids = {o["assetId"] for o in ops if o.get("assetId")} | {
        i["assetId"] for t in tl["tracks"] for i in t["items"] if i.get("assetId")} | {
        o["kit"][k] for o in ops if o.get("op") == "brand" and isinstance(o.get("kit"), dict)
        for k in ("logoAssetId", "introAssetId", "outroAssetId") if o["kit"].get(k)}
    accessible = acl.accessible_asset_ids(ctx.principal)
    assets = {a["assetId"]: a for a in ddb.batch_get((f"ASSET#{i}", "META") for i in ids if i in accessible)
              if a.get("status") == "ready"}
    for o in ops:
        if o.get("op") == "captions" and "transcripts" not in o:
            main = timeline_ops.main_track(tl)
            o["transcripts"] = {aid: _transcript(assets[aid]) for aid in {i.get("assetId") for i in main["items"]}
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
