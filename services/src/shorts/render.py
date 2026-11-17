"""Server renderer: timeline JSON → MP4 (+ SRT/VTT, thumbnail) with FFmpeg. Runs in the Fargate media worker.

Pipeline
 1. Main track (magnetic): each item → normalized segment (W×H, fps, yuv420p, AAC 48 kHz stereo) with speed,
    reframe layout (crop | split | fit_blur | letterbox), Ken Burns for images, fades/transition-ins.
 2. Concatenate segments.
 3. Overlay B-roll items (same normalization) at their timeline start.
 4. Burn text + word-timed captions via libass (ASS generated from the same layout math as the browser preview).
 5. Mix music/voiceover (music ducked under dialogue), loudness-normalize to settings.loudness.
Inputs are read through presigned HTTPS URLs with input seeking (only needed byte ranges are fetched).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

FONT = os.environ.get("RENDER_FONT", "Pretendard")
# Bundled fonts (Pretendard, OFL) so text renders the same in Lambda (stills) and the Fargate worker (videos).
FONTS_DIR = os.environ.get("RENDER_FONTS_DIR") or str(Path(__file__).resolve().parent / "fonts")
PRESETS = {"preview": {"scale": 0.5, "crf": 30, "preset": "veryfast", "abr": "96k"},
           "final": {"scale": 1.0, "crf": 19, "preset": "medium", "abr": "192k"}}


XFADE = {"fade": "fade", "dissolve": "dissolve", "dipToBlack": "fadeblack", "dipToWhite": "fadewhite",
         "wipeLeft": "wipeleft", "wipeUp": "wipeup", "slideLeft": "slideleft", "zoom": "zoomin", "blur": "hblur"}


class RenderError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BIN", "ffmpeg")


def run(cmd: list[str], timeout: int = 7200) -> None:
    """ffmpeg with an argument list (no shell). Remote inputs must be presigned S3 HTTPS URLs."""
    from . import safeurl
    if not isinstance(cmd, list) or cmd[0] != ffmpeg_bin():
        raise RenderError("Unexpected command")
    for i, a in enumerate(cmd[:-1]):
        if a == "-i" and "://" in cmd[i + 1]:
            safeurl.check(cmd[i + 1])
    log.info("ffmpeg %s", " ".join(c if len(c) < 200 else c[:200] + "…" for c in cmd[1:]))
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # nosec B603 - validated list args
    if p.returncode != 0:
        raise RenderError(p.stderr[-3000:])


def even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


# ------------------------------------------------------------------ tolerant readers (older timelines)
NAMED_Y = {"top": 0.18, "upper": 0.3, "center": 0.5, "middle": 0.5, "lower": 0.7, "bottom": 0.8}


def as_obj(v) -> dict:
    return v if isinstance(v, dict) else {}


def as_position(v, default_y: float) -> dict:
    if isinstance(v, dict):
        return {"x": 0.5, "y": default_y, **v}
    if isinstance(v, str):
        return {"x": 0.5, "y": NAMED_Y.get(v.lower(), default_y)}
    return {"x": 0.5, "y": default_y}


# ------------------------------------------------------------------ geometry (shared with web preview)
def path_expr(path: list[dict], key: str, t0: float, default: float) -> str:
    """Piecewise-hold expression over segment-local time t (source seconds since srcIn)."""
    pts = sorted(path or [], key=lambda p: p["t"])
    if not pts:
        return f"{default}"
    expr = f"{float(pts[-1].get(key, default)):.4f}"
    for p in reversed(pts[:-1]):
        nxt = pts[pts.index(p) + 1]
        expr = f"if(lt(t,{max(0.0, nxt['t'] - t0):.3f}),{float(p.get(key, default)):.4f},{expr})"
    return expr


def crop_filter(src_w: int, src_h: int, W: int, H: int, path: list[dict], t0: float) -> str:
    """Cover-crop to W:H following the focus path (x,y = centre in 0..1, scale ≥ 1 zooms in)."""
    ar = W / H
    scale = float((path or [{}])[0].get("scale", 1.0) or 1.0)
    if src_w / src_h > ar:
        ch = src_h / scale
        cw = ch * ar
    else:
        cw = src_w / scale
        ch = cw / ar
    cw, ch = even(cw), even(ch)
    fx, fy = path_expr(path, "x", t0, 0.5), path_expr(path, "y", t0, 0.5)
    x = f"max(0,min(iw-{cw},({fx})*iw-{cw}/2))"
    y = f"max(0,min(ih-{ch},({fy})*ih-{ch}/2))"
    return f"crop={cw}:{ch}:'{x}':'{y}',scale={W}:{H}:flags=lanczos"


def _bg_filter(src: str, W: int, H: int, background: str) -> str:
    """Full-canvas background for box layouts: blurred copy of the clip, or a solid colour."""
    if background == "blur":
        return (f"{src}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur=24:2,eq=brightness=-0.12")
    col = background if background.startswith("#") and len(background) in (4, 7) else "#000000"
    return f"{src}scale={W}:{H},drawbox=x=0:y=0:w=iw:h=ih:color={col}@1:t=fill"


def _pad_color(opts: dict) -> str:
    c = str(opts.get("background") or opts.get("padColor") or "black")
    return c if c.startswith("#") or c.isalpha() else "black"


def box_rect(box: dict, W: int, H: int) -> tuple[int, int, int, int]:
    """Normalized {x, y, w, h} (top-left origin, 0..1 of the canvas) → even pixel rect, clamped to the canvas."""
    w = even(max(0.02, min(1.0, float(box.get("w", 1)))) * W)
    h = even(max(0.02, min(1.0, float(box.get("h", 1)))) * H)
    x = int(round(max(0.0, min(1.0, float(box.get("x", (1 - w / W) / 2)))) * W))
    y = int(round(max(0.0, min(1.0, float(box.get("y", (1 - h / H) / 2)))) * H))
    return x, y, w, h


def layout_filter(layout: str, src_w: int, src_h: int, W: int, H: int, path: list[dict], t0: float,
                  opts: dict | None = None) -> str:
    """Video filter graph from [0:v] to [v] for one clip.

    Layouts: crop (cover the canvas, follow the focus path) · fit_blur · letterbox · split (subject on top, whole
    frame below) · box — the clip fills any rectangle of the canvas (`box: {x,y,w,h}`), `fit: cover|contain`,
    `background: black|blur|#rrggbb`. e.g. a 1:1 square centred in a 9:16 frame is
    {layout: box, box: {x: 0, y: 0.2187, w: 1, h: 0.5625}}.
    """
    opts = opts or {}
    if layout == "box":
        bx, by, bw, bh = box_rect(as_obj(opts.get("box")), W, H)
        fit = opts.get("fit", "cover")
        fg = (crop_filter(src_w, src_h, bw, bh, path, t0) if fit != "contain"
              else f"scale={bw}:{bh}:force_original_aspect_ratio=decrease")
        bg = _bg_filter("[a]", W, H, str(opts.get("background") or opts.get("padColor") or "black"))
        return (f"[0:v]split=2[a][b];{bg}[bg];[b]{fg}[fg];"
                f"[bg][fg]overlay=x='{bx}+({bw}-w)/2':y='{by}+({bh}-h)/2'[v]")
    if layout == "fit_blur":
        return (f"[0:v]split=2[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur=24:2,eq=brightness=-0.12[bg];[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
    if layout == "letterbox":
        pad = _pad_color(opts)
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={pad}[v]")
    if layout == "split":  # top: subject crop (speaker); bottom: whole frame (slide/board)
        top_h = even(H * float(opts.get("splitRatio", 0.5)))
        return (f"[0:v]split=2[a][b];[a]{crop_filter(src_w, src_h, W, top_h, path, t0)}[top];"
                f"[b]scale={W}:{H - top_h}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H - top_h}:(ow-iw)/2:(oh-ih)/2:color={_pad_color(opts)}[bot];[top][bot]vstack[v]")
    return f"[0:v]{crop_filter(src_w, src_h, W, H, path, t0)}[v]"


def atempo_chain(speed: float) -> str:
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.4f}")
    return ",".join(parts)


# ------------------------------------------------------------------ segments
# ffmpeg xfade transitions (all of them are accepted; friendly aliases map onto these)
XFADE_NAMES = {"fade", "wipeleft", "wiperight", "wipeup", "wipedown", "slideleft", "slideright", "slideup", "slidedown",
               "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite", "radial", "smoothleft", "smoothright",
               "smoothup", "smoothdown", "circleopen", "circleclose", "vertopen", "vertclose", "horzopen", "horzclose",
               "dissolve", "pixelize", "diagtl", "diagtr", "diagbl", "diagbr", "hlslice", "hrslice", "vuslice", "vdslice",
               "hblur", "fadegrays", "wipetl", "wipetr", "wipebl", "wipebr", "squeezeh", "squeezev", "zoomin", "fadefast",
               "fadeslow", "hlwind", "hrwind", "vuwind", "vdwind", "coverleft", "coverright", "coverup", "coverdown",
               "revealleft", "revealright", "revealup", "revealdown"}


def xfade_name(kind) -> str | None:
    if not kind or kind == "cut":
        return None
    return XFADE.get(kind) or (str(kind).lower() if str(kind).lower() in XFADE_NAMES else "fade")


def _f(e: dict, k: str, d: float) -> float:
    try:
        return float(e.get(k, d))
    except (TypeError, ValueError):
        return d


def video_effects(effects, W: int) -> list[str]:
    """Clip effects → ffmpeg video filters. Unknown types are ignored (reported by validate_effects)."""
    out: list[str] = []
    for e in effects or []:
        if not isinstance(e, dict):
            continue
        t = e.get("type")
        if t == "color":
            out.append(f"eq=brightness={_f(e, 'brightness', 0):.3f}:contrast={_f(e, 'contrast', 1):.3f}:"
                       f"saturation={_f(e, 'saturation', 1):.3f}:gamma={_f(e, 'gamma', 1):.3f}")
            if _f(e, "temperature", 0):
                out.append(f"colortemperature=temperature={6500 - _f(e, 'temperature', 0) * 3500:.0f}")
            if _f(e, "tint", 0):
                out.append(f"colorbalance=gm={-_f(e, 'tint', 0) * 0.3:.3f}")
        elif t == "blur":
            out.append(f"gblur=sigma={max(0.1, _f(e, 'radius', 8) * W / 1080 / 2):.2f}")
        elif t == "vignette":
            out.append(f"vignette=angle={0.2 + max(0.0, min(1.0, _f(e, 'amount', 0.5))) * 0.6:.3f}")
        elif t == "sharpen":
            out.append(f"unsharp=5:5:{max(0.0, min(3.0, _f(e, 'amount', 0.5) * 1.5)):.2f}")
        elif t == "denoise":
            out.append(f"hqdn3d={max(0.0, _f(e, 'amount', 0.5)) * 4:.2f}")
        elif t == "grayscale":
            out.append("hue=s=0")
        elif t == "sepia":
            out.append("colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131")
        elif t == "invert":
            out.append("negate")
        elif t == "pixelate":
            n = max(2, int(_f(e, "size", 16)))
            out.append(f"scale=iw/{n}:ih/{n}:flags=neighbor,scale=iw*{n}:ih*{n}:flags=neighbor")
        elif t == "mirror":
            out.append("hflip")
    return out


def audio_effects(effects) -> list[str]:
    out: list[str] = []
    for e in effects or []:
        if not isinstance(e, dict):
            continue
        t = e.get("type")
        if t == "eq":
            if _f(e, "low", 0):
                out.append(f"bass=g={_f(e, 'low', 0) * 12:.1f}")
            if _f(e, "mid", 0):
                out.append(f"equalizer=f=1000:t=q:w=1:g={_f(e, 'mid', 0) * 12:.1f}")
            if _f(e, "high", 0):
                out.append(f"treble=g={_f(e, 'high', 0) * 12:.1f}")
        elif t in ("audioDenoise", "voiceClean"):
            out.append(f"afftdn=nr={10 + max(0.0, min(1.0, _f(e, 'amount', 0.5))) * 20:.0f}")
        elif t == "pitch":
            out.append(f"rubberband=pitch={max(0.5, min(2.0, _f(e, 'factor', 1))):.3f}")
    return out


def transform_filters(tf: dict, effects=None, W: int = 1080, rotate_canvas: bool = False) -> list[str]:
    """crop → flip → effects → rotation. Overlays rotate with transparent corners; the main layer keeps WxH."""
    out: list[str] = []
    crop = as_obj(tf.get("crop"))
    if crop:
        l, t_, r, b = (max(0.0, min(0.45, _f(crop, k, 0))) for k in ("l", "t", "r", "b"))
        out.append(f"crop=iw*{1 - l - r:.4f}:ih*{1 - t_ - b:.4f}:iw*{l:.4f}:ih*{t_:.4f}")
    if tf.get("flipH"):
        out.append("hflip")
    if tf.get("flipV"):
        out.append("vflip")
    out += video_effects(effects, W)
    rot = _f(tf, "rotation", 0)
    if rot:
        a = f"{rot:.3f}*PI/180"
        out.append(f"rotate={a}:c=black" if rotate_canvas else f"rotate={a}:c=none:ow=rotw({a}):oh=roth({a})")
    return out


def ass_color(c, default: str) -> tuple[str, int]:
    """CSS colour (#rgb, #rrggbb, #rrggbbaa, rgb()/rgba()) → (ASS &HBBGGRR&, ASS alpha 0=opaque..255)."""
    import re as _re
    c = str(c or default).strip()
    r = g = b = 255
    a = 1.0
    m = _re.match(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+))?\s*\)", c)
    if m:
        r, g, b = (int(float(m.group(k))) for k in (1, 2, 3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
    else:
        h = c.lstrip("#")
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        try:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            if len(h) == 8:
                a = int(h[6:8], 16) / 255
        except ValueError:
            return ass_color(default, "#FFFFFF") if c != default else ("&H00FFFFFF&", 0)
    return f"&H{b:02X}{g:02X}{r:02X}&", int(round((1 - max(0.0, min(1.0, a))) * 255))


def _mul_alpha(ass_alpha: int, opacity: float) -> int:
    vis = (255 - ass_alpha) / 255 * max(0.0, min(1.0, opacity))
    return int(round((1 - vis) * 255))


def shape_event(it: dict, layer: int, t0: str, t1: str, W: int, H: int, opacity: float) -> str:
    box = as_obj(it.get("box"))
    x, y = float(box.get("x", 0)) * W, float(box.get("y", 0)) * H
    w, h = max(1.0, float(box.get("w", 0.1)) * W), max(1.0, float(box.get("h", 0.1)) * H)
    fc, fa = ass_color(it.get("fill"), "#000000")
    st = as_obj(it.get("stroke"))
    sc, sa = ass_color(st.get("color"), "#FFFFFF")
    bord = float(st.get("width", 0) or 0) * H
    kind = it.get("shape", "rect")
    if kind == "ellipse":
        k = 0.5523
        rx, ry = w / 2, h / 2
        d = (f"m {rx:.0f} 0 b {rx + k * rx:.0f} 0 {w:.0f} {ry - k * ry:.0f} {w:.0f} {ry:.0f} "
             f"b {w:.0f} {ry + k * ry:.0f} {rx + k * rx:.0f} {h:.0f} {rx:.0f} {h:.0f} "
             f"b {rx - k * rx:.0f} {h:.0f} 0 {ry + k * ry:.0f} 0 {ry:.0f} b 0 {ry - k * ry:.0f} {rx - k * rx:.0f} 0 {rx:.0f} 0")
    elif kind == "roundRect":
        r = min(w, h) / 2 * max(0.0, min(1.0, float(it.get("radius", 0.25))))
        d = (f"m {r:.0f} 0 l {w - r:.0f} 0 b {w:.0f} 0 {w:.0f} 0 {w:.0f} {r:.0f} l {w:.0f} {h - r:.0f} "
             f"b {w:.0f} {h:.0f} {w:.0f} {h:.0f} {w - r:.0f} {h:.0f} l {r:.0f} {h:.0f} b 0 {h:.0f} 0 {h:.0f} 0 {h - r:.0f} "
             f"l 0 {r:.0f} b 0 0 0 0 {r:.0f} 0")
    else:
        d = f"m 0 0 l {w:.0f} 0 {w:.0f} {h:.0f} 0 {h:.0f}"
    extra = ""
    if kind == "progressBar":  # fills left → right over the item's duration
        dur_ms = max(1, int((float(it["duration"])) * 1000))
        extra = f"\\clip({x:.0f},{y:.0f},{x + 1:.0f},{y + h:.0f})\\t(0,{dur_ms},\\clip({x:.0f},{y:.0f},{x + w:.0f},{y + h:.0f}))"
    return (f"Dialogue: {layer},{t0},{t1},Base,,0,0,0,,{{\\an7\\pos({x:.0f},{y:.0f})\\p1\\1c{fc}\\1a&H{_mul_alpha(fa, opacity):02X}&"
            f"\\3c{sc}\\3a&H{_mul_alpha(sa, opacity):02X}&\\bord{bord:.1f}\\shad0{extra}}}{d}{{\\p0}}")


class Renderer:
    def __init__(self, timeline: dict, media: dict[str, dict], workdir: Path, quality: str = "final",
                 progress: Callable[[float, str], None] | None = None):
        self.tl = timeline
        self.media = media  # assetId → {url, kind, width, height, durationSec, hasAudio, images:{page:url}}
        self.work = workdir
        self.q = PRESETS[quality]
        s = timeline["settings"]
        self.W, self.H = even(s["width"] * self.q["scale"]), even(s["height"] * self.q["scale"])
        self.fps = int(s.get("fps", 30))
        self.progress = progress or (lambda f, m: None)

    def _enc(self, audio: bool = True) -> list[str]:
        out = ["-c:v", "libx264", "-preset", self.q["preset"], "-crf", str(self.q["crf"]), "-pix_fmt", "yuv420p",
               "-r", str(self.fps), "-g", str(self.fps * 2)]
        if audio:
            out += ["-c:a", "aac", "-b:a", self.q["abr"], "-ar", "48000", "-ac", "2"]
        return out

    def segment(self, item: dict, out: Path, tail: float = 0.0, first: bool = True, layer: bool = False) -> Path:
        """Render one item. `tail` extends the clip past its out point (transition handle)."""
        W, H, dur = self.W, self.H, float(item["duration"]) + tail
        m = self.media.get(item["assetId"])
        if not m:
            raise RenderError(f"Missing media for asset {item['assetId']}")
        fades = []
        tin = as_obj(item.get("transitionIn"))
        # Transitions between clips are xfades (see render()); only the very first clip fades in from black.
        fade_in = float(item.get("fadeIn") or (tin.get("duration", 0) if first and tin.get("type") not in (None, "cut") else 0))
        fade_out = float(item.get("fadeOut") or 0)
        if fade_in:
            fades.append(f"fade=t=in:st=0:d={fade_in:.3f}")
        if fade_out:
            fades.append(f"fade=t=out:st={max(0.0, dur - fade_out):.3f}:d={fade_out:.3f}")
        vpost = ",".join([f"fps={self.fps}", "format=yuv420p", "setsar=1", *fades])
        if item["mediaKind"] == "image":
            url = m["images"].get(item.get("page", 0)) or m["url"]
            kb = item.get("kenBurns") or {"from": {"x": .5, "y": .5, "scale": 1}, "to": {"x": .5, "y": .5, "scale": 1}}
            frames = max(1, int(round(dur * self.fps)))
            z0, z1 = kb["from"]["scale"], kb["to"]["scale"]
            x0, x1, y0, y1 = kb["from"]["x"], kb["to"]["x"], kb["from"]["y"], kb["to"]["y"]
            u = f"(on/{max(1, frames - 1)})"
            graph = (f"[0:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                     f"zoompan=z='{z0}+({z1 - z0})*{u}':x='(iw-iw/zoom)*({x0}+({x1 - x0})*{u})':"
                     f"y='(ih-ih/zoom)*({y0}+({y1 - y0})*{u})':d={frames}:s={W}x{H}:fps={self.fps},{vpost}[v];"
                     f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f}[a]")
            run([ffmpeg_bin(), "-y", "-loglevel", "error", "-loop", "1", "-t", f"{dur:.3f}", "-i", url,
                 "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}", *self._enc(), str(out)])
            return out
        speed = float(item.get("speed") or 1)
        src_in, src_out = float(item["srcIn"]), float(item["srcOut"])
        reframe = item.get("reframe") or {}
        canvas_ar, src_ar = W / H, (m.get("width") or W) / (m.get("height") or H)
        layout = reframe.get("layout") or ("crop" if abs(canvas_ar - src_ar) > 0.02 else "crop")
        v = layout_filter(layout, int(m.get("width") or W), int(m.get("height") or H), W, H,
                          reframe.get("path") or [], src_in,
                          {"padColor": self.tl["settings"].get("background") or "black", **reframe})
        mode = item.get("mode") or "normal"
        if mode == "freeze":  # hold the first frame for the whole item
            frames = max(1, int(round(dur * self.fps)))
            v = v.replace("[0:v]", f"[0:v]trim=end_frame=1,loop=loop={frames}:size=1:start=0,setpts=N/{self.fps}/TB,", 1)
            speed = 1.0
        # Main-track clips apply their transform here; overlay layers apply it while compositing (render()).
        extra = transform_filters({} if layer else as_obj(item.get("transform")), item.get("effects"), W, rotate_canvas=True)
        if mode == "reverse":
            extra.append("reverse")
        speed_f = "" if mode == "freeze" else f"setpts=(PTS-STARTPTS)/{speed:.5f},"
        v = v[:-3] + f",{speed_f}{''.join(f + ',' for f in extra)}{vpost}[v]"
        vol = float(item.get("volume", 1.0))
        afades = []
        if fade_in:
            afades.append(f"afade=t=in:st=0:d={fade_in:.3f}")
        if fade_out:
            afades.append(f"afade=t=out:st={max(0.0, dur - fade_out):.3f}:d={fade_out:.3f}")
        afx = audio_effects(item.get("effects")) + (["areverse"] if mode == "reverse" else [])
        if m.get("hasAudio", True) and mode != "freeze":
            a = f"[0:a]asetpts=PTS-STARTPTS,{atempo_chain(speed)},volume={vol:.3f}" + \
                "".join("," + f for f in afx + afades) + f",aresample=48000,apad,atrim=0:{dur:.3f}[a]"
        else:
            a = f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f}[a]"
        src_out = src_out + tail * speed
        run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{src_in:.3f}", "-t", f"{src_out - src_in:.3f}",
             "-i", m["url"], "-filter_complex", f"{v};{a}", "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
             *self._enc(), str(out)])
        return out

    # ------------------------------------------------------------------ captions/text (ASS)
    def ass(self, path: Path) -> bool:
        """Text, captions and shapes as one ASS script. Layer = track index, so stacking follows track order."""
        W, H = self.W, self.H
        lines: list[str] = []

        def ts(t: float) -> str:
            t = max(0.0, t)
            return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"

        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")

        def font(st: dict) -> str:
            return str(st.get("font") or FONT).replace(",", " ")[:60]

        def style_tags(st: dict, opacity: float = 1.0) -> tuple[str, str]:
            """(style name, override tags)."""
            size = float(st.get("size") or 0.05) * H
            stroke = as_obj(st.get("stroke"))
            outline = float(stroke.get("width", 0) or 0) * H
            fc, fa = ass_color(st.get("color"), "#FFFFFF")
            tags = [f"\\fn{font(st)}", f"\\fs{size:.1f}", f"\\b{1 if float(st.get('weight') or 800) >= 600 else 0}",
                    f"\\i{1 if st.get('italic') else 0}", f"\\1c{fc}", f"\\1a&H{_mul_alpha(fa, opacity):02X}&"]
            if st.get("letterSpacing") is not None:
                tags.append(f"\\fsp{float(st['letterSpacing']) * size:.1f}")
            sh = as_obj(st.get("shadow"))
            if st.get("background"):
                bc, ba = ass_color(st.get("background"), "#000000")
                pad = float(st.get("padding", 0.25)) * size
                tags += [f"\\3c{bc}", f"\\3a&H{_mul_alpha(ba, opacity):02X}&", f"\\bord{pad:.1f}", "\\shad0"]
                return "Box", "".join(tags)
            sc, sa = ass_color(stroke.get("color"), "#000000")
            tags += [f"\\3c{sc}", f"\\3a&H{_mul_alpha(sa, opacity):02X}&", f"\\bord{outline:.1f}"]
            if sh:
                hc, ha = ass_color(sh.get("color"), "#000000")
                tags += [f"\\4c{hc}", f"\\4a&H{_mul_alpha(ha, opacity):02X}&",
                         f"\\xshad{float(sh.get('x', 0)) * H / 400:.1f}", f"\\yshad{float(sh.get('y', 2)) * H / 400:.1f}"]
                if float(sh.get("blur", 0) or 0) > 0:
                    tags.append(f"\\blur{min(20.0, float(sh['blur']) / 4):.1f}")
            else:
                tags.append("\\shad0")
            return "Base", "".join(tags)

        def placement(pos: dict, st: dict) -> tuple[int, float, float, int, int]:
            """\an, x, y and wrap margins from anchor + align + maxWidth (matches the preview's box model)."""
            mw = max(0.05, min(1.0, float(pos.get("maxWidth", 0.86) or 0.86)))
            align = st.get("align") or "center"
            anchor = pos.get("anchor") or "center"
            row = {"top": 7, "bottom": 1}.get(anchor, 4)
            col_ = {"left": 0, "right": 2}.get(align, 1)
            cx, cy = float(pos["x"]) * W, float(pos["y"]) * H
            x = cx - mw * W / 2 if col_ == 0 else cx + mw * W / 2 if col_ == 2 else cx
            ml = int(max(0, cx - mw * W / 2))
            mr = int(max(0, W - (cx + mw * W / 2)))
            return row + col_, x, cy, ml, mr

        for li, tr in enumerate(self.tl["tracks"]):
            if tr.get("hidden"):
                continue
            for it in tr["items"]:
                s0, s1 = float(it["start"]), float(it["start"]) + float(it["duration"])
                op_ = float(it.get("opacity", 1) if it.get("opacity") is not None else 1)
                if it["type"] == "text":
                    st = as_obj(it.get("style"))
                    pos = as_position(it.get("position"), 0.2)
                    an, x, y, ml, mr = placement(pos, st)
                    anim = as_obj(it.get("animation"))
                    ain, aout = anim.get("in") or "none", anim.get("out") or "none"
                    fi = 180 if ain in ("fade", "pop", "slideUp", "slideDown") else 0
                    fo = 180 if aout in ("fade", "pop") else 0
                    extra = f"\\fad({fi},{fo})" if fi or fo else ""
                    if ain == "pop":
                        extra += "\\fscx80\\fscy80\\t(0,160,\\fscx100\\fscy100)"
                    if it.get("rotation"):
                        extra += f"\\frz{-float(it['rotation']):.1f}"
                    dy = 0.04 * H
                    if ain in ("slideUp", "slideDown"):
                        y0 = y + dy if ain == "slideUp" else y - dy
                        where = f"\\move({x:.0f},{y0:.0f},{x:.0f},{y:.0f},0,320)"
                    else:
                        where = f"\\pos({x:.0f},{y:.0f})"
                    raw = it["text"].upper() if st.get("uppercase") else it["text"]
                    style, tags = style_tags(st, op_)
                    if ain in ("typewriter", "wordByWord"):
                        units = list(raw) if ain == "typewriter" else raw.split(" ")
                        total = min(150.0, (s1 - s0) * 60)  # centiseconds; reveal over ≤1.5 s (≤60 % of the item)
                        cs = max(1, int(total / max(1, len(units))))
                        sep = "" if ain == "typewriter" else " "
                        body = sep.join(f"{{\\ko{cs}}}{esc(u)}" for u in units)
                        tags += "\\2a&HFF&"
                    else:
                        body = esc(raw)
                    lines.append(f"Dialogue: {li},{ts(s0)},{ts(s1)},{style},,{ml},{mr},0,,{{\\an{an}{where}{tags}{extra}}}{body}")
                elif it["type"] == "caption" and it.get("words"):
                    st = as_obj(it.get("style"))
                    pos = as_position(it.get("position"), 0.75)
                    x, y = float(pos.get("x", 0.5)) * W, float(pos["y"]) * H
                    hl = st.get("highlightColor")
                    words = it["words"]
                    upper = st.get("uppercase")
                    fmt = lambda w: esc(w["w"].upper() if upper else w["w"])  # noqa: E731
                    style, tags = style_tags(st, op_)
                    base_c, _ = ass_color(st.get("color"), "#FFFFFF")
                    m = int(W * 0.07)
                    if hl:  # one event per word interval with the active word highlighted (same as preview)
                        hc, _ = ass_color(hl, "#FFD400")
                        for k, w in enumerate(words):
                            end = words[k + 1]["s"] if k + 1 < len(words) else s1
                            start = s0 if k == 0 else w["s"]
                            body = " ".join(f"{{\\1c{hc}}}{fmt(x_)}{{\\1c{base_c}}}" if j == k else fmt(x_)
                                            for j, x_ in enumerate(words))
                            lines.append(f"Dialogue: {li},{ts(start)},{ts(max(end, start + 0.05))},{style},,{m},{m},0,,"
                                         f"{{\\an5\\pos({x:.0f},{y:.0f}){tags}}}{body}")
                    else:
                        lines.append(f"Dialogue: {li},{ts(s0)},{ts(s1)},{style},,{m},{m},0,,"
                                     f"{{\\an5\\pos({x:.0f},{y:.0f}){tags}}}{' '.join(fmt(w) for w in words)}")
                elif it["type"] == "shape":
                    lines.append(shape_event(it, li, ts(s0), ts(s1), W, H, op_))
        if not lines:
            return False
        header = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
                  "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                  "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
                  "MarginL, MarginR, MarginV, Encoding\n"
                  f"Style: Base,{FONT},{H * 0.05:.0f},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,5,"
                  f"{int(W * 0.07)},{int(W * 0.07)},0,1\n"
                  f"Style: Box,{FONT},{H * 0.05:.0f},&H00FFFFFF,&H00FFFFFF,&H59000000,&H00000000,1,0,0,0,100,100,0,0,3,6,0,5,"
                  f"{int(W * 0.07)},{int(W * 0.07)},0,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
        return True

    def subtitles(self) -> tuple[str, str]:
        """SRT + VTT from caption items (sidecar files)."""
        caps = sorted((it for tr in self.tl["tracks"] if tr["kind"] == "caption" for it in tr["items"]
                       if it["type"] == "caption"), key=lambda x: x["start"])
        def t(sec: float, sep: str) -> str:
            ms = int(round(sec * 1000))
            return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}{sep}{ms % 1000:03d}"
        srt, vtt = [], ["WEBVTT", ""]
        for n, c in enumerate(caps, 1):
            text = " ".join(w["w"] for w in c["words"])
            srt += [str(n), f"{t(c['start'], ',')} --> {t(c['start'] + c['duration'], ',')}", text, ""]
            vtt += [f"{t(c['start'], '.')} --> {t(c['start'] + c['duration'], '.')}", text, ""]
        return "\n".join(srt), "\n".join(vtt)

    # ------------------------------------------------------------------ full render
    def render(self, out: Path) -> dict:
        tracks = self.tl["tracks"]
        main = next(t for t in tracks if t["kind"] == "video")
        if not main["items"]:
            raise RenderError("The main track is empty")
        total = len(main["items"]) + sum(len(t["items"]) for t in tracks if t["kind"] == "overlay") + 2
        done = 0
        items = main["items"]
        # Transition into item i (i>0): xfade over d seconds using a tail handle on item i-1.
        trans = [None] + [(xfade_name(as_obj(it.get("transitionIn")).get("type")),
                           min(3.0, float(as_obj(it.get("transitionIn")).get("duration", 0.4) or 0.4)))
                          for it in items[1:]]
        trans = [t if t and t[0] else None for t in trans]
        segs = []
        for i, it in enumerate(items):
            tail = trans[i + 1][1] if i + 1 < len(items) and trans[i + 1] else 0.0
            segs.append(self.segment(it, self.work / f"seg_{i:04d}.mp4", tail=tail, first=(i == 0)))
            done += 1
            self.progress(done / total, f"segment {i + 1}/{len(items)}")
        base = self.work / "base.mp4"
        if not any(trans):
            listfile = self.work / "concat.txt"
            listfile.write_text("".join(f"file '{p.name}'\n" for p in segs))
            run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listfile),
                 "-c", "copy", str(base)])
        else:
            # Sequential xfade/acrossfade (or plain concat at cuts). Offsets keep every clip at its timeline start.
            args, graph, t = [], [], float(items[0]["duration"])
            for k, p_ in enumerate(segs):
                args += ["-i", str(p_)]
                # One timebase/format for every input so xfade and concat can be chained.
                graph.append(f"[{k}:v]setpts=PTS-STARTPTS,fps={self.fps},format=yuv420p,settb=AVTB[nv{k}]")
                graph.append(f"[{k}:a]asetpts=PTS-STARTPTS,aresample=48000[na{k}]")
            v, a = "[nv0]", "[na0]"
            for i in range(1, len(segs)):
                tr = trans[i]
                if tr:
                    kind, d = tr
                    graph.append(f"{v}[nv{i}]xfade=transition={kind}:duration={d:.3f}:offset={t:.3f},settb=AVTB[xv{i}]")
                    graph.append(f"{a}[na{i}]acrossfade=d={d:.3f}[xa{i}]")
                else:
                    graph.append(f"{v}{a}[nv{i}][na{i}]concat=n=2:v=1:a=1[cv{i}][xa{i}]")
                    graph.append(f"[cv{i}]settb=AVTB[xv{i}]")
                v, a = f"[xv{i}]", f"[xa{i}]"
                t += float(items[i]["duration"])
            run([ffmpeg_bin(), "-y", "-loglevel", "error", *args, "-filter_complex", ";".join(graph),
                 "-map", v, "-map", a, *self._enc(), str(base)])
        duration = sum(float(it["duration"]) for it in main["items"])

        inputs = ["-i", str(base)]
        vchain, achains, n = "[0:v]null[v0]", [], 1
        cur = "v0"
        overlay_audio: list[tuple[int, dict]] = []
        main_id = main["id"]
        for tr in tracks:
            # Every non-main video/overlay track is a free layer, composited bottom → top in track order.
            if tr["kind"] not in {"overlay", "video"} or tr["id"] == main_id or tr.get("hidden"):
                continue
            for j, it in enumerate(tr["items"]):
                if it.get("type") != "clip":
                    continue
                s, e = it["start"], it["start"] + it["duration"]
                tf = as_obj(it.get("transform"))
                scale = float(tf.get("scale", 1) or 1)
                opacity = float(it.get("opacity", 1) if it.get("opacity") is not None else 1)
                done += 1
                self.progress(done / total, "overlays")
                m = self.media.get(it["assetId"]) or {}
                pip = scale < 0.999 or (it.get("mediaKind") == "image" and tf) or any(
                    tf.get(k) for k in ("rotation", "crop", "flipH", "flipV"))
                is_video = it.get("mediaKind") != "image"
                if is_video:
                    inputs += ["-i", str(self.segment(it, self.work / f"ov_{tr['id']}_{j:04d}.mp4", layer=True))]
                    if float(it.get("volume", 0) or 0) > 0 and not tr.get("muted"):
                        overlay_audio.append((n, {**it, "volume": float(it["volume"]) * float(tr.get("volume", 1))}))
                else:
                    inputs += ["-loop", "1", "-t", f"{it['duration']:.3f}", "-i", m.get("originalUrl") or m.get("url")]
                pre = transform_filters(tf, None if is_video else it.get("effects"), self.W)
                key = next((e_ for e_ in (it.get("effects") or []) if isinstance(e_, dict) and e_.get("type") == "chromaKey"), None)
                if key:
                    kc, _ = ass_color(key.get("color"), "#00FF00")
                    bgr = kc[2:8]
                    pre.insert(0, f"chromakey=0x{bgr[4:6]}{bgr[2:4]}{bgr[0:2]}:{_f(key, 'similarity', 0.3):.3f}:{_f(key, 'smoothness', 0.1):.3f}")
                chain = ",".join(["format=rgba", *pre])
                if pip:
                    # Picture-in-picture (logos, inserts): positioned/scaled/rotated, keeps alpha.
                    w = even(self.W * scale)
                    x, y = float(tf.get("x", 0.5)) * self.W, float(tf.get("y", 0.5)) * self.H
                    vchain += (f";[{n}:v]{chain},scale={w}:-2,colorchannelmixer=aa={opacity:.3f},"
                               f"setpts=PTS-STARTPTS+{s:.3f}/TB[o{n}];"
                               f"[{cur}][o{n}]overlay=x='{x:.1f}-w/2':y='{y:.1f}-h/2':eof_action=pass:"
                               f"enable='between(t,{s:.3f},{e:.3f})'[v{n}]")
                else:
                    vchain += (f";[{n}:v]{chain},scale={self.W}:{self.H},colorchannelmixer=aa={opacity:.3f},"
                               f"setpts=PTS-STARTPTS+{s:.3f}/TB[o{n}];"
                               f"[{cur}][o{n}]overlay=0:0:eof_action=pass:enable='between(t,{s:.3f},{e:.3f})'[v{n}]")
                cur = f"v{n}"
                n += 1
        ass = self.work / "text.ass"
        if self.ass(ass):
            vchain += f";[{cur}]subtitles=filename='{ass}':fontsdir='{FONTS_DIR}'[vout]"
        else:
            vchain += f";[{cur}]null[vout]"

        dialogue = "[0:a]"
        mains = [f"[0:a]volume={float(main.get('volume', 1)) if not main.get('muted') else 0:.3f}[dlg]"]
        music_in = []
        for tr in tracks:
            if tr["kind"] != "audio" or tr.get("muted"):
                continue
            for it in tr["items"]:
                m = self.media.get(it["assetId"])
                if not m:
                    continue
                url = m.get("audioUrl") or m["url"]
                sp = float(it.get("speed") or 1)
                inputs += ["-ss", f"{float(it.get('srcIn', 0)):.3f}", "-t", f"{float(it['duration']) * sp:.3f}", "-i", url]
                vol = float(tr.get("volume", 1)) * float(it.get("volume", 1))
                d_ = float(it["duration"])
                fo = float(it["fadeOut"]) if it.get("fadeOut") is not None else (1.5 if tr.get("audioRole", "music") == "music" else 0)
                fi = float(it.get("fadeIn") or 0)
                fade = (f",afade=t=in:st=0:d={fi:.3f}" if fi else "") + (f",afade=t=out:st={max(0.0, d_ - fo):.3f}:d={fo:.3f}" if fo else "")
                fx = "".join("," + f for f in audio_effects(it.get("effects")))
                tempo = f",{atempo_chain(sp)}" if sp != 1 else ""
                ms = int(it["start"] * 1000)
                achains.append(f"[{n}:a]aresample=48000{tempo},volume={vol:.3f}{fx}{fade},atrim=0:{d_:.3f},adelay={ms}|{ms}[m{n}]")
                music_in.append((f"m{n}", tr.get("audioRole", "music")))
                n += 1
        for k_, it in overlay_audio:  # B-roll natural sound (overlay clips with volume > 0)
            ms = int(float(it["start"]) * 1000)
            achains.append(f"[{k_}:a]aresample=48000,volume={float(it['volume']):.3f},adelay={ms}|{ms}[m{k_}x]")
            music_in.append((f"m{k_}x", "sfx"))
        afilter = ";".join(mains + achains)
        if music_in:
            music = [k for k, role in music_in if role == "music"]
            other = [k for k, role in music_in if role != "music"]
            if music:
                afilter += ";" + "".join(f"[{k}]" for k in music) + f"amix=inputs={len(music)}:normalize=0[mus]"
                dk = as_obj(self.tl["settings"].get("ducking"))
                if dk.get("enabled", True):
                    afilter += (f";[dlg]asplit=2[dlg1][sc];[mus][sc]sidechaincompress=threshold={_f(dk, 'threshold', 0.03):.3f}:"
                                f"ratio={_f(dk, 'ratio', 8):.1f}:attack={_f(dk, 'attack', 20):.0f}:release={_f(dk, 'release', 400):.0f}[duck]")
                else:
                    afilter += ";[dlg]anull[dlg1];[mus]anull[duck]"
                parts = ["[dlg1]", "[duck]"] + [f"[{k}]" for k in other]
            else:
                parts = ["[dlg]"] + [f"[{k}]" for k in other]
            afilter += ";" + "".join(parts) + f"amix=inputs={len(parts)}:normalize=0:duration=first[mix]"
        else:
            afilter += ";[dlg]anull[mix]"
        loud = float(self.tl["settings"].get("loudness", -14))
        afilter += f";[mix]loudnorm=I={loud}:TP=-1.5:LRA=11,aresample=48000[aout]"
        self.progress(done / total, "compositing")
        run([ffmpeg_bin(), "-y", "-loglevel", "error", *inputs, "-filter_complex", f"{vchain};{afilter}",
             "-map", "[vout]", "-map", "[aout]", "-t", f"{duration:.3f}", *self._enc(), "-movflags", "+faststart",
             str(out)])
        del dialogue
        return {"durationSec": duration, "width": self.W, "height": self.H, "fps": self.fps}


