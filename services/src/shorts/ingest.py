"""Event-driven ingest coordination (no Step Functions).

Two layers of analysis per content key (ck = content hash):
  base  (language-independent, shared by every duplicate upload)
        CONTENT#{ck} / META      stages: marengo:N, transcribe:N, image:N
  lang  (follows the workspace language; produced once per language)
        CONTENT#{ck} / LANG#{l}  stages: pegasus.summary, pegasus.segments:N, caption:N, describe

Flow:
  S3 ObjectCreated ──▶ on_object_created ──▶ (dedupe) or Fargate media worker "prepare"
  prepare.done     ──▶ on_prepared ──▶ base tasks + lang tasks for every waiting language
  stage.done       ──▶ on_stage_done ──▶ (lang: after pegasus/caption → describe) ──▶ maybe_finalize
  maybe_finalize   ──▶ when base ready AND lang ready: attach vectors to tenant index, assets ready
"""
from __future__ import annotations

import base64
import json
import logging
from functools import lru_cache

import boto3

from . import ddb, storage, vectors
from .config import get_config

log = logging.getLogger(__name__)

TERMINAL = {"done", "failed", "skipped"}
DEFAULT_LANGUAGE = "ko"
LANGUAGES = {"ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese", "es": "Spanish", "fr": "French",
             "de": "German", "pt": "Portuguese", "vi": "Vietnamese", "th": "Thai", "id": "Indonesian"}


@lru_cache(maxsize=1)
def sqs():
    return boto3.client("sqs")


@lru_cache(maxsize=1)
def ecs():
    return boto3.client("ecs")


def send(queue_url: str, body: dict, delay: int = 0) -> None:
    sqs().send_message(QueueUrl=queue_url, MessageBody=json.dumps(body), DelaySeconds=min(delay, 900))


def send_model_task(task: dict) -> None:
    send(get_config().model_queue_url, task)


def send_ingest_event(event: dict, delay: int = 0) -> None:
    send(get_config().ingest_queue_url, event, delay)


def tenant_language(tenant_id: str) -> str:
    tenant = ddb.get(f"TENANT#{tenant_id}", "META") or {}
    lang = tenant.get("language") or DEFAULT_LANGUAGE
    return lang if lang in LANGUAGES else DEFAULT_LANGUAGE


# ------------------------------------------------------------------ content keys
def content_key_from_s3(bucket: str, key: str) -> tuple[str | None, int]:
    head = storage.s3().head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
    size = head["ContentLength"]
    crc = head.get("ChecksumCRC64NVME")
    if crc and head.get("ChecksumType", "FULL_OBJECT") == "FULL_OBJECT":
        return f"c64-{base64.b64decode(crc).hex()}-{size}", size
    return None, size  # media worker computes sha256


# ------------------------------------------------------------------ asset status
def set_asset_status(asset_id: str, status: str, **extra) -> None:
    ddb.update(f"ASSET#{asset_id}", "META", {"status": status, "updatedAt": ddb.now_ms(), **extra})


def link_asset_to_content(asset: dict, content_key: str) -> None:
    ddb.update(f"ASSET#{asset['assetId']}", "META", {
        "contentKey": content_key,
        "analysisLanguage": tenant_language(asset["tenantId"]),
        "GSI3PK": f"CONTENT#{content_key}",
        "GSI3SK": f"TENANT#{asset['tenantId']}#ASSET#{asset['assetId']}",
        "updatedAt": ddb.now_ms(),
    })


def _create_item(pk: str, sk: str, fields: dict) -> bool:
    try:
        ddb.put({"PK": pk, "SK": sk, "stages": {}, "createdAt": ddb.now_ms(), **fields},
                condition="attribute_not_exists(PK)")
        return True
    except Exception as exc:
        if ddb.conditional_failed(exc):
            return False
        raise


# ------------------------------------------------------------------ upload / prepare
def on_object_created(bucket: str, key: str) -> None:
    parsed = storage.parse_original_key(key)
    if not parsed:
        log.info("Ignoring non-original key %s", key)
        return
    asset = ddb.get(f"ASSET#{parsed['asset']}", "META", consistent=True)
    if not asset or asset.get("tenantId") != parsed["tenant"]:
        log.warning("Object %s has no matching asset; ignoring", key)
        return
    content_key, size = content_key_from_s3(bucket, key)
    ddb.update(f"ASSET#{asset['assetId']}", "META", {"sizeBytes": size, "originalKey": key})
    if content_key:
        link_asset_to_content(asset, content_key)
        content = ddb.get(f"CONTENT#{content_key}", "META", consistent=True)
        if content and content.get("status") in {"preparing", "analyzing", "ready"}:
            set_asset_status(asset["assetId"], "processing", kind=content.get("kind", asset.get("kind")))
            if content.get("manifest"):
                ensure_language(content_key, tenant_language(asset["tenantId"]))
            maybe_finalize(content_key)
            return
        _create_item(f"CONTENT#{content_key}", "META", {"contentKey": content_key, "status": "preparing"})
    set_asset_status(asset["assetId"], "processing")
    run_media_worker({"job": "prepare", "assetId": asset["assetId"], "tenantId": asset["tenantId"],
                      "bucket": bucket, "key": key, "contentKey": content_key,
                      "filename": asset.get("filename", ""), "contentType": asset.get("contentType", "")})


