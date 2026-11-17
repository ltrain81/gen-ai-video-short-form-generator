"""Bedrock model calls: Marengo Embed 3.0, Pegasus 1.5, multimodal vision (OpenAI on Bedrock)."""
from __future__ import annotations

import base64
import json
from functools import lru_cache

import boto3
from botocore.config import Config as BotoConfig

from .config import get_config


@lru_cache(maxsize=1)
def runtime():
    return boto3.client("bedrock-runtime", config=BotoConfig(read_timeout=900, retries={"max_attempts": 6, "mode": "adaptive"}))


# ---------------------------------------------------------------- Marengo
def _media_source(s3_uri: str | None = None, data: bytes | None = None) -> dict:
    if data is not None:
        return {"base64String": base64.b64encode(data).decode()}
    return {"s3Location": {"uri": s3_uri, "bucketOwner": get_config().account_id}}


def embed_text(text: str) -> list[float]:
    body = {"inputType": "text", "text": {"inputText": text[:2000]}}
    return _invoke_marengo(body)


def embed_image(s3_uri: str | None = None, data: bytes | None = None) -> list[float]:
    body = {"inputType": "image", "image": {"mediaSource": _media_source(s3_uri, data)}}
    return _invoke_marengo(body)


def embed_text_image(text: str, s3_uri: str | None = None, data: bytes | None = None) -> list[float]:
    body = {"inputType": "text_image", "text_image": {"inputText": text, "mediaSource": _media_source(s3_uri, data)}}
    return _invoke_marengo(body)


def _invoke_marengo(body: dict) -> list[float]:
    resp = runtime().invoke_model(modelId=get_config().marengo_query_model_id, body=json.dumps(body))
    data = json.loads(resp["body"].read())["data"]
    return (data[0] if isinstance(data, list) else data)["embedding"]


def start_video_embedding(s3_uri: str, output_s3_uri: str, input_type: str = "video") -> str:
    """Async Marengo job over a video/audio chunk. Returns invocationArn."""
    spec: dict = {"mediaSource": _media_source(s3_uri), "embeddingScope": ["clip", "asset"]}
    if input_type == "video":
        spec["segmentation"] = {"method": "dynamic", "dynamic": {"minDurationSec": 4}}
        spec["embeddingOption"] = ["visual", "audio", "transcription"]
    else:
        spec["embeddingOption"] = ["audio", "transcription"]
    resp = runtime().start_async_invoke(
        modelId=get_config().marengo_model_id,
        modelInput={"inputType": input_type, input_type: spec},
        outputDataConfig={"s3OutputDataConfig": {"s3Uri": output_s3_uri.rstrip("/")}},
    )
    return resp["invocationArn"]


def get_async(invocation_arn: str) -> dict:
    return runtime().get_async_invoke(invocationArn=invocation_arn)


# ---------------------------------------------------------------- Pegasus
SEGMENT_DEFINITIONS = [
    {
        "id": "scenes",
        "description": "Contiguous scenes or shots with a coherent action, topic or setting.",
        "fields": [
            {"name": "summary", "type": "string", "description": "What happens; who/what is visible; what is said."},
            {"name": "on_screen_text", "type": "string", "description": "Visible text, slides, captions (empty if none)."},
            {"name": "shot_type", "type": "string", "description": "Framing.",
             "enum": ["wide", "medium", "close_up", "screen_recording", "slide", "b_roll", "other"]},
            {"name": "highlight_score", "type": "integer",
             "description": "Integer 1-5: how strong this moment is as a standalone hook or highlight (5 = best)."},
            {"name": "tags", "type": "string", "description": "Comma-separated keywords (people roles, objects, actions)."},
        ],
    },
    {
        "id": "chapters",
        "description": "High-level chapters of the video, each covering one topic.",
        "fields": [
            {"name": "title", "type": "string", "description": "Short chapter title."},
            {"name": "summary", "type": "string", "description": "One or two sentence chapter summary."},
        ],
    },
]

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "language": {"type": "string"},
        "genre": {"type": "string"},
        "speakers": {"type": "array", "items": {"type": "string"}},
        "topics": {"type": "array", "items": {"type": "string"}},
        "mood": {"type": "string"},
        "suggested_formats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary"],
}


def _invoke_pegasus(body: dict) -> dict:
    resp = runtime().invoke_model(modelId=get_config().pegasus_model_id, body=json.dumps(body))
    return json.loads(resp["body"].read())


def language_instruction(language: str) -> str:
    native = NATIVE_INSTRUCTIONS.get(language, "").split(". ")[0].split("。")[0]
    return (native + ". " if native else "") + \
        f"Write every text value in {language} (keep proper nouns as commonly written in {language})."