# ------------------------------------------------------------------ stills (agent self-check / thumbnails)
def _active(items: list[dict], t: float) -> dict | None:
    return next((i for i in items if float(i["start"]) <= t < float(i["start"]) + float(i["duration"])), None)


def _shift(item: dict, off: float) -> dict:
    it = {**item, "start": float(item["start"]) + off}
    if it.get("type") == "caption":
        it["words"] = [{**w, "s": float(w["s"]) + off, "e": float(w["e"]) + off} for w in item.get("words", [])]
    return it


class StillRenderer(Renderer):
    """Composites single frames with the same layout filters, overlay rules and ASS text as `render()`."""

    def _layer(self, item: dict, t: float, out: Path, layer: bool = False) -> Path:
        W, H = self.W, self.H
        m = self.media.get(item["assetId"]) or {}
        if not m.get("url"):
            raise RenderError(f"Missing media for asset {item['assetId']}")
        if item.get("mediaKind") == "image":
            url = (m.get("images") or {}).get(item.get("page", 0)) or m["url"]
            kb = item.get("kenBurns") or {"from": {"x": .5, "y": .5, "scale": 1}, "to": {"x": .5, "y": .5, "scale": 1}}
            u = min(1.0, max(0.0, (t - float(item["start"])) / max(0.01, float(item["duration"]))))
            z = kb["from"]["scale"] + (kb["to"]["scale"] - kb["from"]["scale"]) * u
            fx = kb["from"]["x"] + (kb["to"]["x"] - kb["from"]["x"]) * u
            fy = kb["from"]["y"] + (kb["to"]["y"] - kb["from"]["y"]) * u
            zw, zh = even(W * z), even(H * z)
            vf = (f"scale={zw}:{zh}:force_original_aspect_ratio=increase,crop={zw}:{zh},"
                  f"crop={W}:{H}:{(zw - W) * fx:.0f}:{(zh - H) * fy:.0f}")
            run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", url, "-vf", vf, "-frames:v", "1", str(out)])
            return out
        rel = (t - float(item["start"])) * float(item.get("speed") or 1)
        mode = item.get("mode") or "normal"
        st = (float(item.get("srcIn", 0)) if mode == "freeze"
              else float(item.get("srcOut", 0)) - rel if mode == "reverse" else float(item.get("srcIn", 0)) + rel)
        reframe = as_obj(item.get("reframe"))
        layout = reframe.get("layout") or "crop"
        graph = layout_filter(layout, int(m.get("width") or W), int(m.get("height") or H), W, H,
                              reframe.get("path") or [], st, {"padColor": self.tl["settings"].get("background") or "black", **reframe})
        extra = transform_filters({} if layer else as_obj(item.get("transform")), item.get("effects"), W, rotate_canvas=True)
        if extra:
            graph = graph[:-3] + "," + ",".join(extra) + "[v]"
        run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{max(0.0, st):.3f}", "-i", m["url"],
             "-filter_complex", graph, "-map", "[v]", "-frames:v", "1", str(out)])
        return out

    def still(self, t: float, out: Path, width: int = 540) -> Path:
        W, H = self.W, self.H
        tracks = self.tl["tracks"]
        main = next((x for x in tracks if x["kind"] == "video"), {"items": []})
        base = self.work / f"st_{t:.3f}_main.png"
        it = _active(main["items"], t)
        if it:
            self._layer(it, t, base)
        else:
            bg = str(self.tl["settings"].get("background") or "#000000")
            run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={bg}:s={W}x{H}",
                 "-frames:v", "1", str(base)])
        inputs, chain, cur, n = ["-i", str(base)], "[0:v]null[v0]", "v0", 1
        for tr in tracks:
            if tr["kind"] not in {"overlay", "video"} or tr is main or tr.get("hidden"):
                continue
            o = _active([x for x in tr["items"] if x.get("type") == "clip"], t)
            if not o:
                continue
            tf = as_obj(o.get("transform"))
            scale = float(tf.get("scale", 1) or 1)
            opacity = float(o.get("opacity", 1) if o.get("opacity") is not None else 1)
            pip = scale < 0.999 or (o.get("mediaKind") == "image" and tf) or any(
                tf.get(k) for k in ("rotation", "crop", "flipH", "flipV"))
            m = self.media.get(o["assetId"]) or {}
            if pip and o.get("mediaKind") == "image":
                src = m.get("originalUrl") or m.get("url")
            else:
                src = str(self._layer(o, t, self.work / f"st_{t:.3f}_ov{n}.png", layer=True))
            inputs += ["-i", src]
            pre = transform_filters(tf, o.get("effects") if o.get("mediaKind") == "image" else None, W)
            key = next((e_ for e_ in (o.get("effects") or []) if isinstance(e_, dict) and e_.get("type") == "chromaKey"), None)
            if key:
                kc, _ = ass_color(key.get("color"), "#00FF00")
                bgr = kc[2:8]
                pre.insert(0, f"chromakey=0x{bgr[4:6]}{bgr[2:4]}{bgr[0:2]}:{_f(key, 'similarity', 0.3):.3f}:{_f(key, 'smoothness', 0.1):.3f}")
            pre_s = ",".join(["format=rgba", *pre])
            if pip:
                w = even(W * scale)
                x, y = float(tf.get("x", 0.5)) * W, float(tf.get("y", 0.5)) * H
                chain += (f";[{n}:v]{pre_s},scale={w}:-2,colorchannelmixer=aa={opacity:.3f}[o{n}];"
                          f"[{cur}][o{n}]overlay=x='{x:.1f}-w/2':y='{y:.1f}-h/2'[v{n}]")
            else:
                chain += (f";[{n}:v]{pre_s},scale={W}:{H},colorchannelmixer=aa={opacity:.3f}[o{n}];"
                          f"[{cur}][o{n}]overlay=0:0[v{n}]")
            cur, n = f"v{n}", n + 1
        comp = self.work / f"st_{t:.3f}_comp.png"
        run([ffmpeg_bin(), "-y", "-loglevel", "error", *inputs, "-filter_complex", chain, "-map", f"[{cur}]",
             "-frames:v", "1", str(comp)])
        # Text/captions: shift the active items so the earliest starts at 0.5 s, then grab the frame that corresponds
        # to t (keeps animation timing exact without rendering the whole timeline).
        act = [i for tr in tracks if tr["kind"] in {"text", "caption"} for i in tr["items"] if _active([i], t)]
        off = 0.5 - min([float(i["start"]) for i in act] or [t])
        grab = t + off
        text_tl = {**self.tl, "tracks": [
            {**tr, "items": [_shift(i, off) for i in tr["items"] if _active([i], t)]}
            if tr["kind"] in {"text", "caption"} else {**tr, "items": []} for tr in tracks]}
        ass = self.work / f"st_{t:.3f}.ass"
        r = Renderer(text_tl, self.media, self.work, "final" if self.q is PRESETS["final"] else "preview")
        vf = f"scale={even(width)}:-2"
        if r.ass(ass):
            vf = f"subtitles=filename='{ass}':fontsdir='{FONTS_DIR}',{vf}"
        run([ffmpeg_bin(), "-y", "-loglevel", "error", "-loop", "1", "-framerate", "10", "-t", f"{grab + 0.5:.3f}",
             "-i", str(comp), "-vf", vf, "-ss", f"{grab:.3f}", "-frames:v", "1", "-q:v", "4", str(out)])
        return out


