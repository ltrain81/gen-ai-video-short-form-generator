"""Model tasks executed by the model-worker Lambda (SQS, concurrency-limited) and the poller."""
from __future__ import annotations

import json
import logging
from functools import lru_cache

import boto3

from . import ddb, ingest, models, storage
from .ingest import LANGUAGES
from .config import get_config

log = logging.getLogger(__name__)

POLL_DELAY_SEC = 20
TRANSCRIBE_LANGUAGES = ["ko-KR", "en-US", "ja-JP", "zh-CN", "es-US", "fr-FR", "de-DE"]


@lru_cache(maxsize=1)
def transcribe():
    return boto3.client("transcribe")


def _bucket() -> str:
    return get_config().media_bucket


def _put_json(key: str, data) -> None:
    storage.s3().put_object(Bucket=_bucket(), Key=key, Body=json.dumps(data, ensure_ascii=False).encode(),
                            ContentType="application/json")


def _get_json(key: str):
    return json.loads(storage.s3().get_object(Bucket=_bucket(), Key=key)["Body"].read())


def _content(ck: str) -> dict:
    content = ddb.get(f"CONTENT#{ck}", "META", consistent=True)
    if not content:
        raise RuntimeError(f"Unknown content {ck}")
    return content


def _chunk(content: dict, index: int) -> dict:
    return next(c for c in content["manifest"]["chunks"] if c["index"] == index)


def _vector_key(ck: str, modality: str, scope: str, start: float, extra: str = "") -> str:
    return f"{ck}:{modality}:{scope}:{start:.2f}{(':' + extra) if extra else ''}"


# ------------------------------------------------------------------ dispatch
def run(task: dict) -> None:
    name = task["task"]
    handler = {
        "pegasus.summary": pegasus_summary,
        "pegasus.segments": pegasus_segments,
        "marengo.start": marengo_start,
        "transcribe.start": transcribe_start,
        "image.analyze": image_analyze,
        "image.caption": image_caption,
        "describe.embed": describe_embed,
    }[name]
    try:
        handler(task)
    except Exception as exc:  # noqa: BLE001 - record failure on the stage, then re-raise for SQS retry/DLQ
        log.exception("Task %s failed", name)
        if task.get("final_attempt"):
            ingest.on_stage_done(task["contentKey"], _stage_name(task), "failed", str(exc), task.get("lang"))
        raise


def _stage_name(task: dict) -> str:
    n = task["task"]
    if n == "pegasus.summary":
        return "pegasus.summary"
    if n == "describe.embed":
        return "describe"
    if n == "image.analyze":
        return f"image:{task['image']}"
    if n == "image.caption":
        return f"caption:{task['image']}"
    base = {"pegasus.segments": "pegasus.segments", "marengo.start": "marengo", "transcribe.start": "transcribe"}[n]
    return f"{base}:{task['chunk']}"


# ------------------------------------------------------------------ Pegasus
def _lang(task: dict) -> tuple[str, str]:
    code = task.get("lang") or ingest.DEFAULT_LANGUAGE
    return code, LANGUAGES.get(code, "English")


def analysis_prefix(ck: str, lang: str) -> str:
    return f"{storage.content_prefix(ck)}/analysis/{lang}"


def pegasus_summary(task: dict) -> None:
    ck = task["contentKey"]
    code, language = _lang(task)
    content = _content(ck)
    chunks = content["manifest"]["chunks"]
    if len(chunks) == 1:
        summary = models.pegasus_summary(storage.s3_uri(chunks[0]["key"]), language)
    else:
        parts = [models.pegasus_summary(storage.s3_uri(c["key"]), language) for c in chunks]
        summary = _merge_summaries(parts)
    _put_json(f"{analysis_prefix(ck, code)}/summary.json", summary)
    ddb.put({"PK": f"CONTENT#{ck}", "SK": f"ANALYSIS#summary#{code}", **summary})
    ingest.on_stage_done(ck, "pegasus.summary", lang=code)


