"""Authoritative timeline operations (agent tools and UI share these).

The main video track (kind=video, first) is *magnetic*: items are laid out back-to-back with no gaps,
so inserts/removes/trims ripple automatically. Other tracks are free-positioned.

Pins: `timeline.pins` lists paths like "items/<id>" (whole item) or "items/<id>/srcIn" (one property)
or "settings/aspect". Agent ops that would modify a pinned path are rejected per-op and reported,
so the user's manual edits are never overwritten (L2+ principle).
"""
from __future__ import annotations

import copy
import uuid
from typing import Any

MIN_DURATION = 0.1
DEFAULT_IMAGE_SEC = 3.0
ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350), "4:3": (1440, 1080)}

CAPTION_PRESETS = {
    "clean": {"font": "Pretendard", "size": 0.042, "weight": 700, "color": "#FFFFFF",
              "stroke": {"color": "#000000", "width": 0.004}},
    "bold": {"font": "Pretendard", "size": 0.055, "weight": 900, "color": "#FFFFFF", "uppercase": True,
             "stroke": {"color": "#000000", "width": 0.006}, "highlightColor": "#FFD400"},
    "karaoke": {"font": "Pretendard", "size": 0.05, "weight": 800, "color": "#FFFFFF", "highlightColor": "#22D3EE",
                "stroke": {"color": "#000000", "width": 0.005}},
    "pop": {"font": "Pretendard", "size": 0.06, "weight": 900, "color": "#FFFFFF", "highlightColor": "#FF3B6B",
            "shadow": {"color": "#000000", "blur": 8, "x": 0, "y": 2}},
    "boxed": {"font": "Pretendard", "size": 0.04, "weight": 600, "color": "#FFFFFF", "background": "rgba(0,0,0,0.65)"},
    "minimal": {"font": "Pretendard", "size": 0.035, "weight": 500, "color": "#FFFFFF"},
}


class OpError(ValueError):
    pass


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


# ------------------------------------------------------------------ lookup helpers
def track(tl: dict, track_id: str) -> dict:
    for t in tl["tracks"]:
        if t["id"] == track_id:
            return t
    raise OpError(f"Unknown track {track_id}")


def main_track(tl: dict) -> dict:
    return next(t for t in tl["tracks"] if t["kind"] == "video")


def find_item(tl: dict, item_id: str) -> tuple[dict, dict, int]:
    for t in tl["tracks"]:
        for i, it in enumerate(t["items"]):
            if it["id"] == item_id:
                return t, it, i
    raise OpError(f"Unknown item {item_id}")


def is_pinned(tl: dict, path: str) -> bool:
    pins = tl.get("pins", [])
    parts = path.split("/")
    return any(path == p or "/".join(parts[:2]) == p for p in pins)


def _guard(tl: dict, path: str, actor: str) -> None:
    if actor == "agent" and is_pinned(tl, path):
        raise OpError(f"'{path}' is pinned by the user")


def clip_duration(it: dict) -> float:
    if it.get("type") == "clip" and it.get("mediaKind") in {"video", "audio"}:
        return max(MIN_DURATION, (it["srcOut"] - it["srcIn"]) / float(it.get("speed") or 1))
    return max(MIN_DURATION, float(it.get("duration", DEFAULT_IMAGE_SEC)))


def relayout(tl: dict) -> None:
    """Magnetic main track: recompute starts/durations back-to-back."""
    t = 0.0
    for it in main_track(tl)["items"]:
        it["duration"] = round(clip_duration(it), 4)
        it["start"] = round(t, 4)
        t += it["duration"]
    for tr in tl["tracks"]:
        if tr["kind"] != "video":
            for it in tr["items"]:
                if it.get("type") == "clip" and it.get("mediaKind") in {"video", "audio"}:
                    it["duration"] = round(clip_duration(it), 4)
            tr["items"].sort(key=lambda x: x["start"])