def render_stills(timeline: dict, media: dict[str, dict], times: list[float], width: int = 540) -> list[bytes]:
    work = Path(tempfile.mkdtemp(prefix="still-"))
    try:
        r = StillRenderer(timeline, media, work, "preview")
        outs = []
        for k, t in enumerate(times):
            p_ = r.still(float(t), work / f"out_{k:02d}.jpg", width)
            outs.append(p_.read_bytes())
        return outs
    finally:
        shutil.rmtree(work, ignore_errors=True)


def render_timeline(timeline: dict, media: dict[str, dict], out_dir: Path, quality: str,
                    progress: Callable[[float, str], None] | None = None) -> dict:
    work = Path(tempfile.mkdtemp(prefix="render-"))
    try:
        r = Renderer(timeline, media, work, quality, progress)
        mp4 = out_dir / "video.mp4"
        info = r.render(mp4)
        srt, vtt = r.subtitles()
        (out_dir / "captions.srt").write_text(srt, encoding="utf-8")
        (out_dir / "captions.vtt").write_text(vtt, encoding="utf-8")
        at = float(timeline.get("meta", {}).get("thumbnailAt") or min(1.0, info["durationSec"] / 3))
        run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(mp4), "-frames:v", "1",
             "-q:v", "3", str(out_dir / "thumb.jpg")])
        return info
    finally:
        shutil.rmtree(work, ignore_errors=True)
