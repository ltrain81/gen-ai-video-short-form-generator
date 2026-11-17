"""Media preparation (runs in the Fargate media worker; needs ffmpeg/ffprobe, pdftoppm, libreoffice).

prepare(job) downloads an original, detects its kind and writes content-addressed derivatives:
  video    proxy_720.mp4 (preview + analysis), proxy_360.mp4 (scrub), thumb.jpg, sprite_*.jpg,
           waveform.json, audio.m4a, chunks/NNNN.mp4 + chunks/NNNN.m4a
  audio    audio.m4a, waveform.json, chunks
  image    image.jpg (<=2048px, <5MB), thumb.jpg
  document pages/NNNN.jpg (+ thumb.jpg)       pdf / ppt / pptx / key / odp
  font, other: stored as-is (no derivatives)
Then sends `prepare.done` with a manifest to the ingest queue.
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import mimetypes
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import storage
from .config import get_config

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".wmv", ".flv", ".3gp", ".mxf"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aiff", ".aif", ".wma"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif"}
DOC_EXT = {".pdf", ".ppt", ".pptx", ".key", ".odp", ".doc", ".docx"}
FONT_EXT = {".ttf", ".otf", ".woff", ".woff2"}

SPRITE_INTERVAL_SEC = 2
SPRITE_COLS, SPRITE_ROWS, SPRITE_W = 10, 10, 160
WAVEFORM_PEAKS_PER_SEC = 50


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def ffprobe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    return json.loads(out.stdout)


def detect_kind(filename: str, content_type: str, probe: dict | None) -> str:
    ext = Path(filename).suffix.lower()
    if ext in DOC_EXT:
        return "document"
    if ext in FONT_EXT:
        return "font"
    if ext in IMAGE_EXT or content_type.startswith("image/"):
        return "image"
    streams = (probe or {}).get("streams", [])
    has_video = any(s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1
                    for s in streams)
    duration = float((probe or {}).get("format", {}).get("duration", 0) or 0)
    if has_video and duration > 0.5:
        return "video"
    if any(s.get("codec_type") == "audio" for s in streams) or ext in AUDIO_EXT:
        return "audio"
    if has_video:
        return "image"
    return "other"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class Uploader:
    def __init__(self, ck: str):
        self.prefix = storage.content_prefix(ck)
        self.bucket = get_config().media_bucket

    def put(self, local: Path, rel: str) -> str:
        key = f"{self.prefix}/{rel}"
        ctype = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
        storage.s3().upload_file(str(local), self.bucket, key, ExtraArgs={"ContentType": ctype})
        return key

    def put_json(self, data, rel: str) -> str:
        key = f"{self.prefix}/{rel}"
        storage.s3().put_object(Bucket=self.bucket, Key=key, Body=json.dumps(data).encode(),
                                ContentType="application/json")
        return key


# ------------------------------------------------------------------ video / audio
def video_info(probe: dict) -> dict:
    v = next(s for s in probe["streams"] if s.get("codec_type") == "video")
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    rotation = int(v.get("tags", {}).get("rotate", rotation) or 0)
    if abs(rotation) % 180 == 90:
        w, h = h, w
    num, _, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    return {"width": w, "height": h, "fps": round(fps, 3) if 1 < fps < 241 else 30.0,
            "durationSec": float(probe["format"].get("duration", 0) or 0),
            "hasAudio": any(s.get("codec_type") == "audio" for s in probe["streams"]),
            "videoCodec": v.get("codec_name")}


def scale_filter(max_side: int) -> str:
    # Fit inside max_side x max_side keeping aspect, even dimensions; normalize to CFR and SDR-ish yuv420p.
    return (f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))',"
            "format=yuv420p")


def make_proxy(src: Path, dst: Path, max_side: int, crf: int, fps: float, audio: bool) -> None:
    gop = max(1, round(fps * 2))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-map", "0:v:0", "-vf", scale_filter(max_side),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-g", str(gop), "-keyint_min", str(gop),
           "-sc_threshold", "0", "-movflags", "+faststart"]
    if audio:
        cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"]
    run(cmd + [str(dst)])


def extract_audio(src: Path, dst: Path) -> None:
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-map", "0:a:0", "-c:a", "aac", "-b:a", "128k",
         "-ac", "2", "-ar", "48000", "-af", "aresample=async=1", str(dst)])


def waveform(src: Path, duration: float) -> dict:
    """Peak envelope (0-1) at WAVEFORM_PEAKS_PER_SEC, mono 8 kHz decode."""
    rate = 8000
    proc = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate),
                           "-f", "s16le", "-"], check=True, capture_output=True)
    samples = array.array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    window = rate // WAVEFORM_PEAKS_PER_SEC
    peaks = [round(max((abs(x) for x in samples[i:i + window]), default=0) / 32768, 3)
             for i in range(0, len(samples), window)]
    return {"peaksPerSec": WAVEFORM_PEAKS_PER_SEC, "durationSec": duration, "peaks": peaks}


def thumbnail(src: Path, dst: Path, at: float, width: int = 640) -> None:
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(at, 0):.2f}", "-i", str(src), "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "3", str(dst)])


def sprites(src: Path, outdir: Path, duration: float) -> list[dict]:
    """Contact sheets: one frame every SPRITE_INTERVAL_SEC, COLSxROWS tiles per sheet."""
    per_sheet = SPRITE_COLS * SPRITE_ROWS
    total = max(1, math.ceil(duration / SPRITE_INTERVAL_SEC))
    pattern = outdir / "sprite_%04d.jpg"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf",
         f"fps=1/{SPRITE_INTERVAL_SEC},scale={SPRITE_W}:-2,tile={SPRITE_COLS}x{SPRITE_ROWS}", "-q:v", "5",
         str(pattern)])
    sheets = sorted(outdir.glob("sprite_*.jpg"))
    return [{"file": p, "firstFrame": i * per_sheet, "frames": min(per_sheet, total - i * per_sheet)}
            for i, p in enumerate(sheets)]


def split_chunks(src: Path, outdir: Path, chunk_sec: int, duration: float, ext: str) -> list[dict]:
    """Keyframe-aligned stream-copy segmentation. Returns [{index, file, startSec, endSec}]."""
    if duration <= chunk_sec * 1.1:
        return [{"index": 0, "file": src, "startSec": 0.0, "endSec": duration}]
    listfile = outdir / f"segments_{ext}.csv"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-map", "0", "-c", "copy", "-f", "segment",
         "-segment_time", str(chunk_sec), "-reset_timestamps", "1", "-segment_list", str(listfile),
         "-segment_list_type", "csv", str(outdir / f"chunk_%04d.{ext}")])
    chunks = []
    for i, line in enumerate(listfile.read_text().strip().splitlines()):
        name, start, end = line.rsplit(",", 2)
        chunks.append({"index": i, "file": outdir / name, "startSec": float(start), "endSec": float(end)})
    return chunks


def prepare_av(src: Path, work: Path, up: Uploader, kind: str, probe: dict) -> dict:
    chunk_sec = get_config().analysis_chunk_sec
    manifest: dict = {"kind": kind}
    if kind == "video":
        info = video_info(probe)
        manifest.update(info)
        dur = info["durationSec"]
        p720, p360 = work / "proxy_720.mp4", work / "proxy_360.mp4"
        make_proxy(src, p720, 1280, 23, info["fps"], info["hasAudio"])
        make_proxy(src, p360, 640, 30, info["fps"], info["hasAudio"])
        manifest["proxyKey"] = up.put(p720, "proxy_720.mp4")
        manifest["scrubKey"] = up.put(p360, "proxy_360.mp4")
        thumb = work / "thumb.jpg"
        thumbnail(p720, thumb, min(dur * 0.1, 10))
        manifest["thumbnailKey"] = up.put(thumb, "thumb.jpg")
        sheet_dir = work / "sprites"
        sheet_dir.mkdir()
        manifest["sprites"] = {
            "intervalSec": SPRITE_INTERVAL_SEC, "cols": SPRITE_COLS, "rows": SPRITE_ROWS, "tileWidth": SPRITE_W,
            "sheets": [{"key": up.put(s["file"], f"sprites/{s['file'].name}"), "firstFrame": s["firstFrame"],
                        "frames": s["frames"]} for s in sprites(p360, sheet_dir, dur)],
        }
        av_source = p720
    else:
        dur = float(probe["format"].get("duration", 0) or 0)
        manifest.update({"durationSec": dur, "hasAudio": True})
        av_source = src

    if manifest.get("hasAudio"):
        audio = work / "audio.m4a"
        extract_audio(av_source, audio)
        manifest["audioKey"] = up.put(audio, "audio.m4a")
        manifest["waveformKey"] = up.put_json(waveform(audio, dur), "waveform.json")

    chunk_dir = work / "chunks"
    chunk_dir.mkdir()
    chunks = []
    if kind == "video":
        vchunks = split_chunks(p720, chunk_dir, chunk_sec, dur, "mp4")
        for c in vchunks:
            entry = {"index": c["index"], "startSec": c["startSec"], "endSec": c["endSec"],
                     "key": manifest["proxyKey"] if c["file"] == p720 else up.put(c["file"], f"chunks/{c['index']:04d}.mp4")}
            if manifest.get("hasAudio"):
                if c["file"] == p720:
                    entry["audioKey"] = manifest["audioKey"]
                else:
                    a = chunk_dir / f"chunk_{c['index']:04d}.m4a"
                    extract_audio(c["file"], a)
                    entry["audioKey"] = up.put(a, f"chunks/{c['index']:04d}.m4a")
            chunks.append(entry)
    else:
        audio = work / "audio.m4a"
        for c in split_chunks(audio, chunk_dir, chunk_sec, dur, "m4a"):
            key = manifest["audioKey"] if c["file"] == audio else up.put(c["file"], f"chunks/{c['index']:04d}.m4a")
            chunks.append({"index": c["index"], "startSec": c["startSec"], "endSec": c["endSec"],
                           "key": key, "audioKey": key})
    manifest["chunks"] = chunks
    return manifest


# ------------------------------------------------------------------ image / document
MAX_IMAGE_BYTES = 4_800_000


def normalize_image(src: Path, dst: Path, max_side: int = 2048) -> tuple[int, int]:
    q = 3
    while True:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-frames:v", "1", "-vf",
             f"scale='min({max_side},iw)':'min({max_side},ih)':force_original_aspect_ratio=decrease", "-q:v",
             str(q), str(dst)])
        if dst.stat().st_size <= MAX_IMAGE_BYTES or q >= 15:
            break
        q += 3
    info = ffprobe(dst)["streams"][0]
    return int(info["width"]), int(info["height"])


def prepare_image(src: Path, work: Path, up: Uploader) -> dict:
    img = work / "image.jpg"
    w, h = normalize_image(src, img)
    thumb = work / "thumb.jpg"
    normalize_image(img, thumb, 640)
    key = up.put(img, "image.jpg")
    return {"kind": "image", "width": w, "height": h, "imageKey": key, "thumbnailKey": up.put(thumb, "thumb.jpg"),
            "images": [{"index": 0, "key": key, "width": w, "height": h}]}


def prepare_document(src: Path, work: Path, up: Uploader) -> dict:
    pdf = src
    if src.suffix.lower() != ".pdf":
        run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(work), str(src)], timeout=1800,
            env={**os.environ, "HOME": str(work)})
        pdf = work / (src.stem + ".pdf")
    pages_dir = work / "pages"
    pages_dir.mkdir()
    run(["pdftoppm", "-jpeg", "-jpegopt", "quality=88", "-scale-to", "1920", str(pdf), str(pages_dir / "p")])
    images = []
    for i, page in enumerate(sorted(pages_dir.glob("p-*.jpg"))):
        dst = pages_dir / f"page_{i:04d}.jpg"
        w, h = normalize_image(page, dst)
        images.append({"index": i, "key": up.put(dst, f"pages/{i:04d}.jpg"), "width": w, "height": h})
    manifest = {"kind": "document", "pageCount": len(images), "images": images}
    if images:
        thumb = work / "thumb.jpg"
        normalize_image(pages_dir / "page_0000.jpg", thumb, 640)
        manifest["thumbnailKey"] = up.put(thumb, "thumb.jpg")
    if src.suffix.lower() != ".pdf":
        manifest["pdfKey"] = up.put(pdf, "document.pdf")
    return manifest


# ------------------------------------------------------------------ entry
def prepare(job: dict, send_event) -> None:
    work = Path(tempfile.mkdtemp(prefix="prep-"))
    try:
        filename = job.get("filename") or job["key"].rsplit("/", 1)[-1]
        src = work / ("original" + Path(filename).suffix.lower())
        storage.s3().download_file(job["bucket"], job["key"], str(src))
        ck = job.get("contentKey") or f"s256-{sha256_file(src)}"
        try:
            probe = ffprobe(src)
        except subprocess.CalledProcessError:
            probe = None
        kind = detect_kind(filename, job.get("contentType", ""), probe)
        up = Uploader(ck)
        if kind in {"video", "audio"}:
            manifest = prepare_av(src, work, up, kind, probe)
        elif kind == "image":
            manifest = prepare_image(src, work, up)
        elif kind == "document":
            manifest = prepare_document(src, work, up)
        else:
            manifest = {"kind": kind}
        up.put_json(manifest, "manifest.json")
        send_event({"type": "prepare.done", "assetId": job["assetId"], "contentKey": ck, "manifest": manifest})
    except Exception as exc:  # noqa: BLE001
        detail = exc.stderr[-2000:] if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        send_event({"type": "prepare.failed", "assetId": job["assetId"], "contentKey": job.get("contentKey"),
                    "error": detail})
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