def duration(tl: dict) -> float:
    return max([it["start"] + it["duration"] for tr in tl["tracks"] for it in tr["items"]] or [0.0])


# ------------------------------------------------------------------ ops
def op_add_clip(tl: dict, op: dict, actor: str, assets: dict) -> dict:
    asset = assets.get(op["assetId"])
    if not asset:
        raise OpError(f"Asset {op['assetId']} not accessible")
    kind = asset.get("kind")
    if kind not in {"video", "image", "audio", "document"}:
        raise OpError(f"Asset kind {kind} cannot be placed on the timeline")
    media_kind = "image" if kind in {"image", "document"} else kind
    tr = track(tl, op.get("track") or ("a1" if media_kind == "audio" else "v1"))
    if tr["kind"] == "audio" and media_kind != "audio" and kind != "video":
        raise OpError("Only audio (or a video's sound) can go on an audio track")
    dur_src = float(asset.get("durationSec") or 0)
    item: dict[str, Any] = {"id": new_id("i"), "type": "clip", "assetId": op["assetId"], "mediaKind": media_kind,
                            "start": 0.0, "duration": DEFAULT_IMAGE_SEC}
    if media_kind in {"video", "audio"} or (tr["kind"] == "audio" and kind == "video"):
        if tr["kind"] == "audio":
            item["mediaKind"] = "audio"
        src_in = max(0.0, float(op.get("srcIn", 0)))
        src_out = float(op.get("srcOut", dur_src or src_in + 5))
        if dur_src:
            src_out = min(src_out, dur_src)
        if src_out - src_in < MIN_DURATION:
            raise OpError("Clip range too short")
        item.update(srcIn=round(src_in, 3), srcOut=round(src_out, 3), speed=float(op.get("speed", 1)))
    else:
        item.update(srcIn=0, srcOut=0, duration=float(op.get("duration", DEFAULT_IMAGE_SEC)))
        if kind == "document":
            item["page"] = int(op.get("page", 0))
        item["kenBurns"] = op.get("kenBurns") or {"from": {"x": 0.5, "y": 0.5, "scale": 1.0},
                                                   "to": {"x": 0.5, "y": 0.5, "scale": 1.08}}
    for k in ("transform", "volume", "fadeIn", "fadeOut", "transitionIn", "transitionOut", "effects", "reframe",
              "opacity"):
        if k in op:
            item[k] = op[k]
    if tl["settings"]["aspect"] == "9:16" and media_kind == "video" and (asset.get("width", 0) > asset.get("height", 0)):
        item.setdefault("reframe", {"mode": "auto"})
        item.setdefault("transform", {"x": 0.5, "y": 0.5, "scale": 1, "rotation": 0, "fit": "cover"})
    item["provenance"] = {"by": actor, **({"reason": op["reason"]} if op.get("reason") else {}),
                          **({"analysisRef": op["analysisRef"]} if op.get("analysisRef") else {})}
    if tr["kind"] == "video":
        at = op.get("index")
        tr["items"].insert(len(tr["items"]) if at is None else int(at), item)
    else:
        item["start"] = round(float(op.get("start", 0)), 4)
        tr["items"].append(item)
    return {"itemId": item["id"]}


def op_remove(tl: dict, op: dict, actor: str, _assets: dict) -> dict:
    _guard(tl, f"items/{op['itemId']}", actor)
    tr, _, idx = find_item(tl, op["itemId"])
    tr["items"].pop(idx)
    tl["pins"] = [p for p in tl.get("pins", []) if not p.startswith(f"items/{op['itemId']}")]
    return {}