def _merge_summaries(parts: list[dict]) -> dict:
    merged = {
        "title": parts[0].get("title", ""),
        "summary": " ".join(p.get("summary", "") for p in parts),
        "language": parts[0].get("language", ""),
        "genre": parts[0].get("genre", ""),
    }
    for key in ("speakers", "topics", "suggested_formats"):
        merged[key] = list(dict.fromkeys(x for p in parts for x in p.get(key, [])))
    return merged


def pegasus_segments(task: dict) -> None:
    ck, i = task["contentKey"], task["chunk"]
    code, language = _lang(task)
    chunk = _chunk(_content(ck), i)
    offset = float(chunk["startSec"])
    raw = models.pegasus_segments(storage.s3_uri(chunk["key"]), language=language)
    scenes, chapters = [], []
    for seg in raw:
        seg = dict(seg)
        seg["start_sec"] = float(seg.get("start_sec", 0)) + offset
        seg["end_sec"] = float(seg.get("end_sec", 0)) + offset
        (chapters if seg.pop("_definition_id", "scenes") == "chapters" else scenes).append(seg)
    _put_json(f"{analysis_prefix(ck, code)}/segments/{i:04d}.json", {"scenes": scenes, "chapters": chapters})
    ingest.on_stage_done(ck, f"pegasus.segments:{i}", lang=code)


# ------------------------------------------------------------------ Marengo (video/audio, async)
def marengo_start(task: dict) -> None:
    ck, i = task["contentKey"], task["chunk"]
    chunk = _chunk(_content(ck), i)
    key = chunk["key"] if task.get("inputType", "video") == "video" else chunk["audioKey"]
    out = storage.s3_uri(f"{storage.content_prefix(ck)}/marengo/{i:04d}")
    arn = models.start_video_embedding(storage.s3_uri(key), out, task.get("inputType", "video"))
    ingest.send(get_config().poll_queue_url, {"poll": "marengo", "arn": arn, "contentKey": ck, "chunk": i},
                POLL_DELAY_SEC)


def marengo_poll(msg: dict) -> bool:
    """Returns True when finished (success or failure), False to poll again."""
    job = models.get_async(msg["arn"])
    status = job["status"]
    ck, i = msg["contentKey"], msg["chunk"]
    if status == "InProgress":
        return False
    if status != "Completed":
        ingest.on_stage_done(ck, f"marengo:{i}", "failed", job.get("failureMessage", status))
        return True
    out_uri = job["outputDataConfig"]["s3OutputDataConfig"]["s3Uri"]
    out_key = out_uri.split(f"s3://{_bucket()}/", 1)[1].rstrip("/") + "/output.json"
    data = _get_json(out_key)["data"]
    offset = float(_chunk(_content(ck), i)["startSec"])
    vecs = []
    for item in data:
        scope = item.get("embeddingScope", "clip")
        modality = item.get("embeddingOption", "visual")
        start = float(item.get("startSec", 0)) + offset
        end = float(item.get("endSec", 0)) + offset
        # Asset-scope vectors are per chunk; keep them (useful for "whole video" similarity).
        vecs.append({
            "key": _vector_key(ck, modality, scope, start),
            "embedding": item["embedding"],
            "metadata": {"contentKey": ck, "modality": modality, "scope": scope,
                         "startSec": round(start, 3), "endSec": round(end, 3), "chunk": i},
        })
    _put_json(f"{storage.content_prefix(ck)}/vectors/marengo-{i:04d}.json", vecs)
    ingest.on_stage_done(ck, f"marengo:{i}")
    return True


# ------------------------------------------------------------------ Transcribe
def transcribe_start(task: dict) -> None:
    ck, i = task["contentKey"], task["chunk"]
    chunk = _chunk(_content(ck), i)
    job_name = f"shorts-{ck[:60]}-{i:04d}"[:200]
    try:
        transcribe().start_transcription_job(
            TranscriptionJobName=job_name,
            Media={"MediaFileUri": storage.s3_uri(chunk["audioKey"])},
            IdentifyMultipleLanguages=True,
            LanguageOptions=TRANSCRIBE_LANGUAGES,
            OutputBucketName=_bucket(),
            OutputKey=f"{storage.content_prefix(ck)}/transcribe/raw-{i:04d}.json",
            Settings={"ShowSpeakerLabels": True, "MaxSpeakerLabels": 10},
        )
    except transcribe().exceptions.ConflictException:
        pass  # already started (retry)
    ingest.send(get_config().poll_queue_url, {"poll": "transcribe", "job": job_name, "contentKey": ck, "chunk": i},
                POLL_DELAY_SEC)


