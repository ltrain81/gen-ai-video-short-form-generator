"""Batch: N shorts from one long video. TENANT#{t} / BATCH#{id} → projectIds.

Moments are planned deterministically from the analysis (distinct chapters, else the top highlight scenes spaced
apart), then each project gets a focused first turn so the shorts do not overlap.
"""
from __future__ import annotations

import json
import logging
import os
import uuid

import boto3

from . import acl, broker, ddb, ingest, projects, recipes, storage
from .auth import Principal
from .config import get_config
from .http import BadRequest, NotFound

log = logging.getLogger(__name__)
MAX_COUNT = 10


def _analysis(asset: dict, name: str) -> list:
    lang = asset.get("analysisLanguage") or ingest.DEFAULT_LANGUAGE
    try:
        return json.loads(storage.s3().get_object(Bucket=get_config().media_bucket,
                                                  Key=f"{storage.content_prefix(asset['contentKey'])}/analysis/{lang}/{name}.json")["Body"].read())
    except storage.s3().exceptions.NoSuchKey:
        return []


def plan(asset: dict, count: int) -> list[dict]:
    chapters = [c for c in _analysis(asset, "chapters") if c["end_sec"] - c["start_sec"] >= 20]
    if len(chapters) >= count:
        chapters.sort(key=lambda c: -(c["end_sec"] - c["start_sec"]))
        picked = sorted(chapters[:count], key=lambda c: c["start_sec"])
        return [{"startSec": c["start_sec"], "endSec": c["end_sec"], "title": c.get("title"), "summary": c.get("summary")}
                for c in picked]
    scenes = sorted(_analysis(asset, "scenes"), key=lambda s: -(s.get("highlight_score") or 0))
    dur = float(asset.get("durationSec") or 0)
    window = max(30.0, dur / (count * 2)) if dur else 60.0
    picked: list[dict] = []
    for s in scenes:
        mid = (s["start_sec"] + s["end_sec"]) / 2
        if all(abs(mid - (p["startSec"] + p["endSec"]) / 2) > window for p in picked):
            picked.append({"startSec": max(0.0, mid - window / 2), "endSec": min(dur or mid + window, mid + window / 2),
                           "title": s.get("summary", "")[:60], "summary": s.get("summary")})
        if len(picked) == count:
            break
    if len(picked) < count and dur:  # fall back to even slices
        step = dur / count
        picked = [{"startSec": i * step, "endSec": (i + 1) * step, "title": f"Part {i + 1}", "summary": ""} for i in range(count)]
    return sorted(picked, key=lambda p: p["startSec"])


def plan_topics(p: Principal, asset_id: str, topics: list[str], length: float = 45, verify: bool = True) -> list[dict]:
    """Where each topic is best covered in this video (hybrid transcript + Marengo + Pegasus; see topics.py)."""
    from . import agent_tools, topics as topic_planner
    asset = ddb.get(f"ASSET#{asset_id}", "META")
    acl.require(p, asset, "use")
    if asset.get("kind") != "video" or asset.get("status") != "ready":
        raise BadRequest("Topic planning needs a ready video asset")
    topics = [str(t).strip()[:120] for t in topics if str(t).strip()][:MAX_COUNT]
    if not topics:
        raise BadRequest("topics are required")
    m = ((ddb.get(f"CONTENT#{asset['contentKey']}", "META") or {}).get("manifest") or {})
    helpers = {"profile": agent_tools.content_profile(asset), "sentences": agent_tools._transcript(asset).get("sentences", []),
               "chapters": _analysis(asset, "chapters"), "scenes": _analysis(asset, "scenes"),
               "shots": agent_tools._shots(asset), "chunks": m.get("chunks", [])}
    lang = ingest.LANGUAGES.get(ingest.tenant_language(p.tenant_id), "Korean")
    return topic_planner.plan({**asset, "tenantId": p.tenant_id}, topics, float(length or 45), helpers, verify, lang)