def op_trim(tl: dict, op: dict, actor: str, assets: dict) -> dict:
    tr, it, _ = find_item(tl, op["itemId"])
    if it.get("type") != "clip":
        if "duration" in op:
            _guard(tl, f"items/{it['id']}/duration", actor)
            dur = max(MIN_DURATION, float(op["duration"]))
            retime_caption(it, float(it["start"]), dur)
            it["duration"] = dur
        return {}
    for key in ("srcIn", "srcOut"):
        if key in op:
            _guard(tl, f"items/{it['id']}/{key}", actor)
    if it["mediaKind"] == "image":
        if "duration" in op:
            it["duration"] = max(MIN_DURATION, float(op["duration"]))
        return {}
    src_dur = float((assets.get(it["assetId"]) or {}).get("durationSec") or 1e9)
    src_in = max(0.0, float(op.get("srcIn", it["srcIn"])))
    src_out = min(src_dur, float(op.get("srcOut", it["srcOut"])))
    if src_out - src_in < MIN_DURATION:
        raise OpError("Trim would make the clip too short")
    it["srcIn"], it["srcOut"] = round(src_in, 3), round(src_out, 3)
    return {}


def op_split(tl: dict, op: dict, actor: str, _assets: dict) -> dict:
    _guard(tl, f"items/{op['itemId']}", actor)
    tr, it, idx = find_item(tl, op["itemId"])
    at = float(op["at"])  # timeline time
    rel = at - it["start"]
    if rel <= MIN_DURATION or rel >= it["duration"] - MIN_DURATION:
        raise OpError("Split point must be inside the item")
    second = copy.deepcopy(it)
    second["id"] = new_id("i")
    if it.get("type") == "clip" and it["mediaKind"] in {"video", "audio"}:
        cut = it["srcIn"] + rel * float(it.get("speed") or 1)
        it["srcOut"], second["srcIn"] = round(cut, 3), round(cut, 3)
    else:
        it["duration"], second["duration"] = rel, it["duration"] - rel
        second["start"] = at
    second.pop("transitionIn", None)
    it.pop("transitionOut", None)
    tr["items"].insert(idx + 1, second)
    return {"itemIds": [it["id"], second["id"]]}


def op_move(tl: dict, op: dict, actor: str, _assets: dict) -> dict:
    _guard(tl, f"items/{op['itemId']}/order", actor)
    tr, it, idx = find_item(tl, op["itemId"])
    dest = track(tl, op.get("track", tr["id"]))
    tr["items"].pop(idx)
    if dest["kind"] == "video":
        dest["items"].insert(min(int(op.get("index", len(dest["items"]))), len(dest["items"])), it)
    else:
        new_start = round(float(op.get("start", it["start"])), 4)
        retime_caption(it, new_start, float(it["duration"]))
        it["start"] = new_start
        dest["items"].append(it)
    return {}


UPDATABLE = {"transform", "reframe", "kenBurns", "volume", "fadeIn", "fadeOut", "effects", "keyframes", "opacity",
             "transitionIn", "transitionOut", "speed", "speedCurve", "mode", "text", "style", "position", "animation",
             "role", "preset", "start", "duration", "page", "fill", "box", "words"}


OBJECT_PRCORNERS = {"tl": (0.13, 0.08), "tr": (0.87, 0.08), "bl": (0.13, 0.92), "br": (0.87, 0.92)}


