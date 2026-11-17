"""Project export for desktop NLEs (L5): OpenTimelineIO JSON, FCPXML 1.10 (Final Cut / DaVinci / Premiere import),
CMX3600 EDL (main track). Media references point at the original file names so editors can relink to the
downloaded originals.
"""
from __future__ import annotations

import json


def escape(text: str) -> str:
    """XML text escaping (we only generate XML; nothing is parsed)."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def quoteattr(text: str) -> str:
    return '"' + escape(text).replace('"', "&quot;").replace("\n", "&#10;") + '"'


def _media_names(assets: dict) -> dict:
    return {aid: (a.get("filename") or f"{aid}.mp4") for aid, a in assets.items()}


def _rt(sec: float, fps: int) -> dict:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": float(fps), "value": round(sec * fps)}


def _range(start: float, dur: float, fps: int) -> dict:
    return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _rt(start, fps), "duration": _rt(dur, fps)}


def to_otio(tl: dict, assets: dict, title: str) -> str:
    fps = int(tl["settings"].get("fps", 30))
    names = _media_names(assets)
    tracks = []
    for tr in tl["tracks"]:
        if tr["kind"] not in {"video", "overlay", "audio"} or not tr["items"]:
            continue
        kind = "Audio" if tr["kind"] == "audio" else "Video"
        children, cursor = [], 0.0
        for it in sorted(tr["items"], key=lambda x: x["start"]):
            if it.get("type") != "clip":
                continue
            if it["start"] > cursor + 1e-3:
                children.append({"OTIO_SCHEMA": "Gap.1", "name": "", "source_range": _range(0, it["start"] - cursor, fps)})
            src_dur = (it["srcOut"] - it["srcIn"]) if it.get("mediaKind") != "image" else it["duration"]
            a = assets.get(it["assetId"], {})
            children.append({
                "OTIO_SCHEMA": "Clip.2", "name": names.get(it["assetId"], it["assetId"]),
                "source_range": _range(it.get("srcIn", 0) if it.get("mediaKind") != "image" else 0, src_dur, fps),
                "media_references": {"DEFAULT_MEDIA": {
                    "OTIO_SCHEMA": "ExternalReference.1", "target_url": names.get(it["assetId"]),
                    "available_range": _range(0, float(a.get("durationSec") or src_dur), fps)}},
                "active_media_reference_key": "DEFAULT_MEDIA",
                "effects": ([{"OTIO_SCHEMA": "LinearTimeWarp.1", "name": "speed", "effect_name": "LinearTimeWarp",
                              "time_scalar": float(it["speed"])}] if it.get("speed", 1) != 1 else []),
                "metadata": {"shorts": {k: it.get(k) for k in ("reframe", "volume", "kenBurns", "provenance") if it.get(k)}},
            })
            cursor = it["start"] + it["duration"]
        tracks.append({"OTIO_SCHEMA": "Track.1", "name": tr["name"], "kind": kind, "children": children})
    markers = [{"OTIO_SCHEMA": "Marker.2", "name": m["label"], "marked_range": _range(m["t"], 0, fps), "color": "RED"}
               for m in tl.get("markers", [])]
    doc = {"OTIO_SCHEMA": "Timeline.1", "name": title, "global_start_time": None,
           "metadata": {"shorts": {"settings": tl["settings"], "meta": tl.get("meta", {})}},
           "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "children": tracks, "markers": markers}}
    return json.dumps(doc, ensure_ascii=False, indent=1)


def to_fcpxml(tl: dict, assets: dict, title: str) -> str:
    s = tl["settings"]
    fps = int(s.get("fps", 30))
    t = lambda sec: f"{round(sec * fps)}/{fps}s"  # noqa: E731
    names = _media_names(assets)
    res = [f'<format id="r0" name="FFVideoFormat{s["height"]}p{fps}" frameDuration="1/{fps}s" width="{s["width"]}" height="{s["height"]}"/>']
    ids = {}
    for n, (aid, a) in enumerate(assets.items(), 1):
        ids[aid] = f"r{n}"
        has_v = a.get("kind") in {"video", "image", "document"}
        res.append(f'<asset id="r{n}" name={quoteattr(names[aid])} start="0s" duration="{t(float(a.get("durationSec") or 10))}" '
                   f'hasVideo="{int(has_v)}" hasAudio="{int(a.get("kind") in {"video", "audio"})}" format="r0">'
                   f'<media-rep kind="original-media" src={quoteattr("file:./" + names[aid])}/></asset>')
    main = next(tr for tr in tl["tracks"] if tr["kind"] == "video")
    spine = []
    for it in main["items"]:
        if it.get("type") != "clip":
            continue
        dur = it["duration"]
        attached = []
        for tr in tl["tracks"]:
            if tr["kind"] in {"overlay", "audio"}:
                lane = 1 if tr["kind"] == "overlay" else -1
                for o in tr["items"]:
                    if o.get("type") == "clip" and it["start"] <= o["start"] < it["start"] + dur:
                        attached.append(f'<asset-clip ref="{ids.get(o["assetId"], "r1")}" lane="{lane}" '
                                        f'offset="{t(o.get("srcIn", 0) + o["start"] - it["start"] + it.get("srcIn", 0))}" '
                                        f'start="{t(o.get("srcIn", 0))}" duration="{t(o["duration"])}" name={quoteattr(names.get(o["assetId"], ""))}/>')
            if tr["kind"] in {"text", "caption"}:
                for x in tr["items"]:
                    if it["start"] <= x["start"] < it["start"] + dur:
                        text = x.get("text") or " ".join(w["w"] for w in x.get("words", []))
                        attached.append(f'<marker start="{t(it.get("srcIn", 0) + x["start"] - it["start"])}" duration="{t(x["duration"])}" value={quoteattr(text[:200])}/>')
        speed = ""
        if it.get("speed", 1) != 1:
            speed = f'<timeMap><timept time="0s" value="0s" interp="linear"/><timept time="{t(dur)}" value="{t(dur * it["speed"])}" interp="linear"/></timeMap>'
        spine.append(f'<asset-clip ref="{ids[it["assetId"]]}" offset="{t(it["start"])}" start="{t(it.get("srcIn", 0))}" '
                     f'duration="{t(dur)}" name={quoteattr(names[it["assetId"]])}>{speed}{"".join(attached)}</asset-clip>')
    total = sum(i["duration"] for i in main["items"])
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.10">\n'
            f'<resources>{"".join(res)}</resources>\n<library><event name={quoteattr(title)}><project name={quoteattr(title)}>'
            f'<sequence format="r0" duration="{t(total)}" tcStart="0s" tcFormat="NDF"><spine>{"".join(spine)}</spine></sequence>'
            '</project></event></library>\n</fcpxml>\n')


def to_edl(tl: dict, assets: dict, title: str) -> str:
    fps = int(tl["settings"].get("fps", 30))
    names = _media_names(assets)

    def tc(sec: float) -> str:
        f = round(sec * fps)
        return f"{f // (3600 * fps):02d}:{f // (60 * fps) % 60:02d}:{f // fps % 60:02d}:{f % fps:02d}"
    lines = [f"TITLE: {escape(title)[:70]}", "FCM: NON-DROP FRAME", ""]
    main = next(tr for tr in tl["tracks"] if tr["kind"] == "video")
    for n, it in enumerate([i for i in main["items"] if i.get("type") == "clip"], 1):
        src_in = it.get("srcIn", 0)
        src_out = src_in + it["duration"] * float(it.get("speed") or 1)
        reel = f"AX{n:03d}"
        lines.append(f"{n:03d}  {reel:<8} AA/V  C        {tc(src_in)} {tc(src_out)} {tc(it['start'])} {tc(it['start'] + it['duration'])}")
        lines.append(f"* FROM CLIP NAME: {names.get(it['assetId'], it['assetId'])}")
        if it.get("speed", 1) != 1:
            lines.append(f"M2   {reel:<8}       {float(it['speed']) * fps:05.1f}                {tc(src_in)}")
        lines.append("")
    return "\n".join(lines)


FORMATS = {"otio": (to_otio, "application/json", "otio"), "fcpxml": (to_fcpxml, "application/xml", "fcpxml"),
           "edl": (to_edl, "text/plain", "edl")}