def create(p: Principal, asset_id: str, count: int, body: dict) -> dict:
    """N shorts from one video: from planned `items` [{topic?, startSec, endSec}] (topic series) or `count` moments."""
    planned = [x for x in (body.get("items") or []) if isinstance(x, dict) and "startSec" in x and "endSec" in x][:MAX_COUNT]
    if not planned and body.get("topics"):
        planned = plan_topics(p, asset_id, list(body["topics"]), float(body.get("lengthSec") or 45))
    count = len(planned) if planned else max(1, min(MAX_COUNT, int(count)))
    asset = ddb.get(f"ASSET#{asset_id}", "META")
    acl.require(p, asset, "use")
    if asset.get("kind") != "video" or asset.get("status") != "ready":
        raise BadRequest("Batch needs a ready video asset")
    recipe = recipes.get_recipe(p.tenant_id, body["recipe"]) if body.get("recipe") else None
    aspect = body.get("aspect") or (recipe or {}).get("aspect") or "9:16"
    lang = ingest.tenant_language(p.tenant_id)
    bid = body.get("_batchId") or "bt" + uuid.uuid4().hex[:12]
    items = []
    moments = [{"startSec": float(x["startSec"]), "endSec": float(x["endSec"]), "topic": x.get("topic"),
                "title": x.get("topic"), "summary": x.get("pegasus") or ""} for x in planned] if planned else plan(asset, count)
    for i, m in enumerate(moments, 1):
        base = asset.get('title') or asset.get('aiTitle') or asset.get('filename')
        title = f"{m['topic']} — {base}" if m.get("topic") else f"{base} #{i}"
        proj = projects.create(p, title[:200], aspect, str(body.get("instruction", ""))[:2000], [asset_id])
        ddb.update(f"PROJ#{proj['projectId']}", "META", {"batchId": bid, **({"recipe": recipe["id"]} if recipe else {}),
                                                        **({"brandKitId": body["brandKitId"]} if body.get("brandKitId") else {})})
        if m.get("topic"):
            focus = (f"Topic series short {i}/{count} from asset {asset_id}: a self-contained short about \"{m['topic']}\". "
                     f"The planned range is {m['startSec']:.1f}s–{m['endSec']:.1f}s (found by transcript + visual search and "
                     "Pegasus); you may trim or extend it by up to ~15 s to start and end on complete thoughts, but stay on "
                     "this topic and do not reuse other topics' ranges. Title the short with the topic.")
        else:
            focus = (f"Batch short {i}/{count} from asset {asset_id}. Use ONLY source range {m['startSec']:.1f}s–{m['endSec']:.1f}s"
                     f" ({m.get('title') or ''}: {m.get('summary') or ''}). Other shorts in this batch cover the other ranges, "
                     f"so do not reuse moments outside it.")
        text = (recipes.compose_prompt(recipe, body.get("instruction", ""), {k: body.get(k) for k in ("lengthSec", "captions")
                                                                              if body.get(k)}, lang)
                if recipe else (body.get("instruction") or "Make a strong short from this range."))
        items.append({"projectId": proj["projectId"], "title": title, "range": [m["startSec"], m["endSec"]], "topic": m.get("topic"),
                      "prompt": f"{focus}\n{text}"})
    ddb.put({"PK": f"TENANT#{p.tenant_id}", "SK": f"BATCH#{bid}", "batchId": bid, "assetId": asset_id, "ownerId": p.sub,
             "createdAt": ddb.now_ms(), "items": [{k: v for k, v in it.items() if k != "prompt"} for it in items],
             "recipe": (recipe or {}).get("id"), "aspect": aspect, "kind": "topics" if planned else "count"})
    fn = os.environ["BROKER_FUNCTION"]
    lam = boto3.client("lambda")
    for it in items:  # one independent agent session per short
        turn = uuid.uuid4().hex[:10]
        if broker.try_start(it["projectId"], turn):
            broker.save_message(it["projectId"], "user", it["prompt"].split("\n", 1)[-1], turn=turn)
            lam.invoke(FunctionName=fn, InvocationType="Event", Payload=json.dumps(
                {"projectId": it["projectId"], "sub": p.sub, "text": it["prompt"], "turn": turn}).encode())
    return get(p, bid)