def op_brand(tl: dict, op: dict, actor: str, assets: dict) -> dict:
    """Apply a brand kit: logo PIP overlay over the whole video, intro/outro clips, caption/text styling.
    Re-applying replaces the previous brand elements (tagged provenance.reason == 'brand')."""
    kit = op.get("kit") or {}
    tl["settings"]["brandKitId"] = kit.get("brandKitId")
    for tr in tl["tracks"]:
        tr["items"] = [i for i in tr["items"] if (i.get("provenance") or {}).get("reason") != "brand"
                       or is_pinned(tl, f"items/{i['id']}")]
    relayout(tl)
    main = main_track(tl)
    added = []
    for key, at_end in (("introAssetId", False), ("outroAssetId", True)):
        aid = kit.get(key)
        if aid and aid in assets:
            a = assets[aid]
            dur = float(a.get("durationSec") or 2.5)
            r = op_add_clip(tl, {"assetId": aid, "srcIn": 0, "srcOut": min(dur, 6), "duration": 2.5,
                                 "index": len(main["items"]) if at_end else 0, "reason": "brand"}, actor, assets)
            added.append(r["itemId"])
    relayout(tl)
    total = duration(tl)
    logo = kit.get("logoAssetId")
    if logo and logo in assets and total > 0:
        x, y = CORNERS.get(kit.get("logoCorner", "tr"), CORNERS["tr"])
        item = {"id": new_id("i"), "type": "clip", "assetId": logo, "mediaKind": "image", "srcIn": 0, "srcOut": 0,
                "start": 0.0, "duration": round(total, 3), "opacity": float(kit.get("logoOpacity", 0.9)),
                "transform": {"x": x, "y": y, "scale": float(kit.get("logoScale", 0.18)), "rotation": 0, "fit": "contain"},
                "provenance": {"by": actor, "reason": "brand"}}
        track(tl, "o1")["items"].append(item)
        added.append(item["id"])
    style = {k: v for k, v in {"font": kit.get("font"), "color": kit.get("textColor"),
                               "highlightColor": kit.get("accentColor")}.items() if v}
    for tr in tl["tracks"]:
        for it in tr["items"]:
            if it.get("type") in {"text", "caption"} and style:
                if it["type"] == "caption" and kit.get("captionPreset"):
                    it["preset"] = kit["captionPreset"]
                if not is_pinned(tl, f"items/{it['id']}/style"):
                    it["style"] = {**(it.get("style") if isinstance(it.get("style"), dict) else {}), **style}
    relayout(tl)
    return {"added": added}


OPS = {"transform", "reframe", "kenBurns", "style", "position", "animation", "keyframes", "transitionIn",
                "transitionOut", "box"}


OBJECT_PROPS = {"transform", "reframe", "kenBurns", "style", "position", "animation", "keyframes", "transitionIn",
                "transitionOut", "box"}


def retime_caption(it: dict, start: float, duration: float) -> None:
    """Caption words hold timeline times: map them from the old span onto the new one (move = shift, stretch = scale)."""
    if it.get("type") != "caption":
        return
    old_s, old_d = float(it["start"]), max(1e-6, float(it["duration"]))
    k = duration / old_d
    it["words"] = [{**w, "s": round(start + (w["s"] - old_s) * k, 3), "e": round(start + (w["e"] - old_s) * k, 3)}
                   for w in it.get("words", [])]


def op_update(tl: dict, op: dict, actor: str, _assets: dict) -> dict:
    _, it, _ = find_item(tl, op["itemId"])
    changed = []
    st = op.get("set") or {}
    if it.get("type") == "caption" and ("start" in st or "duration" in st) and "words" not in st:
        retime_caption(it, float(st.get("start", it["start"])), max(MIN_DURATION, float(st.get("duration", it["duration"]))))
    for k, v in (op.get("set") or {}).items():
        if k not in UPDATABLE:
            raise OpError(f"Property {k} is not updatable")
        if k in OBJECT_PROPS and v is not None and not isinstance(v, dict):
            raise OpError(f"{k} must be an object (e.g. style: {{size: 0.05, color: '#FFFFFF'}})")
        if k == "style" and isinstance(v, dict) and it.get("type") in {"text", "caption"}:
            v = {**(it.get("style") if isinstance(it.get("style"), dict) else {}), **v}
        _guard(tl, f"items/{it['id']}/{k}", actor)
        if v is None:
            it.pop(k, None)
        else:
            it[k] = v
        changed.append(k)
    return {"changed": changed}


DEFAULT_TEXT_STYLE = {"font": "Pretendard", "size": 0.05, "weight": 800, "color": "#FFFFFF",
                      "stroke": {"color": "#000000", "width": 0.005}, "align": "center"}
DEFAULT_TEXT_POSITION = {"x": 0.5, "y": 0.2, "anchor": "center", "maxWidth": 0.86}