def run_media_worker(job: dict) -> None:
    cfg = get_config()
    ecs().run_task(
        cluster=cfg.media_worker_cluster,
        taskDefinition=cfg.media_worker_task_def,
        launchType="FARGATE",
        count=1,
        networkConfiguration={"awsvpcConfiguration": {
            "subnets": cfg.media_worker_subnets,
            "securityGroups": [cfg.media_worker_security_group],
            "assignPublicIp": "DISABLED",
        }},
        overrides={"containerOverrides": [{"name": "worker", "environment": [
            {"name": "JOB", "value": json.dumps(job)}]}]},
    )


def on_prepared(event: dict) -> None:
    """Media worker finished. event: {assetId, contentKey, manifest}"""
    ck, manifest = event["contentKey"], event["manifest"]
    asset = ddb.get(f"ASSET#{event['assetId']}", "META", consistent=True)
    if asset and asset.get("contentKey") != ck:
        link_asset_to_content(asset, ck)  # sha256 path: key computed by the worker

    _create_item(f"CONTENT#{ck}", "META", {"contentKey": ck, "status": "preparing"})
    existing = ddb.get(f"CONTENT#{ck}", "META", consistent=True)
    if existing.get("manifest"):  # another upload of the same bytes already prepared it
        _copy_manifest_to_assets(ck, existing["manifest"])
        for lang in _waiting_languages(ck):
            ensure_language(ck, lang)
        maybe_finalize(ck)
        return

    kind = manifest["kind"]
    stages: dict[str, str] = {}
    tasks: list[dict] = []
    for chunk in manifest.get("chunks", []):
        i = chunk["index"]
        if kind in {"video", "audio"}:
            stages[f"marengo:{i}"] = "pending"
            tasks.append({"task": "marengo.start", "contentKey": ck, "chunk": i, "inputType": kind})
        if chunk.get("audioKey"):
            stages[f"transcribe:{i}"] = "pending"
            tasks.append({"task": "transcribe.start", "contentKey": ck, "chunk": i})
    for page in manifest.get("images", []):
        stages[f"image:{page['index']}"] = "pending"
        tasks.append({"task": "image.analyze", "contentKey": ck, "image": page["index"]})

    ddb.update(f"CONTENT#{ck}", "META", {
        "status": "analyzing" if stages else "ready",
        "kind": kind, "manifest": manifest, "stages": stages,
        "durationSec": manifest.get("durationSec"), "updatedAt": ddb.now_ms(),
    })
    _copy_manifest_to_assets(ck, manifest)
    for t in tasks:
        send_model_task(t)
    for lang in _waiting_languages(ck) or [tenant_language(asset["tenantId"]) if asset else DEFAULT_LANGUAGE]:
        ensure_language(ck, lang)
    maybe_finalize(ck)


def _waiting_languages(ck: str) -> list[str]:
    return sorted({a.get("analysisLanguage") or tenant_language(a["tenantId"])
                   for a in ddb.query(f"CONTENT#{ck}", index="GSI3")})


def ensure_language(ck: str, lang: str) -> None:
    """Start the language layer for `lang` once (idempotent)."""
    content = ddb.get(f"CONTENT#{ck}", "META", consistent=True)
    manifest = (content or {}).get("manifest")
    if not manifest:
        return
    kind = manifest["kind"]
    stages: dict[str, str] = {}
    tasks: list[dict] = []
    if kind == "video":
        stages["pegasus.summary"] = "pending"
        tasks.append({"task": "pegasus.summary", "contentKey": ck, "lang": lang})
        for chunk in manifest.get("chunks", []):
            stages[f"pegasus.segments:{chunk['index']}"] = "pending"
            tasks.append({"task": "pegasus.segments", "contentKey": ck, "chunk": chunk["index"], "lang": lang})
    elif kind == "image":
        for page in manifest.get("images", []):
            stages[f"caption:{page['index']}"] = "pending"
            tasks.append({"task": "image.caption", "contentKey": ck, "image": page["index"], "lang": lang})
    created = _create_item(f"CONTENT#{ck}", f"LANG#{lang}", {
        "contentKey": ck, "language": lang, "status": "analyzing" if stages else "ready", "stages": stages})
    if created:
        try:
            for t in tasks:
                send_model_task(t)
        except Exception:
            ddb.delete(f"CONTENT#{ck}", f"LANG#{lang}")  # let a retry re-dispatch cleanly
            raise


def _copy_manifest_to_assets(ck: str, manifest: dict) -> None:
    fields = _asset_fields(manifest)
    for a in ddb.query(f"CONTENT#{ck}", index="GSI3"):
        if a.get("status") != "ready":
            ddb.update(f"ASSET#{a['assetId']}", "META", {**fields, "status": "processing", "updatedAt": ddb.now_ms()})