def get(p: Principal, bid: str) -> dict:
    b = ddb.get(f"TENANT#{p.tenant_id}", f"BATCH#{bid}")
    if not b or (b["ownerId"] != p.sub and not p.is_tenant_admin):
        raise NotFound("Batch not found")
    out = []
    for it in b["items"]:
        proj = ddb.get(f"PROJ#{it['projectId']}", "META") or {}
        busy = proj.get("agentBusy")
        out.append({**it, "version": proj.get("currentVersion", 0),
                    "busy": bool(busy and busy.get("until", 0) > ddb.now_ms())})
    return {**{k: b.get(k) for k in ("batchId", "assetId", "createdAt", "recipe", "aspect", "kind", "status", "topics", "plan", "error")},
            "items": out}


def list_for(p: Principal) -> list[dict]:
    return [{k: b.get(k) for k in ("batchId", "assetId", "createdAt", "recipe", "aspect")} | {"count": len(b.get("items", []))}
            for b in ddb.query(f"TENANT#{p.tenant_id}", "BATCH#", forward=False) if b["ownerId"] == p.sub or p.is_tenant_admin]


def start_topic_batch(p: Principal, asset_id: str, body: dict) -> dict:
    """Create the batch record now (status=planning) and plan + launch in the broker (long-running)."""
    asset = ddb.get(f"ASSET#{asset_id}", "META")
    acl.require(p, asset, "use")
    topics = [str(t).strip()[:120] for t in body.get("topics") or [] if str(t).strip()][:MAX_COUNT]
    if not topics:
        raise BadRequest("topics are required")
    bid = "bt" + uuid.uuid4().hex[:12]
    ddb.put({"PK": f"TENANT#{p.tenant_id}", "SK": f"BATCH#{bid}", "batchId": bid, "assetId": asset_id, "ownerId": p.sub,
             "createdAt": ddb.now_ms(), "items": [], "topics": topics, "status": "planning", "kind": "topics",
             "recipe": body.get("recipe"), "aspect": body.get("aspect") or "9:16"})
    boto3.client("lambda").invoke(FunctionName=os.environ["BROKER_FUNCTION"], InvocationType="Event", Payload=json.dumps(
        {"kind": "topic_batch", "sub": p.sub, "batchId": bid, "assetId": asset_id,
         "body": {k: v for k, v in body.items() if k in {"topics", "lengthSec", "aspect", "recipe", "instruction", "captions", "brandKitId"}}}).encode())
    return get(p, bid)


def run_topic_batch(event: dict) -> None:
    from .auth import load_principal
    p = load_principal(event["sub"])
    bid = event["batchId"]
    try:
        body = dict(event["body"])
        body["items"] = plan_topics(p, event["assetId"], body["topics"], float(body.get("lengthSec") or 45))
        created = create(p, event["assetId"], len(body["items"]), {**body, "_batchId": bid})
        b = ddb.get(f"TENANT#{p.tenant_id}", f"BATCH#{created['batchId']}") or {}
        ddb.update(f"TENANT#{p.tenant_id}", f"BATCH#{bid}", {"items": b.get("items", []), "status": "running",
                                                            "plan": [{k: x.get(k) for k in ("topic", "startSec", "endSec", "confidence", "signals", "warning")}
                                                                     for x in body["items"]]})
    except Exception as exc:  # noqa: BLE001
        log.exception("topic batch failed")
        ddb.update(f"TENANT#{p.tenant_id}", f"BATCH#{bid}", {"status": "failed", "error": str(exc)[:500]})
