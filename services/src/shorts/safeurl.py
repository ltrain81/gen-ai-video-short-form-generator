"""HTTPS-only URL opening (no file:/ or custom schemes), with an optional host allowlist."""
from __future__ import annotations

import urllib.request
from urllib.parse import urlparse

AWS_HOST_SUFFIXES = (".amazonaws.com", ".api.aws", ".on.aws")


def check(url: str, allowed_suffixes: tuple[str, ...] | None = AWS_HOST_SUFFIXES) -> str:
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        raise ValueError("Only https URLs are allowed")
    if allowed_suffixes and not any(u.hostname == s.lstrip(".") or u.hostname.endswith(s) for s in allowed_suffixes):
        raise ValueError(f"Host not allowed: {u.hostname}")
    return url


def urlopen(req: urllib.request.Request | str, timeout: float = 60, allowed_suffixes: tuple[str, ...] | None = AWS_HOST_SUFFIXES):
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    check(url, allowed_suffixes)
    return urllib.request.urlopen(req, timeout=timeout)  # nosec B310 - scheme/host validated by check()