def op_add_text(tl: dict, op: dict, actor: str, _assets: dict) -> dict:
    tr = track(tl, op.get("track", "t1"))
    if op.get("style") is not None and not isinstance(op["style"], dict):
        raise OpError("style must be an object, e.g. {size: 0.06, color: '#FFFFFF', weight: 900}")
    style = {**DEFAULT_TEXT_STYLE, **(op.get("style") or {})}
    if not isinstance(style.get("size"), (int, float)) or not 0.01 <= style["size"] <= 0.3:
        style["size"] = DEFAULT_TEXT_STYLE["size"]  # size is a fraction of canvas height
    item = {"id": new_id("x"), "type": "text", "text": str(op["text"])[:500], "role": op.get("role", "free"),
            "start": round(float(op.get("start", 0)), 4), "duration": max(MIN_DURATION, float(op.get("duration", 3))),
            "style": style,
            "position": {**DEFAULT_TEXT_POSITION, **(op.get("position") or {})},
            "animation": op.get("animation") or {"in": "pop", "out": "fade"},
            "provenance": {"by": actor, **({"reason": op["reason"]} if op.get("reason") else {})}}
    tr["items"].append(item)
    return {"itemId": item["id"]}


def op_captions(tl: dict, op: dict, actor: str, assets: dict) -> dict:
    """Build word-timed captions for the main track from asset transcripts (op['transcripts'][assetId])."""
    tr = track(tl, op.get("track", "c1"))
    pinned = [it for it in tr["items"] if is_pinned(tl, f"items/{it['id']}")]
    tr["items"] = pinned if actor == "agent" else []
    preset = op.get("preset", "clean")
    style = {**CAPTION_PRESETS.get(preset, CAPTION_PRESETS["clean"]), **(op.get("style") or {})}
    transcripts: dict = op.get("transcripts") or {}
    max_words = int(op.get("maxWordsPerLine", 4 if tl["settings"]["aspect"] == "9:16" else 8))
    words_out: list[dict] = []
    for it in main_track(tl)["items"]:
        if it.get("type") != "clip" or it.get("mediaKind") != "video":
            continue
        speed = float(it.get("speed") or 1)
        for w in (transcripts.get(it["assetId"]) or {}).get("words", []):
            if w["s"] >= it["srcIn"] and w["e"] <= it["srcOut"]:
                s = it["start"] + (w["s"] - it["srcIn"]) / speed
                e = it["start"] + (w["e"] - it["srcIn"]) / speed
                words_out.append({"s": round(s, 3), "e": round(e, 3), "w": w["w"]})
    groups: list[list[dict]] = []
    for w in words_out:
        if not groups or len(groups[-1]) >= max_words or w["s"] - groups[-1][-1]["e"] > 0.6 \
                or groups[-1][-1]["w"].endswith((".", "?", "!", "。")):
            groups.append([])
        groups[-1].append(w)
    for g in groups:
        tr["items"].append({"id": new_id("c"), "type": "caption", "start": g[0]["s"],
                            "duration": max(MIN_DURATION, round(g[-1]["e"] - g[0]["s"], 3)), "words": g,
                            "preset": preset, "style": {**style, "align": "center", "maxLines": 2},
                            "position": {"y": float(op.get("y", 0.72 if tl["settings"]["aspect"] == "9:16" else 0.86))},
                            "provenance": {"by": actor}})
    return {"captions": len(groups)}


def op_settings(tl: dict, op: dict, actor: str, _assets: dict) -> dict:
    s = op.get("set") or {}
    for k in s:
        _guard(tl, f"settings/{k}", actor)
    if "aspect" in s:
        if s["aspect"] not in ASPECTS:
            raise OpError(f"aspect must be one of {sorted(ASPECTS)}")
        tl["settings"]["width"], tl["settings"]["height"] = ASPECTS[s["aspect"]]
        tl["settings"]["safeZone"] = "shorts" if s["aspect"] == "9:16" else "none"
    for k in ("aspect", "fps", "background", "loudness", "safeZone", "recipe", "brandKitId"):
        if k in s:
            tl["settings"][k] = s[k]
    return {}


