"""Render jobs: PROJ#{p} / RENDER#{id}. Requested via the API, executed by the Fargate media worker.

  renders/{tenant}/{project}/{renderId}/video.mp4 | captions.srt | captions.vtt | thumb.jpg
"""
from __future__ import annotations

import logging
import tempfile
import time
import uuid
from pathlib import Path

from . import ddb, ingest, projects, storage
from .config import get_config

log = logging.getLogger(__name__)
FILES = {"video": "video.mp4", "srt": "captions.srt", "vtt": "captions.vtt", "thumbnail": "thumb.jpg"}


def prefix(tenant: str, project_id: str, render_id: str) -> str:
    return f"renders/{tenant}/{project_id}/{render_id}"


def request(project: dict, quality: str, requested_by: str) -> dict:
    rid = "r" + uuid.uuid4().hex[:16]
    item = {"PK": f"PROJ#{project['projectId']}", "SK": f"RENDER#{rid}", "renderId": rid, "status": "queued",
            "quality": quality, "version": project["currentVersion"], "progress": 0, "requestedBy": requested_by,
            "createdAt": ddb.now_ms()}
    ddb.put(item)
    ingest.send_ingest_event({"type": "render.request", "projectId": project["projectId"], "renderId": rid,
                              "tenantId": project["tenantId"]})
    return item


def list_for(project: dict) -> list[dict]:
    out = []
    for r in ddb.query(f"PROJ#{project['projectId']}", "RENDER#", forward=False, limit=50):
        entry = {k: r.get(k) for k in ("renderId", "status", "quality", "version", "progress", "message", "createdAt",
                                       "finishedAt", "durationSec", "width", "height", "error")}
        if r.get("status") == "done":
            base = prefix(project["tenantId"], project["projectId"], r["renderId"])
            title = (r.get("title") or "video").replace("/", "_")[:80]
            entry["urls"] = {k: storage.presign_get(f"{base}/{f}", 3600, f"{title}.{f.rsplit('.', 1)[1]}" if k != "thumbnail" else None)
                             for k, f in FILES.items()}
        out.append(entry)
    return out


def _update(project_id: str, rid: str, **fields) -> None:
    ddb.update(f"PROJ#{project_id}", f"RENDER#{rid}", fields)


def proxy_size(w: int, h: int, max_side: int = 1280) -> tuple[int, int]:
    """Frame size of proxy_720.mp4 (media.make_proxy: long side capped at max_side, other side even, -2 rounding)."""
    def even(x: float) -> int:  # ffmpeg av_rescale: round half away from zero, in units of 2
        return max(2, int(x / 2 + 0.5) * 2)
    if w > h:
        ow = min(max_side, w)
        return ow, even(h * ow / w)
    oh = min(max_side, h)
    return even(w * oh / h), oh


def media_map(timeline: dict, quality: str) -> dict:
    """assetId → presigned inputs. Final renders read originals (best quality); previews read 720p proxies."""
    out = {}
    ids = {i["assetId"] for t in timeline["tracks"] for i in t["items"] if i.get("assetId")} | {
        e["assetId"] for t in timeline["tracks"] for i in t["items"] for e in (i.get("effects") or [])
        if isinstance(e, dict) and e.get("type") == "lut" and e.get("assetId")}
    for aid in ids:
        a = ddb.get(f"ASSET#{aid}", "META") or {}
        content = ddb.get(f"CONTENT#{a.get('contentKey', '')}", "META") if a.get("contentKey") else None
        m = (content or {}).get("manifest") or {}
        sign = lambda k: storage.presign_get(k, 6 * 3600) if k else None  # noqa: E731
        kind = m.get("kind", a.get("kind"))
        if kind == "video":
            key = a.get("originalKey") if quality == "final" and a.get("originalKey") else m.get("proxyKey")
            url = sign(key)
        elif kind in {"image", "document"}:
            url = sign(m.get("imageKey") or (m.get("images") or [{}])[0].get("key"))
        else:
            url = sign(m.get("audioKey") or a.get("originalKey"))
        w, h = int(m.get("width") or 0) or None, int(m.get("height") or 0) or None
        if kind == "video" and w and h and key == m.get("proxyKey"):
            w, h = proxy_size(w, h)  # layout filters size their crops from these: they must match the decoded input
        out[aid] = {"url": url, "kind": kind, "width": w, "height": h, "hasAudio": bool(m.get("hasAudio", kind != "image")),
                    "audioUrl": sign(m.get("audioKey")),
                    "originalUrl": sign(m.get("imageAlphaKey") or m.get("imageKey")) if kind == "image" else None,
                    "images": {int(i["index"]): sign(i["key"]) for i in m.get("images", [])}}
    return out


def run_job(job: dict) -> None:
    """Executed in the media worker container (needs ffmpeg with libass)."""
    from . import render
    pid, rid = job["projectId"], job["renderId"]
    project = ddb.get(f"PROJ#{pid}", "META", consistent=True)
    rec = ddb.get(f"PROJ#{pid}", f"RENDER#{rid}", consistent=True)
    if not project or not rec:
        raise RuntimeError("Unknown project/render")
    _update(pid, rid, status="running", startedAt=ddb.now_ms(), progress=0.02)
    last = [0.0]

    def progress(frac: float, msg: str) -> None:
        if time.time() - last[0] > 3:
            last[0] = time.time()
            _update(pid, rid, progress=round(min(0.98, 0.02 + frac * 0.96), 3), message=msg)

    try:
        timeline = projects.load_timeline(project, int(rec["version"]))
        out_dir = Path(tempfile.mkdtemp(prefix="out-"))
        info = render.render_timeline(timeline, media_map(timeline, rec["quality"]), out_dir, rec["quality"], progress)
        base = prefix(project["tenantId"], pid, rid)
        ctypes = {"mp4": "video/mp4", "srt": "application/x-subrip", "vtt": "text/vtt", "jpg": "image/jpeg"}
        for f in FILES.values():
            storage.s3().upload_file(str(out_dir / f), get_config().media_bucket, f"{base}/{f}",
                                     ExtraArgs={"ContentType": ctypes[f.rsplit('.', 1)[1]]})
        _update(pid, rid, status="done", progress=1, finishedAt=ddb.now_ms(), message="done",
                title=timeline.get("meta", {}).get("title") or project.get("title"), **info)
    except Exception as exc:  # noqa: BLE001
        log.exception("Render failed")
        _update(pid, rid, status="failed", error=str(exc)[-1500:], finishedAt=ddb.now_ms())
        raise
