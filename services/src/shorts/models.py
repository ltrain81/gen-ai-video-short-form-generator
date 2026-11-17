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


def pegasus_summary(s3_uri: str) -> dict:
    out = _invoke_pegasus({
        "analysis_mode": "general",
        "inputPrompt": (
            "Analyze this video for a video editor's archive. Give a concise title, a summary, "
            "the spoken language, genre, speakers (by role, e.g. 'host', 'guest in blue shirt'), main topics, "
            "mood, and which edit formats it suits (highlight, recap, tutorial, supercut, interview, b-roll, ...)."
        ),
        "mediaSource": _media_source(s3_uri),
        "temperature": 0.2,
        "maxOutputTokens": 4096,
        "responseFormat": {"type": "json_schema", "jsonSchema": SUMMARY_SCHEMA},
    })
    return json.loads(out["message"])


def pegasus_segments(s3_uri: str, definitions: list[dict] | None = None) -> list[dict]:
    out = _invoke_pegasus({
        "analysis_mode": "time_based_metadata",
        "mediaSource": _media_source(s3_uri),
        "temperature": 0,
        "maxOutputTokens": 32768,
        "segment_time_format": "seconds",
        "segment_definitions": definitions or SEGMENT_DEFINITIONS,
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
    '"photo|illustration|screenshot|slide|logo|chart|other"}'
)


def caption_image(data: bytes, fmt: str = "jpeg", prompt: str = IMAGE_CAPTION_PROMPT) -> dict:
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