def op_meta(tl: dict, op: dict, actor: str, _assets: dict) -> dict:
    for k, v in (op.get("set") or {}).items():
        if k in {"title", "description", "hashtags", "thumbnailAt", "thumbnailAssetId"}:
            _guard(tl, f"meta/{k}", actor)
            tl["meta"][k] = v
    return {}


def op_marker(tl: dict, op: dict, actor: str, _assets: dict) -> dict:
    m = {"id": new_id("m"), "t": float(op["t"]), "label": str(op.get("label", ""))[:120], "kind": op.get("kind", "note")}
    tl["markers"].append(m)
    tl["markers"].sort(key=lambda x: x["t"])
    return {"markerId": m["id"]}


def op_clear_track(tl: dict, op: dict, actor: str, _assets: dict) -> dict:
    tr = track(tl, op["track"])
    keep = [it for it in tr["items"] if actor == "agent" and is_pinned(tl, f"items/{it['id']}")]
    removed = len(tr["items"]) - len(keep)
    tr["items"] = keep
    return {"removed": removed, "keptPinned": len(keep)}


def op_pin(tl: dict, op: dict, actor: str, _assets: dict) -> dict:
    if actor == "agent":
        raise OpError("Only the user can pin/unpin")
    path = op["path"]
    pins = set(tl.get("pins", []))
    (pins.add if op.get("pinned", True) else pins.discard)(path)
    tl["pins"] = sorted(pins)
    return {}


CORNERS = {"tl": (0.13, 0.08), "tr": (0.87, 0.08), "bl": (0.13, 0.92), "br": (0.87, 0.92)}


def op_brand(tl: dict, op: dict, actor: str, assets: dict) -> dict:
    """Apply a brand kit: logo PIP overlay over the whole video, intro/outro clips, caption/text styling.
    Re-applying replaces the previous brand elements (tagged provenance.reason == 'brand')."""
    kit = op.get("kit") or {}
    tl["settings"]["brandKitId"] = kit.get("brandKitId")
    for tr in tl["tracks"]:
        tr["items"] = [i for i in tr["items"] if (i.get("provenance") or {}).get("reason") != "brand"
                       or is_pinned(tl, f"items/{i['id']}")]
    relayout(tl)
    main = main_track(tl)
    added = []
    for key, at_end in (("introAssetId", False), ("outroAssetId", True)):
        aid = kit.get(key)
        if aid and aid in assets:
            a = assets[aid]
            dur = float(a.get("durationSec") or 2.5)
            r = op_add_clip(tl, {"assetId": aid, "srcIn": 0, "srcOut": min(dur, 6), "duration": 2.5,
                                 "index": len(main["items"]) if at_end else 0, "reason": "brand"}, actor, assets)
            added.append(r["itemId"])
    relayout(tl)
    total = duration(tl)
    logo = kit.get("logoAssetId")
    if logo and logo in assets and total > 0:
        x, y = CORNERS.get(kit.get("logoCorner", "tr"), CORNERS["tr"])
        item = {"id": new_id("i"), "type": "clip", "assetId": logo, "mediaKind": "image", "srcIn": 0, "srcOut": 0,
                "start": 0.0, "duration": round(total, 3), "opacity": float(kit.get("logoOpacity", 0.9)),
                "transform": {"x": x, "y": y, "scale": float(kit.get("logoScale", 0.18)), "rotation": 0, "fit": "contain"},
                "provenance": {"by": actor, "reason": "brand"}}
        track(tl, "o1")["items"].append(item)
        added.append(item["id"])
    style = {k: v for k, v in {"font": kit.get("font"), "color": kit.get("textColor"),
                               "highlightColor": kit.get("accentColor")}.items() if v}
    for tr in tl["tracks"]:
        for it in tr["items"]:
            if it.get("type") in {"text", "caption"} and style:
                if it["type"] == "caption" and kit.get("captionPreset"):
                    it["preset"] = kit["captionPreset"]
                if not is_pinned(tl, f"items/{it['id']}/style"):
                    it["style"] = {**(it.get("style") if isinstance(it.get("style"), dict) else {}), **style}
    relayout(tl)
    return {"added": added}


