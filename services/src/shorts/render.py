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

FONT = os.environ.get("RENDER_FONT", "Noto Sans CJK KR")
PRESETS = {"preview": {"scale": 0.5, "crf": 30, "preset": "veryfast", "abr": "96k"},
           "final": {"scale": 1.0, "crf": 19, "preset": "medium", "abr": "192k"}}


class RenderError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BIN", "ffmpeg")


def run(cmd: list[str], timeout: int = 7200) -> None:
    log.info("ffmpeg %s", " ".join(c if len(c) < 200 else c[:200] + "…" for c in cmd[1:]))
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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


def layout_filter(layout: str, src_w: int, src_h: int, W: int, H: int, path: list[dict], t0: float) -> str:
    """Video filter graph from [0:v] to [v] for one clip."""
    if layout == "fit_blur":
        return (f"[0:v]split=2[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur=24:2,eq=brightness=-0.12[bg];[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
    if layout == "letterbox":
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black[v]")
    if layout == "split":  # top: subject crop (speaker); bottom: whole frame (slide/board)
        top_h = even(H * 0.5)
        return (f"[0:v]split=2[a][b];[a]{crop_filter(src_w, src_h, W, top_h, path, t0)}[top];"
                f"[b]scale={W}:{H - top_h}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H - top_h}:(ow-iw)/2:(oh-ih)/2:color=black[bot];[top][bot]vstack[v]")
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

    def segment(self, item: dict, out: Path) -> Path:
        W, H, dur = self.W, self.H, float(item["duration"])
        m = self.media.get(item["assetId"])
        if not m:
            raise RenderError(f"Missing media for asset {item['assetId']}")
        fades = []
        tin = (item.get("transitionIn") or {})
        fade_in = float(item.get("fadeIn") or (tin.get("duration", 0) if tin.get("type") not in (None, "cut") else 0))
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
                          reframe.get("path") or [], src_in)
        v = v[:-3] + f",setpts=(PTS-STARTPTS)/{speed:.5f},{vpost}[v]"
        vol = float(item.get("volume", 1.0))
        afades = []
        if fade_in:
            afades.append(f"afade=t=in:st=0:d={fade_in:.3f}")
        if fade_out:
            afades.append(f"afade=t=out:st={max(0.0, dur - fade_out):.3f}:d={fade_out:.3f}")
        if m.get("hasAudio", True):
            a = f"[0:a]asetpts=PTS-STARTPTS,{atempo_chain(speed)},volume={vol:.3f}" + ("," + ",".join(afades) if afades else "") + \
                f",aresample=48000,apad,atrim=0:{dur:.3f}[a]"
        else:
            a = f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f}[a]"
        run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{src_in:.3f}", "-t", f"{src_out - src_in:.3f}",
             "-i", m["url"], "-filter_complex", f"{v};{a}", "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
             *self._enc(), str(out)])
        return out

    # ------------------------------------------------------------------ captions/text (ASS)
    def ass(self, path: Path) -> bool:
        W, H = self.W, self.H
        lines = []
        def col(c: str, alpha: int = 0) -> str:
            c = (c or "#FFFFFF").lstrip("#")
            if len(c) == 3:
                c = "".join(ch * 2 for ch in c)
            try:
                r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
            except ValueError:
                r = g = b = 255
            return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"

        def ts(t: float) -> str:
            t = max(0.0, t)
            return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"

        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")

        def style_tags(st: dict) -> str:
            size = float(st.get("size") or 0.05) * H
            stroke = as_obj(st.get("stroke"))
            outline = float(stroke.get("width", 0) or 0) * H
            tags = [f"\\fn{FONT}", f"\\fs{size:.1f}", f"\\b{1 if (st.get('weight') or 800) >= 600 else 0}",
                    f"\\1c{col(st.get('color'))}", f"\\3c{col(stroke.get('color', '#000000'))}",
                    f"\\bord{outline:.1f}"]
            sh = as_obj(st.get("shadow"))
            tags.append(f"\\shad{(sh or {}).get('blur', 0) / 4:.1f}" if sh else "\\shad0")
            if st.get("background"):
                tags += ["\\bord6", "\\3c&H59000000", f"\\4c{col('#000000', 0x59)}"]
            return "".join(tags)

        for tr in self.tl["tracks"]:
            if tr.get("hidden"):
                continue
            for it in tr["items"]:
                if it["type"] == "text":
                    pos = as_position(it.get("position"), 0.2)
                    anim = as_obj(it.get("animation"))
                    extra = "\\fad(180,180)" if anim.get("in") in ("fade", "pop") or anim.get("out") == "fade" else ""
                    if anim.get("in") == "pop":
                        extra += "\\fscx80\\fscy80\\t(0,160,\\fscx100\\fscy100)"
                    text = esc(it["text"].upper() if as_obj(it.get("style")).get("uppercase") else it["text"])
                    lines.append(f"Dialogue: 1,{ts(it['start'])},{ts(it['start'] + it['duration'])},Base,,0,0,0,,"
                                 f"{{\\an5\\pos({pos['x'] * W:.0f},{pos['y'] * H:.0f}){style_tags(as_obj(it.get('style')))}{extra}}}{text}")
                elif it["type"] == "caption" and it.get("words"):
                    st = as_obj(it.get("style"))
                    y = float(as_position(it.get("position"), 0.75)["y"]) * H
                    hl = st.get("highlightColor")
                    words = it["words"]
                    upper = st.get("uppercase")
                    fmt = lambda w: esc(w["w"].upper() if upper else w["w"])  # noqa: E731
                    if hl:  # one event per word interval with the active word highlighted (same as preview)
                        for k, w in enumerate(words):
                            end = words[k + 1]["s"] if k + 1 < len(words) else it["start"] + it["duration"]
                            start = it["start"] if k == 0 else w["s"]
                            body = " ".join(f"{{\\1c{col(hl)}}}{fmt(x)}{{\\1c{col(st.get('color'))}}}" if j == k else fmt(x)
                                            for j, x in enumerate(words))
                            lines.append(f"Dialogue: 0,{ts(start)},{ts(max(end, start + 0.05))},Base,,0,0,0,,"
                                         f"{{\\an5\\pos({W / 2:.0f},{y:.0f}){style_tags(st)}}}{body}")
                    else:
                        lines.append(f"Dialogue: 0,{ts(it['start'])},{ts(it['start'] + it['duration'])},Base,,0,0,0,,"
                                     f"{{\\an5\\pos({W / 2:.0f},{y:.0f}){style_tags(st)}}}{' '.join(fmt(w) for w in words)}")
        if not lines:
            return False
        header = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
                  "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                  "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
                  "MarginL, MarginR, MarginV, Encoding\n"
                  f"Style: Base,{FONT},{H * 0.05:.0f},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,5,"
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
        segs = []
        for i, it in enumerate(main["items"]):
            segs.append(self.segment(it, self.work / f"seg_{i:04d}.mp4"))
            done += 1
            self.progress(done / total, f"segment {i + 1}/{len(main['items'])}")
        listfile = self.work / "concat.txt"
        listfile.write_text("".join(f"file '{p.name}'\n" for p in segs))
        base = self.work / "base.mp4"
        run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listfile),
             "-c", "copy", str(base)])
        duration = sum(float(it["duration"]) for it in main["items"])

        inputs = ["-i", str(base)]
        vchain, achains, n = "[0:v]null[v0]", [], 1
        cur = "v0"
        for tr in tracks:
            if tr["kind"] != "overlay" or tr.get("hidden"):
                continue
            for j, it in enumerate(tr["items"]):
                seg = self.segment(it, self.work / f"ov_{tr['id']}_{j:04d}.mp4")
                done += 1
                self.progress(done / total, "b-roll")
                inputs += ["-i", str(seg)]
                s, e = it["start"], it["start"] + it["duration"]
                vchain += (f";[{n}:v]setpts=PTS-STARTPTS+{s:.3f}/TB[o{n}];"
                           f"[{cur}][o{n}]overlay=0:0:eof_action=pass:enable='between(t,{s:.3f},{e:.3f})'[v{n}]")
                cur = f"v{n}"
                n += 1
        ass = self.work / "text.ass"
        if self.ass(ass):
            vchain += f";[{cur}]subtitles=filename='{ass}'{':fontsdir=' + os.environ['RENDER_FONTS_DIR'] if os.environ.get('RENDER_FONTS_DIR') else ''}[vout]"
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
                inputs += ["-ss", f"{float(it.get('srcIn', 0)):.3f}", "-t", f"{float(it['duration']):.3f}", "-i", url]
                vol = float(tr.get("volume", 1)) * float(it.get("volume", 1))
                fade = f",afade=t=out:st={max(0.0, it['duration'] - 1.5):.3f}:d=1.5"
                ms = int(it["start"] * 1000)
                achains.append(f"[{n}:a]aresample=48000,volume={vol:.3f}{fade},adelay={ms}|{ms}[m{n}]")
                music_in.append((f"m{n}", tr.get("audioRole", "music")))
                n += 1
        afilter = ";".join(mains + achains)
        if music_in:
            music = [k for k, role in music_in if role == "music"]
            other = [k for k, role in music_in if role != "music"]
            if music:
                afilter += ";" + "".join(f"[{k}]" for k in music) + f"amix=inputs={len(music)}:normalize=0[mus]"
                afilter += ";[dlg]asplit=2[dlg1][sc];[mus][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]"
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