def transcribe_poll(msg: dict) -> bool:
    job = transcribe().get_transcription_job(TranscriptionJobName=msg["job"])["TranscriptionJob"]
    status = job["TranscriptionJobStatus"]
    ck, i = msg["contentKey"], msg["chunk"]
    if status in {"QUEUED", "IN_PROGRESS"}:
        return False
    if status == "FAILED":
        reason = job.get("FailureReason", "")
        # No speech is not an error for editing purposes.
        ingest.on_stage_done(ck, f"transcribe:{i}", "skipped" if "speech" in reason.lower() else "failed", reason)
        return True
    raw = _get_json(f"{storage.content_prefix(ck)}/transcribe/raw-{i:04d}.json")
    offset = float(_chunk(_content(ck), i)["startSec"])
    _put_json(f"{storage.content_prefix(ck)}/transcribe/{i:04d}.json", normalize_transcript(raw, offset))
    ingest.on_stage_done(ck, f"transcribe:{i}")
    return True


def normalize_transcript(raw: dict, offset: float = 0.0) -> dict:
    """Transcribe JSON → {language, words:[{s,e,w,spk}], sentences:[{s,e,text,spk}]}"""
    results = raw.get("results", {})
    speakers = {}
    for seg in (results.get("speaker_labels") or {}).get("segments", []):
        for it in seg.get("items", []):
            speakers[it.get("start_time")] = it.get("speaker_label")
    words, sentences, current = [], [], None
    for item in results.get("items", []):
        alt = (item.get("alternatives") or [{}])[0]
        text = alt.get("content", "")
        if item.get("type") == "punctuation":
            if words:
                words[-1]["w"] += text
            if current:
                current["text"] += text
                if text in ".?!。？！":
                    sentences.append(current)
                    current = None
            continue
        s, e = float(item["start_time"]) + offset, float(item["end_time"]) + offset
        spk = speakers.get(item.get("start_time"))
        words.append({"s": round(s, 3), "e": round(e, 3), "w": text, "spk": spk})
        if current is None or (spk and current.get("spk") != spk) or s - current["e"] > 1.5:
            if current:
                sentences.append(current)
            current = {"s": round(s, 3), "e": round(e, 3), "text": text, "spk": spk}
        else:
            current["text"] += ("" if _is_cjk_join(current["text"], text) else " ") + text
            current["e"] = round(e, 3)
    if current:
        sentences.append(current)
    langs = [lang.get("language_code") for lang in results.get("language_codes", [])] or [results.get("language_code")]
    return {"languages": [x for x in langs if x], "words": words, "sentences": sentences}


def _is_cjk_join(prev: str, nxt: str) -> bool:
    return bool(prev) and "぀" <= prev[-1] <= "鿿" and "぀" <= nxt[:1] <= "鿿"


# ------------------------------------------------------------------ images / pages
def image_analyze(task: dict) -> None:
    """Base layer: Marengo image embedding (language-independent)."""
    ck, idx = task["contentKey"], task["image"]
    content = _content(ck)
    image = next(p for p in content["manifest"]["images"] if p["index"] == idx)
    data = storage.s3().get_object(Bucket=_bucket(), Key=image["key"])["Body"].read()
    modality = "page" if content["kind"] == "document" else "image"
    vecs = [{"key": _vector_key(ck, modality, "asset", 0, str(idx)), "embedding": models.embed_image(data=data),
             "metadata": {"contentKey": ck, "modality": modality, "scope": "asset", "page": idx,
                          "startSec": 0, "endSec": 0}}]
    _put_json(f"{storage.content_prefix(ck)}/vectors/image-{idx:04d}.json", vecs)
    ingest.on_stage_done(ck, f"image:{idx}")