OPS = {
    "add_clip": op_add_clip, "remove": op_remove, "trim": op_trim, "split": op_split, "move": op_move,
    "update": op_update, "add_text": op_add_text, "captions": op_captions, "settings": op_settings,
    "meta": op_meta, "marker": op_marker, "clear_track": op_clear_track, "pin": op_pin, "brand": op_brand,
}


def pins_for(op: dict, out: dict) -> list[str]:
    """Paths a *user* op should pin so the agent will not override the manual edit."""
    name, iid = op.get("op"), op.get("itemId")
    if name in {"add_clip", "add_text"} and out.get("itemId"):
        return [f"items/{out['itemId']}"]
    if name == "trim":
        return [f"items/{iid}/{k}" for k in ("srcIn", "srcOut", "duration") if k in op]
    if name == "split" and out.get("itemIds"):
        a, b = out["itemIds"]
        return [f"items/{a}/srcOut", f"items/{b}/srcIn"]
    if name == "move":
        return [f"items/{iid}/order"]
    if name == "update":
        return [f"items/{iid}/{k}" for k in (op.get("set") or {})]
    if name in {"settings", "meta"}:
        return [f"{name}/{k}" for k in (op.get("set") or {})]
    return []


def apply(timeline: dict, ops: list[dict], actor: str, assets: dict,
          auto_pin: bool = True) -> tuple[dict, list[dict]]:
    """Apply ops to a copy. Each op succeeds or is reported as rejected; never partially applied.
    User ops auto-pin the paths they touch (unless auto_pin=False)."""
    tl = copy.deepcopy(timeline)
    results = []
    for i, op in enumerate(ops):
        fn = OPS.get(op.get("op", ""))
        if not fn:
            results.append({"index": i, "op": op.get("op"), "ok": False, "error": f"Unknown op; use {sorted(OPS)}"})
            continue
        snapshot = copy.deepcopy(tl)
        try:
            out = fn(tl, op, actor, assets)
            if actor == "user" and auto_pin and op.get("op") != "pin":
                tl["pins"] = sorted(set(tl.get("pins", [])) | set(pins_for(op, out)))
            relayout(tl)
            results.append({"index": i, "op": op["op"], "ok": True, **out})
        except (OpError, KeyError, TypeError, ValueError) as exc:
            tl = snapshot
            results.append({"index": i, "op": op.get("op"), "ok": False, "error": str(exc)})
    return tl, results


def summarize(tl: dict) -> dict:
    """Compact, token-friendly view of a timeline for the agent."""
    out = {"settings": tl["settings"], "durationSec": round(duration(tl), 2), "meta": tl.get("meta", {}),
           "pins": tl.get("pins", []), "markers": tl.get("markers", []), "tracks": []}
    for tr in tl["tracks"]:
        items = []
        for it in tr["items"]:
            s = {"id": it["id"], "type": it["type"], "start": it["start"], "dur": it["duration"]}
            if it["type"] == "clip":
                s.update(asset=it["assetId"], kind=it["mediaKind"], srcIn=it.get("srcIn"), srcOut=it.get("srcOut"))
                if it.get("speed", 1) != 1:
                    s["speed"] = it["speed"]
            elif it["type"] == "text":
                s["text"] = it["text"][:80]
            elif it["type"] == "caption":
                s["text"] = " ".join(w["w"] for w in it["words"])[:80]
            if it.get("provenance", {}).get("reason"):
                s["why"] = it["provenance"]["reason"][:120]
            items.append(s)
        out["tracks"].append({"id": tr["id"], "kind": tr["kind"], "name": tr["name"], "items": items})
    return out
