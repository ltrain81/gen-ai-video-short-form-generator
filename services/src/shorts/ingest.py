"""Event-driven ingest coordination (no Step Functions).

Flow per uploaded object:
  S3 ObjectCreated ──▶ on_object_created
      ├─ content already analyzed (dedupe)?  ──▶ attach_vectors → asset ready
      └─ else ──▶ media worker "prepare" (Fargate: probe, proxies, chunks, pages, waveform)
  prepare.done ──▶ on_prepared ──▶ fan-out model tasks per chunk / page
      marengo.start (async; poller)  pegasus.summary  pegasus.segments  transcribe.start (poller)
      image.analyze (images / document pages)
  stage.done ──▶ on_stage_done ──▶ when every stage of the content is done:
      describe.embed (Pegasus text → Marengo text embeddings) ──▶ finalize
  finalize ──▶ attach_vectors for every asset that references the content ──▶ assets ready

State lives in DynamoDB: CONTENT#{ck}/META holds `stages` {name: pending|done|failed}.
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
        "GSI3PK": f"CONTENT#{content_key}",
        "GSI3SK": f"TENANT#{asset['tenantId']}#ASSET#{asset['assetId']}",
        "updatedAt": ddb.now_ms(),
    })


# ------------------------------------------------------------------ handlers
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
        if content and content.get("status") == "ready":
            attach_asset(asset["assetId"], content)
            return
        if content and content.get("status") in {"preparing", "analyzing"}:
            set_asset_status(asset["assetId"], "processing", kind=content.get("kind", asset.get("kind")))
            return  # finalize will attach this asset too
        _create_content(content_key)
    set_asset_status(asset["assetId"], "processing")
    run_media_worker({"job": "prepare", "assetId": asset["assetId"], "tenantId": asset["tenantId"],
                      "bucket": bucket, "key": key, "contentKey": content_key,
                      "filename": asset.get("filename", ""), "contentType": asset.get("contentType", "")})


def _create_content(content_key: str) -> bool:
    try:
        ddb.put({"PK": f"CONTENT#{content_key}", "SK": "META", "contentKey": content_key,
                 "status": "preparing", "stages": {}, "createdAt": ddb.now_ms()},
                condition="attribute_not_exists(PK)")
        return True
    except Exception as exc:
        if ddb.conditional_failed(exc):
            return False
        raise


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

    existing = ddb.get(f"CONTENT#{ck}", "META", consistent=True)
    if existing and existing.get("status") == "ready":
        attach_asset(event["assetId"], existing)
        return
    if existing and existing.get("status") == "analyzing":
        set_asset_status(event["assetId"], "processing", kind=existing.get("kind"))
        return
    if not existing:
        _create_content(ck)

    kind = manifest["kind"]
    stages: dict[str, str] = {}
    tasks: list[dict] = []
    if kind == "video":
        stages["pegasus.summary"] = "pending"
        tasks.append({"task": "pegasus.summary", "contentKey": ck})
    for chunk in manifest.get("chunks", []):
        i = chunk["index"]
        if kind == "video":
            stages[f"marengo:{i}"] = "pending"
            stages[f"pegasus.segments:{i}"] = "pending"
            tasks.append({"task": "marengo.start", "contentKey": ck, "chunk": i, "inputType": "video"})
            tasks.append({"task": "pegasus.segments", "contentKey": ck, "chunk": i})
        elif kind == "audio":
            stages[f"marengo:{i}"] = "pending"
            tasks.append({"task": "marengo.start", "contentKey": ck, "chunk": i, "inputType": "audio"})
        if chunk.get("audioKey"):
            stages[f"transcribe:{i}"] = "pending"
            tasks.append({"task": "transcribe.start", "contentKey": ck, "chunk": i})
    for page in manifest.get("images", []):
        stages[f"image:{page['index']}"] = "pending"
        tasks.append({"task": "image.analyze", "contentKey": ck, "image": page["index"],
                      "caption": kind == "image"})

    ddb.update(f"CONTENT#{ck}", "META", {
        "status": "analyzing" if stages else "ready",
        "kind": kind,
        "manifest": manifest,
        "stages": stages,
        "durationSec": manifest.get("durationSec"),
        "updatedAt": ddb.now_ms(),
    })
    _copy_manifest_to_assets(ck, manifest)
    if not stages:
        finalize(ck)
        return
    for t in tasks:
        send_model_task(t)


def _copy_manifest_to_assets(ck: str, manifest: dict) -> None:
    fields = {k: manifest.get(k) for k in ("kind", "durationSec", "width", "height", "fps", "pageCount",
                                           "hasAudio", "thumbnailKey") if manifest.get(k) is not None}
    for a in ddb.query(f"CONTENT#{ck}", index="GSI3"):
        ddb.update(f"ASSET#{a['assetId']}", "META", {**fields, "status": "processing", "updatedAt": ddb.now_ms()})


def on_stage_done(ck: str, stage: str, status: str = "done", error: str | None = None) -> None:
    item = ddb.update_raw(
        f"CONTENT#{ck}", "META",
        "SET stages.#s = :st, updatedAt = :now" + (", lastError = :err" if error else ""),
        names={"#s": stage},
        values={":st": status, ":now": ddb.now_ms(), **({":err": error[:1000]} if error else {})},
    )
    stages = item.get("stages", {})
    analysis_stages = {k: v for k, v in stages.items() if k != "describe"}
    if all(v in TERMINAL for v in analysis_stages.values()):
        if "describe" not in stages and item.get("kind") == "video":
            # Text embeddings of Pegasus outputs once every segment pass finished.
            try:
                ddb.update_raw(f"CONTENT#{ck}", "META", "SET stages.#d = :p",
                               names={"#d": "describe"}, values={":p": "pending"},
                               condition="attribute_not_exists(stages.#d)")
                send_model_task({"task": "describe.embed", "contentKey": ck})
            except Exception as exc:  # another stage completion won the race
                if not ddb.conditional_failed(exc):
                    raise
            return
        if stages.get("describe", "done") in TERMINAL:
            finalize(ck)


def finalize(ck: str) -> None:
    try:  # first finisher wins
        content = ddb.update_raw(f"CONTENT#{ck}", "META", "SET #st = :ready, readyAt = :now",
                                 names={"#st": "status"}, values={":ready": "ready", ":now": ddb.now_ms()},
                                 condition="#st <> :ready")
    except Exception as exc:
        if ddb.conditional_failed(exc):
            return
        raise
    for a in ddb.query(f"CONTENT#{ck}", index="GSI3"):
        attach_asset(a["assetId"], content)


# ------------------------------------------------------------------ vectors
def load_content_vectors(ck: str) -> list[dict]:
    """All normalized vectors for a content key (written by model tasks)."""
    prefix = f"{storage.content_prefix(ck)}/vectors/"
    out: list[dict] = []
    paginator = storage.s3().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=get_config().media_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            body = storage.s3().get_object(Bucket=get_config().media_bucket, Key=obj["Key"])["Body"].read()
            out.extend(json.loads(body))
    return out


def attach_asset(asset_id: str, content: dict) -> None:
    """Make the content searchable in the asset's tenant index and mark the asset ready."""
    asset = ddb.get(f"ASSET#{asset_id}", "META", consistent=True)
    if not asset:
        return
    tenant = asset["tenantId"]
    marker = ddb.get(f"CONTENT#{content['contentKey']}", f"TENANT#{tenant}")
    if not marker:
        count = vectors.put(tenant, load_content_vectors(content["contentKey"]))
        ddb.put({"PK": f"CONTENT#{content['contentKey']}", "SK": f"TENANT#{tenant}",
                 "vectorCount": count, "attachedAt": ddb.now_ms()})
    manifest = content.get("manifest", {})
    fields = {k: manifest.get(k) for k in ("kind", "durationSec", "width", "height", "fps", "pageCount",
                                           "hasAudio", "thumbnailKey") if manifest.get(k) is not None}
    summary = ddb.get(f"CONTENT#{content['contentKey']}", "ANALYSIS#summary")
    if summary and not asset.get("titleEdited"):
        fields["aiTitle"] = summary.get("title")
    fields["aiSummary"] = (summary or {}).get("summary")
    set_asset_status(asset_id, "ready", **{k: v for k, v in fields.items() if v is not None})


def on_prepare_failed(event: dict) -> None:
    set_asset_status(event["assetId"], "failed", error=str(event.get("error", ""))[:1000])
    if event.get("contentKey"):
        ddb.update(f"CONTENT#{event['contentKey']}", "META", {"status": "failed", "updatedAt": ddb.now_ms()})