def _asset_fields(manifest: dict) -> dict:
    return {k: manifest.get(k) for k in ("kind", "durationSec", "width", "height", "fps", "pageCount",
                                         "hasAudio", "thumbnailKey") if manifest.get(k) is not None}


def on_prepare_failed(event: dict) -> None:
    set_asset_status(event["assetId"], "failed", error=str(event.get("error", ""))[:1000])
    if event.get("contentKey"):
        ddb.update(f"CONTENT#{event['contentKey']}", "META", {"status": "failed", "updatedAt": ddb.now_ms(),
                                                               "lastError": str(event.get("error", ""))[:1000]})


# ------------------------------------------------------------------ stage completion
def on_stage_done(ck: str, stage: str, status: str = "done", error: str | None = None, lang: str | None = None) -> None:
    sk = f"LANG#{lang}" if lang else "META"
    item = ddb.update_raw(
        f"CONTENT#{ck}", sk,
        "SET stages.#s = :st, updatedAt = :now" + (", lastError = :err" if error else ""),
        names={"#s": stage},
        values={":st": status, ":now": ddb.now_ms(), **({":err": error[:1000]} if error else {})},
    )
    stages = item.get("stages", {})
    if lang:
        analysis = {k: v for k, v in stages.items() if k != "describe"}
        if not all(v in TERMINAL for v in analysis.values()):
            return
        if "describe" not in stages:
            try:
                ddb.update_raw(f"CONTENT#{ck}", sk, "SET stages.#d = :p", names={"#d": "describe"},
                               values={":p": "pending"}, condition="attribute_not_exists(stages.#d)")
                send_model_task({"task": "describe.embed", "contentKey": ck, "lang": lang})
            except Exception as exc:  # another completion won the race
                if not ddb.conditional_failed(exc):
                    raise
            return
        if stages["describe"] in TERMINAL:
            _mark_ready(ck, sk)
    elif all(v in TERMINAL for v in stages.values()):
        _mark_ready(ck, sk)
    maybe_finalize(ck)


def _mark_ready(ck: str, sk: str) -> None:
    try:
        ddb.update_raw(f"CONTENT#{ck}", sk, "SET #st = :ready, readyAt = :now", names={"#st": "status"},
                       values={":ready": "ready", ":now": ddb.now_ms()}, condition="#st <> :ready")
    except Exception as exc:
        if not ddb.conditional_failed(exc):
            raise


def maybe_finalize(ck: str) -> None:
    """Attach every waiting asset whose base + language layers are ready."""
    base = ddb.get(f"CONTENT#{ck}", "META", consistent=True)
    if not base or base.get("status") != "ready":
        return
    ready_langs = {i["language"]: i for i in ddb.query(f"CONTENT#{ck}", "LANG#") if i.get("status") == "ready"}
    for a in ddb.query(f"CONTENT#{ck}", index="GSI3"):
        lang = a.get("analysisLanguage") or tenant_language(a["tenantId"])
        if a.get("status") != "ready" and lang in ready_langs:
            attach_asset(a["assetId"], base, lang)


# ------------------------------------------------------------------ vectors
def load_content_vectors(ck: str, lang: str) -> list[dict]:
    """Base vectors (Marengo, images) + description vectors of one language."""
    prefix = f"{storage.content_prefix(ck)}/vectors/"
    out: list[dict] = []
    paginator = storage.s3().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=get_config().media_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            name = obj["Key"].rsplit("/", 1)[-1]
            if name.startswith("description") and name != f"description-{lang}.json":
                continue
            body = storage.s3().get_object(Bucket=get_config().media_bucket, Key=obj["Key"])["Body"].read()
            out.extend(json.loads(body))
    return out


def attach_asset(asset_id: str, content: dict, lang: str) -> None:
    """Make the content searchable in the asset's tenant index and mark the asset ready."""
    asset = ddb.get(f"ASSET#{asset_id}", "META", consistent=True)
    if not asset:
        return
    tenant, ck = asset["tenantId"], content["contentKey"]
    marker_sk = f"TENANT#{tenant}#LANG#{lang}"
    if not ddb.get(f"CONTENT#{ck}", marker_sk):
        count = vectors.put(tenant, load_content_vectors(ck, lang))
        ddb.put({"PK": f"CONTENT#{ck}", "SK": marker_sk, "vectorCount": count, "attachedAt": ddb.now_ms()})
    fields = _asset_fields(content.get("manifest", {}))
    summary = ddb.get(f"CONTENT#{ck}", f"ANALYSIS#summary#{lang}") or {}
    if summary.get("title") and not asset.get("titleEdited"):
        fields["aiTitle"] = summary["title"]
    if summary.get("summary"):
        fields["aiSummary"] = summary["summary"]
    set_asset_status(asset_id, "ready", analysisLanguage=lang, **fields)
