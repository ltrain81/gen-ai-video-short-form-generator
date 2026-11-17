"""Generation mode (deploy with -c mode=full): open-source models on SageMaker async endpoints (scale to zero).

Flow: estimate → user confirms (ask_user kind=confirm / UI) → start() → SageMaker InvokeEndpointAsync → poll() →
the output is copied into the archive as a new asset (ai_generated=true, provenance: model + prompt) → regular
ingest (proxies, Marengo, Pegasus…) → usable like any other footage.

  PROJ#{p} / GEN#{id}   job {kind, status, params, outputLocation, assetId}
Safety: reference images are checked by the vision model; images showing an identifiable real person are refused
(no face-swaps / deepfakes of real people). Outputs carry ai_generated metadata.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
import uuid
from functools import lru_cache

import boto3

from . import acl, ddb, events, ingest, models, projects, storage
from .auth import Forbidden, Principal
from .config import get_config

log = logging.getLogger(__name__)

# warm-run seconds, cold start seconds, instance, approx on-demand $/h (us-east-1; estimates only)
KINDS = {
    "image": {"model": "Tongyi-MAI/Z-Image-Turbo", "ext": "png", "warm": 25, "cold": 480, "instance": "ml.g6e.xlarge", "usd_h": 2.25},
    "image_edit": {"model": "Qwen/Qwen-Image-Edit-2509", "ext": "png", "warm": 150, "cold": 900, "instance": "ml.g6e.4xlarge", "usd_h": 3.80},
    "video": {"model": "Wan-AI/Wan2.2-TI2V-5B-Diffusers", "ext": "mp4", "warm": 420, "cold": 720, "instance": "ml.g6e.2xlarge", "usd_h": 2.75},
    "speech": {"model": "openbmb/VoxCPM2", "ext": "wav", "warm": 20, "cold": 420, "instance": "ml.g6e.xlarge", "usd_h": 2.25},
    "music": {"model": "ACE-Step", "ext": "wav", "warm": 60, "cold": 600, "instance": "ml.g6e.xlarge", "usd_h": 2.25},
}
ENDPOINT_SUFFIX = {"image": "image", "image_edit": "image-edit", "video": "video", "speech": "tts", "music": "music"}
POLL_DELAY = 30


def enabled() -> bool:
    return os.environ.get("DEPLOY_MODE", "edit") == "full"


def endpoint(kind: str) -> str:
    return f"{os.environ.get('GEN_ENDPOINT_PREFIX', 'shorts-gen')}-{ENDPOINT_SUFFIX[kind]}"


@lru_cache(maxsize=1)
def sm_runtime():
    return boto3.client("sagemaker-runtime")


@lru_cache(maxsize=1)
def sm():
    return boto3.client("sagemaker")


def _instances(kind: str) -> int:
    try:
        ep = sm().describe_endpoint(EndpointName=endpoint(kind))
        return int(ep["ProductionVariants"][0].get("CurrentInstanceCount", 0))
    except Exception:  # noqa: BLE001
        return 0


def estimate(kind: str, params: dict) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    k = KINDS[kind]
    warm_s = k["warm"] * (max(1.0, float(params.get("seconds", 5)) / 5) if kind == "video" else 1)
    if kind == "speech" and params.get("provider") in {"polly", "elevenlabs"}:
        return {"kind": kind, "provider": params["provider"], "seconds": 5, "coldStart": False, "usd": 0.01}
    cold = _instances(kind) == 0
    total = warm_s + (k["cold"] if cold else 0)
    return {"kind": kind, "model": k["model"], "instance": k["instance"], "coldStart": cold, "seconds": int(total),
            "usd": round(total / 3600 * k["usd_h"], 2), "note": "approximate; GPU scales to zero when idle"}


# ------------------------------------------------------------------ safety
def check_reference(image: bytes) -> None:
    verdict = models.contains_real_person(image)
    if verdict.get("identifiable_real_person"):
        raise Forbidden("Reference images showing an identifiable real person cannot be used for generation")


def _ref_urls(p: Principal, asset_ids: list[str]) -> list[str]:
    urls = []
    for aid in asset_ids[:3]:
        a = ddb.get(f"ASSET#{aid}", "META")
        acl.require(p, a, "use")
        m = (ddb.get(f"CONTENT#{a.get('contentKey', '')}", "META") or {}).get("manifest") or {}
        key = m.get("imageKey") or m.get("thumbnailKey")
        if not key:
            raise ValueError(f"Asset {aid} has no image")
        check_reference(storage.s3().get_object(Bucket=get_config().media_bucket, Key=key)["Body"].read())
        urls.append(storage.presign_get(key, 3 * 3600))
    return urls


# ------------------------------------------------------------------ jobs
def start(p: Principal, project_id: str, kind: str, params: dict) -> dict:
    if not enabled():
        raise Forbidden("Generation mode is not deployed (deploy with -c mode=full)")
    project, _ = projects.require_project(p, project_id, "edit")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    prompt = str(params.get("prompt") or params.get("text") or "").strip()
    if not prompt:
        raise ValueError("prompt (or text for speech) is required")
    jid = "g" + uuid.uuid4().hex[:14]
    job = {"PK": f"PROJ#{project_id}", "SK": f"GEN#{jid}", "jobId": jid, "kind": kind, "status": "queued",
           "createdAt": ddb.now_ms(), "requestedBy": p.sub, "params": {k: v for k, v in params.items() if k != "images"},
           "model": KINDS[kind]["model"]}
    if kind == "speech" and params.get("provider") in {"polly", "elevenlabs"}:
        data = _tts_managed(p, prompt, params)
        job.update(status="running", provider=params["provider"])
        ddb.put(job)
        return finish(job, data, "mp3" if params["provider"] == "polly" else "mp3")
    req: dict = {"aspect": params.get("aspect") or projects.load_timeline(project)["settings"]["aspect"]}
    if kind == "speech":
        req.update(text=prompt[:4000], voicePrompt=params.get("voice"))
    elif kind == "music":
        req.update(prompt=prompt[:500], lyrics=params.get("lyrics"), seconds=float(params.get("seconds", 30)))
    else:
        req.update(prompt=prompt[:2000], seed=params.get("seed"))
        refs = _ref_urls(p, list(params.get("referenceAssetIds") or []))
        if kind == "image_edit":
            if not refs:
                raise ValueError("image_edit needs referenceAssetIds")
            req["images"] = refs
        if kind == "video":
            req["seconds"] = float(min(5, max(1, float(params.get("seconds", 5)))))
            if refs:
                req["image"] = refs[0]
    in_key = f"gen/{p.tenant_id}/{jid}/input.json"
    storage.s3().put_object(Bucket=get_config().media_bucket, Key=in_key, Body=json.dumps(req).encode(),
                            ContentType="application/json")
    resp = sm_runtime().invoke_endpoint_async(EndpointName=endpoint(kind), InputLocation=storage.s3_uri(in_key),
                                              ContentType="application/json", InvocationTimeoutSeconds=3600)
    job.update(status="running", outputLocation=resp["OutputLocation"], failureLocation=resp.get("FailureLocation"),
               estimate=estimate(kind, params))
    ddb.put(job)
    ingest.send(os.environ["GEN_POLL_QUEUE_URL"], {"projectId": project_id, "jobId": jid}, POLL_DELAY)
    _event(project, p.sub, job)
    return _public(job)


def _split(uri: str) -> tuple[str, str]:
    b, _, k = uri.removeprefix("s3://").partition("/")
    return b, k


def poll(msg: dict) -> bool:
    job = ddb.get(f"PROJ#{msg['projectId']}", f"GEN#{msg['jobId']}", consistent=True)
    if not job or job["status"] in {"done", "failed"}:
        return True
    s3 = storage.s3()
    b, k = _split(job["outputLocation"])
    try:
        data = s3.get_object(Bucket=b, Key=k)["Body"].read()
        finish(job, data, KINDS[job["kind"]]["ext"])
        return True
    except s3.exceptions.NoSuchKey:
        pass
    if job.get("failureLocation"):
        fb, fk = _split(job["failureLocation"])
        try:
            err = s3.get_object(Bucket=fb, Key=fk)["Body"].read().decode()[-1500:]
            ddb.update(job["PK"], job["SK"], {"status": "failed", "error": err, "finishedAt": ddb.now_ms()})
            _notify(job)
            return True
        except s3.exceptions.NoSuchKey:
            pass
    if ddb.now_ms() - job["createdAt"] > 2 * 3600 * 1000:
        ddb.update(job["PK"], job["SK"], {"status": "failed", "error": "timed out", "finishedAt": ddb.now_ms()})
        _notify(job)
        return True
    return False


def finish(job: dict, data: bytes, ext: str) -> dict:
    """Register the output as a project asset; the S3 copy triggers the normal ingest pipeline."""
    project_id = job["PK"].removeprefix("PROJ#")
    project = ddb.get(f"PROJ#{project_id}", "META")
    p_sub, tenant = job["requestedBy"], project["tenantId"]
    asset_id = "a" + uuid.uuid4().hex[:20]
    filename = f"ai-{job['kind']}-{job['jobId']}.{ext}"
    key = storage.original_key(tenant, asset_id, filename)
    now = ddb.now_ms()
    prompt = (job.get("params") or {}).get("prompt") or (job.get("params") or {}).get("text") or ""
    ddb.put({"PK": f"ASSET#{asset_id}", "SK": "META", "assetId": asset_id, "tenantId": tenant, "ownerId": p_sub,
             "filename": filename, "contentType": {"png": "image/png", "mp4": "video/mp4", "wav": "audio/wav",
                                                   "mp3": "audio/mpeg"}[ext],
             "sizeBytes": len(data), "status": "queued", "scope": "project", "projectId": project_id, "collectionIds": [],
             "title": f"AI {job['kind']}: {prompt[:60]}", "tags": ["ai-generated"], "aiGenerated": True,
             "provenance": {"model": job.get("provider") or job.get("model"), "prompt": prompt[:2000], "jobId": job["jobId"]},
             "originalKey": key, "createdAt": now, "updatedAt": now,
             "GSI2PK": f"TENANT#{tenant}", "GSI2SK": f"ASSET#{now}#{asset_id}"})
    acl.grant(f"ASSET#{asset_id}", f"USER#{p_sub}", "owner", tenant, p_sub)
    storage.s3().put_object(Bucket=get_config().media_bucket, Key=key, Body=data,
                            Metadata={"ai-generated": "true", "ai-model": str(job.get("provider") or job.get("model"))[:200]})
    ddb.update(job["PK"], job["SK"], {"status": "done", "assetId": asset_id, "finishedAt": ddb.now_ms()})
    ddb.update(f"PROJ#{project_id}", "META", {"assetIds": list(dict.fromkeys([*(project.get("assetIds") or []), asset_id]))[:200]})
    job = {**job, "status": "done", "assetId": asset_id}
    _notify(job)
    return _public(job)


def _public(job: dict) -> dict:
    return {k: job.get(k) for k in ("jobId", "kind", "status", "model", "provider", "params", "assetId", "error",
                                    "estimate", "createdAt", "finishedAt")}


def _event(project: dict, sub: str, job: dict) -> None:
    events.publish(events.channel(project["projectId"], sub), [{"type": "generation.updated", **_public(job)}])


def _notify(job: dict) -> None:
    project = ddb.get(job["PK"], "META") or {"projectId": job["PK"].removeprefix("PROJ#")}
    _event(project, job["requestedBy"], ddb.get(job["PK"], job["SK"]) or job)


def list_jobs(project_id: str) -> list[dict]:
    return [_public(j) for j in ddb.query(f"PROJ#{project_id}", "GEN#", forward=False, limit=50)]


# ------------------------------------------------------------------ managed TTS (Polly / ElevenLabs BYO key)
POLLY_VOICES = {"ko": "Seoyeon", "en": "Joanna", "ja": "Kazuha", "zh": "Zhiyu", "es": "Lucia", "fr": "Lea", "de": "Vicki"}


def _tts_managed(p: Principal, text: str, params: dict) -> bytes:
    if params["provider"] == "polly":
        lang = ingest.tenant_language(p.tenant_id)
        r = boto3.client("polly").synthesize_speech(Text=text[:3000], OutputFormat="mp3", Engine="neural",
                                                     VoiceId=params.get("voice") or POLLY_VOICES.get(lang, "Joanna"))
        return r["AudioStream"].read()
    key = _byo_key(p.tenant_id, "elevenlabs")
    voice = params.get("voice") or "21m00Tcm4TlvDq8ikWAM"
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}", method="POST",
                                 data=json.dumps({"text": text[:5000], "model_id": "eleven_multilingual_v2"}).encode(),
                                 headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _secret_name(tenant_id: str, provider: str) -> str:
    return f"{os.environ.get('SECRETS_PREFIX', 'shorts')}/{tenant_id}/{provider}"


def _byo_key(tenant_id: str, provider: str) -> str:
    try:
        return boto3.client("secretsmanager").get_secret_value(SecretId=_secret_name(tenant_id, provider))["SecretString"]
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"No {provider} API key configured for this workspace") from exc


def set_byo_key(tenant_id: str, provider: str, value: str) -> None:
    sm_ = boto3.client("secretsmanager")
    name = _secret_name(tenant_id, provider)
    try:
        sm_.put_secret_value(SecretId=name, SecretString=value)
    except sm_.exceptions.ResourceNotFoundException:
        sm_.create_secret(Name=name, SecretString=value, Description=f"BYO {provider} key for workspace {tenant_id}")


def has_byo_key(tenant_id: str, provider: str) -> bool:
    try:
        boto3.client("secretsmanager").describe_secret(SecretId=_secret_name(tenant_id, provider))
        return True
    except Exception:  # noqa: BLE001
        return False