def image_caption(task: dict) -> None:
    """Language layer for images: caption/tags in the workspace language."""
    ck, idx = task["contentKey"], task["image"]
    code, language = _lang(task)
    image = next(p for p in _content(ck)["manifest"]["images"] if p["index"] == idx)
    data = storage.s3().get_object(Bucket=_bucket(), Key=image["key"])["Body"].read()
    caption = models.caption_image(data, language=language)
    _put_json(f"{analysis_prefix(ck, code)}/image-{idx:04d}.json", caption)
    if idx == 0:
        ddb.put({"PK": f"CONTENT#{ck}", "SK": f"ANALYSIS#summary#{code}", "title": caption.get("title", ""),
                 "summary": caption.get("caption", ""), "tags": caption.get("tags", []),
                 "on_screen_text": caption.get("on_screen_text", "")})
    ingest.on_stage_done(ck, f"caption:{idx}", lang=code)


# ------------------------------------------------------------------ description embeddings
def describe_embed(task: dict) -> None:
    ck = task["contentKey"]
    code, _ = _lang(task)
    base = analysis_prefix(ck, code)
    prefix = f"{base}/segments/"
    scenes, chapters = [], []
    for obj in storage.s3().list_objects_v2(Bucket=_bucket(), Prefix=prefix).get("Contents", []):
        data = _get_json(obj["Key"])
        scenes.extend(data.get("scenes", []))
        chapters.extend(data.get("chapters", []))
    scenes.sort(key=lambda s: s["start_sec"])
    chapters.sort(key=lambda s: s["start_sec"])
    _put_json(f"{base}/scenes.json", scenes)
    _put_json(f"{base}/chapters.json", chapters)
    vecs = []
    for obj in storage.s3().list_objects_v2(Bucket=_bucket(), Prefix=f"{base}/image-").get("Contents", []):
        cap = _get_json(obj["Key"])
        idx = int(obj["Key"].rsplit("-", 1)[1].split(".")[0])
        text = " ".join(filter(None, [cap.get("title"), cap.get("caption"), ", ".join(cap.get("tags", [])),
                                      cap.get("on_screen_text")]))
        if text:
            vecs.append({"key": _vector_key(ck, "description", "asset", 0, f"{code}:{idx}"),
                         "embedding": models.embed_text(text),
                         "metadata": {"contentKey": ck, "modality": "description", "scope": "asset", "page": idx,
                                      "startSec": 0, "endSec": 0, "lang": code, "text": text[:1500]}})
    for kind, items in (("scene", scenes), ("chapter", chapters)):
        for seg in items:
            text = " ".join(filter(None, [seg.get("title"), seg.get("summary"), seg.get("on_screen_text"),
                                          seg.get("tags")]))
            if not text.strip():
                continue
            vecs.append({
                "key": _vector_key(ck, "description", kind, seg["start_sec"], code),
                "embedding": models.embed_text(text),
                "metadata": {"contentKey": ck, "modality": "description", "scope": kind,
                             "startSec": round(seg["start_sec"], 3), "endSec": round(seg["end_sec"], 3),
                             "lang": code, "text": text[:1500]},
            })
    summary = ddb.get(f"CONTENT#{ck}", f"ANALYSIS#summary#{code}")
    if summary and summary.get("summary") and _content(ck).get("kind") == "video":
        text = f"{summary.get('title', '')}. {summary['summary']}"
        vecs.append({"key": _vector_key(ck, "description", "asset", 0, code), "embedding": models.embed_text(text),
                     "metadata": {"contentKey": ck, "modality": "description", "scope": "asset",
                                  "startSec": 0, "endSec": float(_content(ck).get("durationSec") or 0),
                                  "lang": code, "text": text[:1500]}})
    _put_json(f"{storage.content_prefix(ck)}/vectors/description-{code}.json", vecs)
    ingest.on_stage_done(ck, "describe", lang=code)
