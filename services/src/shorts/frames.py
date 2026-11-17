"""Frame sampling for the agent ("look") and reframing analysis.

Runs in Lambda: ffmpeg comes from the `imageio-ffmpeg` wheel. Frames are pulled from the 720p proxy through a
presigned URL with input seeking, so only the needed byte ranges are downloaded.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from . import safeurl


def ffmpeg_bin() -> str:
    explicit = os.environ.get("FFMPEG_BIN")
    if explicit:
        return explicit
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def frame_at(url: str, t: float, width: int = 480) -> bytes:
    # Inputs are only presigned S3 HTTPS URLs we created; args are a list (no shell), so no injection surface.
    safeurl.check(url)
    out = subprocess.run(  # nosec B603
        [ffmpeg_bin(), "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", url, "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "4", "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
        check=True, capture_output=True, timeout=60)
    return out.stdout


def frames(url: str, times: list[float], width: int = 480) -> list[bytes]:
    return [frame_at(url, t, width) for t in times]


def contact_sheet(url: str, times: list[float], cols: int = 4, tile_width: int = 320) -> bytes:
    """Grid of frames (row-major, in the order of `times`). Timestamps are returned separately as text."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i, t in enumerate(times):
            p = Path(d) / f"f{i:03d}.jpg"
            p.write_bytes(frame_at(url, t, tile_width))
            paths.append(p)
        rows = (len(paths) + cols - 1) // cols
        out = Path(d) / "sheet.jpg"
        # nosec B603 - list args, temp paths only
        subprocess.run([ffmpeg_bin(), "-loglevel", "error", "-y", "-framerate", "1", "-i", str(Path(d) / "f%03d.jpg"),
                        "-vf", f"tile={cols}x{rows}:padding=4:color=black", "-frames:v", "1", "-q:v", "4", str(out)],
                       check=True, capture_output=True, timeout=60)
        return out.read_bytes()


def spread(start: float, end: float, n: int) -> list[float]:
    if n <= 1 or end - start < 0.5:
        return [round((start + end) / 2, 3)]
    step = (end - start) / n
    return [round(start + step * (i + 0.5), 3) for i in range(n)]