# Pegasus follows the output language best when the instruction is written in that language, at definition level
# (verified: an English "Write in Korean." on field descriptions was ignored).
NATIVE_INSTRUCTIONS = {
    "Korean": "모든 텍스트 필드는 한국어로 작성하세요. on_screen_text는 화면에 보이는 글자를 그대로 옮겨 적으세요.",
    "Japanese": "すべてのテキスト項目は日本語で書いてください。on_screen_text は画面の文字をそのまま書き写してください。",
    "Chinese": "所有文本字段请用中文书写。on_screen_text 请原样抄录画面上的文字。",
    "Spanish": "Escribe todos los campos de texto en español. Copia on_screen_text tal como aparece en pantalla.",
    "French": "Rédige tous les champs texte en français. Recopie on_screen_text tel qu'il apparaît à l'écran.",
    "German": "Schreibe alle Textfelder auf Deutsch. Übernimm on_screen_text exakt wie auf dem Bildschirm.",
}


def localized_definitions(language: str) -> list[dict]:
    instruction = NATIVE_INSTRUCTIONS.get(
        language, f"Write every text field in {language}. Copy on_screen_text verbatim from the screen.")
    return [{**d, "description": f"{d['description']} {instruction}"} for d in SEGMENT_DEFINITIONS]


def pegasus_summary(s3_uri: str, language: str = "English") -> dict:
    out = _invoke_pegasus({
        "analysis_mode": "general",
        "inputPrompt": (
            "Analyze this video for a video editor's archive. Give a concise title, a summary, "
            "the spoken language, genre, speakers (by role, e.g. 'host', 'guest in blue shirt'), main topics, "
            "mood, and which edit formats it suits (highlight, recap, tutorial, supercut, interview, b-roll, ...). "
            + language_instruction(language)
        ),
        "mediaSource": _media_source(s3_uri),
        "temperature": 0.2,
        "maxOutputTokens": 4096,
        "responseFormat": {"type": "json_schema", "jsonSchema": SUMMARY_SCHEMA},
    })
    return json.loads(out["message"])


def pegasus_segments(s3_uri: str, definitions: list[dict] | None = None, language: str = "English") -> list[dict]:
    for _attempt in range(2):  # the multi-definition pass occasionally returns no scenes; retry once
        segs = _pegasus_segments_once(s3_uri, definitions, language)
        if definitions or any(s.get("_definition_id", "scenes") == "scenes" for s in segs):
            return segs
    return segs


def _pegasus_segments_once(s3_uri: str, definitions: list[dict] | None, language: str) -> list[dict]:
    out = _invoke_pegasus({
        "analysis_mode": "time_based_metadata",
        "mediaSource": _media_source(s3_uri),
        "temperature": 0,
        "maxOutputTokens": 32768,
        "segment_time_format": "seconds",
        "segment_definitions": definitions or localized_definitions(language),
    })
    return out.get("segments", [])


def pegasus_ask(s3_uri: str, prompt: str, start: float | None = None, end: float | None = None,
                schema: dict | None = None) -> str | dict:
    body: dict = {"analysis_mode": "general", "inputPrompt": prompt, "mediaSource": _media_source(s3_uri),
                  "temperature": 0.2, "maxOutputTokens": 8192}
    if start is not None:
        body["start_time"] = float(start)
    if end is not None:
        body["end_time"] = float(end)
    if schema:
        body["responseFormat"] = {"type": "json_schema", "jsonSchema": schema}
    out = _invoke_pegasus(body)
    return json.loads(out["message"]) if schema else out["message"]


# ---------------------------------------------------------------- Vision (images)
IMAGE_CAPTION_PROMPT = (
    "Describe this image for a video editor's asset archive. Reply with JSON only: "
    '{"title": str, "caption": str, "tags": [str], "on_screen_text": str, "kind": '
    '"photo|illustration|screenshot|slide|logo|chart|other"}. '
    "Write title, caption and tags in {language}; copy on_screen_text verbatim."
)


def caption_image(data: bytes, fmt: str = "jpeg", language: str = "English", prompt: str | None = None) -> dict:
    prompt = prompt or IMAGE_CAPTION_PROMPT.replace("{language}", language)
    resp = runtime().converse(
        modelId=get_config().vision_model_id,
        messages=[{"role": "user", "content": [
            {"image": {"format": fmt, "source": {"bytes": data}}},
            {"text": prompt},
        ]}],
        inferenceConfig={"maxTokens": 1024},
    )
    text = "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
    start, end = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return {"caption": text.strip(), "tags": []}


# ---------------------------------------------------------------- Reframing (vision)
FRAMING_PROMPT = """You are a professional video editor reframing footage from {src} to a {aspect} canvas.
Editorial intent: {intent}
For each frame (in order; source timestamps {times}) decide:
- layout: "crop" (one clear subject: follow it), "split" (two things must stay visible, e.g. speaker + slide/board:
  stack them), "fit_blur" (wide scene/landscape/group where cropping loses the point: show whole frame over a blurred
  fill), or "letterbox" (text/graphics that must not be cut).
- x, y: center of the region of interest in the source frame, normalized 0..1 (0,0 = top-left).
- zoom: 1.0 = tightest crop that still fits the target aspect at full source height; >1 zooms in further (max {max_zoom}).
- subject: short label of what you keep in frame.
Prefer stability: reuse the previous frame's values unless the subject clearly moved or the shot changed.
Reply with JSON only: {{"frames": [{{"x": 0.5, "y": 0.5, "zoom": 1.0, "layout": "crop", "subject": "..."}}, ...]}}"""


