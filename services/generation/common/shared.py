"""Shared helpers for the SageMaker async inference handlers (copied into each model's code/ at build time)."""
import base64
import io
import json
import os
import urllib.request
from urllib.parse import urlparse

import torch


def device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_json(request_body, content_type):
    if isinstance(request_body, (bytes, bytearray)):
        request_body = request_body.decode()
    return json.loads(request_body)


def fetch(url_or_b64: str) -> bytes:
    """Reference inputs: presigned S3 HTTPS URLs from the app, or base64. Nothing else is fetched."""
    if url_or_b64.startswith(("http:", "https:", "file:", "ftp:")) or "://" in url_or_b64[:16]:
        u = urlparse(url_or_b64)
        if u.scheme != "https" or not (u.hostname or "").endswith(".amazonaws.com"):
            raise ValueError("Only presigned S3 https URLs are accepted")
        with urllib.request.urlopen(url_or_b64, timeout=120) as r:  # nosec B310 - scheme/host validated above
            return r.read()
    return base64.b64decode(url_or_b64)


def load_image(url_or_b64: str):
    from PIL import Image
    return Image.open(io.BytesIO(fetch(url_or_b64))).convert("RGB")


def png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def dims(aspect: str, long_side: int) -> tuple[int, int]:
    a = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1), "4:5": (4, 5), "4:3": (4, 3)}.get(aspect, (9, 16))
    if a[0] >= a[1]:
        w = long_side
        h = int(long_side * a[1] / a[0])
    else:
        h = long_side
        w = int(long_side * a[0] / a[1])
    return w - w % 16, h - h % 16


MODEL_ID = os.environ.get("MODEL_ID", "")
