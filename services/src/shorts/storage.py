"""S3 key layout and presigned URL helpers.

  tenants/{t}/assets/{a}/original/{filename}   upload target (triggers ingest)
  tenants/{t}/uploads/{a}/...                  chat/project-scoped uploads use the same path
  content/{ck}/...                             content-addressed derivatives (shared by duplicates)
  renders/{t}/{projectId}/{renderId}.mp4       final renders
"""
from __future__ import annotations

import re
from functools import lru_cache

import boto3
from botocore.config import Config as BotoConfig

from .config import get_config

ORIGINAL_RE = re.compile(r"^tenants/(?P<tenant>[^/]+)/assets/(?P<asset>[^/]+)/original/(?P<name>.+)$")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


@lru_cache(maxsize=1)
def s3():
    # when_required: presigned part URLs must not demand SDK checksum headers the browser will not send.
    return boto3.client("s3", config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "virtual"},
                                                request_checksum_calculation="when_required",
                                                response_checksum_validation="when_required"))


def safe_filename(name: str) -> str:
    base = SAFE_NAME_RE.sub("_", name.rsplit("/", 1)[-1]).strip("._") or "file"
    return base[:120]


def original_key(tenant_id: str, asset_id: str, filename: str) -> str:
    return f"tenants/{tenant_id}/assets/{asset_id}/original/{safe_filename(filename)}"


def content_prefix(content_key: str) -> str:
    return f"content/{content_key}"


def parse_original_key(key: str) -> dict | None:
    m = ORIGINAL_RE.match(key)
    return m.groupdict() if m else None


def presign_get(key: str, expires: int = 3600, download_name: str | None = None) -> str:
    params = {"Bucket": get_config().media_bucket, "Key": key}
    if download_name:
        params["ResponseContentDisposition"] = f'attachment; filename="{safe_filename(download_name)}"'
    return s3().generate_presigned_url("get_object", Params=params, ExpiresIn=expires)


def s3_uri(key: str) -> str:
    return f"s3://{get_config().media_bucket}/{key}"