def framing_plan(images: list[bytes], times: list[float], aspect: str, intent: str, width: int | None,
                 height: int | None, max_zoom: float = 1.6) -> list[dict]:
    src = f"{width}x{height}" if width and height else "16:9"
    content: list[dict] = [{"image": {"format": "jpeg", "source": {"bytes": img}}} for img in images]
    content.append({"text": FRAMING_PROMPT.format(src=src, aspect=aspect, max_zoom=max_zoom, intent=intent or "keep the main subject",
                                                  times=", ".join(f"{t:.1f}s" for t in times))})
    resp = runtime().converse(modelId=get_config().vision_model_id, messages=[{"role": "user", "content": content}],
                              inferenceConfig={"maxTokens": 2048})
    text = "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
    try:
        frames = json.loads(text[text.find("{"): text.rfind("}") + 1])["frames"]
    except (ValueError, KeyError):
        frames = []
    frames = (frames + [frames[-1] if frames else {}] * len(images))[: len(images)]
    clamp = lambda v, lo, hi, d: min(hi, max(lo, float(v))) if isinstance(v, (int, float)) else d  # noqa: E731
    return [{"x": clamp(f.get("x"), 0, 1, 0.5), "y": clamp(f.get("y"), 0, 1, 0.5),
             "zoom": clamp(f.get("zoom"), 1, max_zoom, 1.0),
             "layout": f.get("layout") if f.get("layout") in {"crop", "split", "fit_blur", "letterbox"} else "crop",
             "subject": str(f.get("subject", ""))[:80]} for f in frames]


def describe_frames(images: list[bytes], context: str = "", language: str = "English") -> str:
    """Text description of frames, for orchestrators that cannot take images in tool results."""
    content: list[dict] = [{"image": {"format": "jpeg", "source": {"bytes": img}}} for img in images[:4]]
    content.append({"text": (
        "You are assisting a video editor. Describe these frames precisely and compactly: composition, subjects and "
        "where they are in frame (left/center/right, top/bottom), on-screen text verbatim, action and visual energy, "
        "and whether a vertical crop would lose important content. " + (f"Context: {context}. " if context else "")
        + f"Answer in {language}.")})
    resp = runtime().converse(modelId=get_config().vision_model_id, messages=[{"role": "user", "content": content}],
                              inferenceConfig={"maxTokens": 900})
    return "".join(c.get("text", "") for c in resp["output"]["message"]["content"]).strip()


def suggest_metadata(context: str, language: str) -> dict:
    """Title / description / hashtags for publishing, as JSON."""
    resp = runtime().converse(
        modelId=get_config().vision_model_id,
        messages=[{"role": "user", "content": [{"text": (
            "Write publishing metadata for this edited video. Reply with JSON only: "
            '{"title": str (<= 60 chars, hook-like), "description": str (2-3 sentences), '
            '"hashtags": [str] (5-8, each starting with #)}. '
            f"Write in {language}. Video context:\n{context[:6000]}")}]}],
        inferenceConfig={"maxTokens": 700})
    text = "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
    try:
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError:
        data = {}
    tags = [t if str(t).startswith("#") else f"#{t}" for t in data.get("hashtags", [])][:10]
    return {"title": str(data.get("title", ""))[:100], "description": str(data.get("description", ""))[:1000], "hashtags": tags}


def contains_real_person(image: bytes) -> dict:
    """Safety gate for generation references: is there an identifiable real person (face clearly visible)?"""
    resp = runtime().converse(
        modelId=get_config().vision_model_id,
        messages=[{"role": "user", "content": [
            {"image": {"format": "jpeg" if image[:3] == b"\xff\xd8\xff" else "png", "source": {"bytes": image}}},
            {"text": ('Does this image show an identifiable real human (a photo or realistic depiction where a face is '
                      'clearly visible)? Illustrations, cartoons, mascots, products, landscapes and people with no '
                      'visible face are not identifiable. Reply JSON only: {"identifiable_real_person": bool, "reason": str}')}]}],
        inferenceConfig={"maxTokens": 200})
    text = "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
    try:
        return json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError:
        return {"identifiable_real_person": True, "reason": "unparseable safety verdict (fail closed)"}


MEMORY_PROMPT = """You maintain the long-term memory of a video-editing project between an editor (user) and an AI.
Update the memory with the new conversation below. Keep: the goal and audience, the decisions made (format, length,
style, music, captions, chosen clips), the user's preferences and dislikes, what was tried and rejected, and open
requests. Drop chit-chat and anything superseded. Max ~250 words, bullet points, in {language}.

Current memory:
{memory}

New conversation:
{conversation}

Reply with the updated memory only."""


def update_memory(memory: str, conversation: str, language: str = "English") -> str:
    resp = runtime().converse(modelId=get_config().vision_model_id, messages=[{"role": "user", "content": [
        {"text": MEMORY_PROMPT.format(memory=memory or "(empty)", conversation=conversation[:30000], language=language)}]}],
        inferenceConfig={"maxTokens": 1024})
    return "".join(c.get("text", "") for c in resp["output"]["message"]["content"]).strip()[:4000]
