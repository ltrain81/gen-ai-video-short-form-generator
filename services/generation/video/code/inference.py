"""Text/Image-to-video: Wan-AI/Wan2.2-TI2V-5B-Diffusers (Apache-2.0), 24 fps.
Request: {prompt, image?: url|b64 (I2V), aspect, seconds (1-5), steps?, seed?}"""
import tempfile

import torch
from diffusers import AutoencoderKLWan, WanImageToVideoPipeline, WanPipeline
from diffusers.utils import export_to_video

from shared import MODEL_ID, device, dims, load_image, load_json

NEG = "blurry, low quality, distorted, watermark, text, static, deformed"


def model_fn(model_dir):
    mid = MODEL_ID or "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
    vae = AutoencoderKLWan.from_pretrained(mid, subfolder="vae", torch_dtype=torch.float32)
    t2v = WanPipeline.from_pretrained(mid, vae=vae, torch_dtype=torch.bfloat16).to(device())
    i2v = WanImageToVideoPipeline(**t2v.components)
    return {"t2v": t2v, "i2v": i2v}


def input_fn(body, content_type):
    return load_json(body, content_type)


def predict_fn(req, pipes):
    w, h = dims(req.get("aspect", "9:16"), 1280)
    w, h = (704, 1280) if h > w else (1280, 704) if w > h else (960, 960)
    frames = int(min(5, max(1, float(req.get("seconds", 5)))) * 24) // 4 * 4 + 1
    g = torch.Generator(device()).manual_seed(int(req.get("seed", 0)) or torch.seed() % (2**31))
    args = dict(prompt=req["prompt"], negative_prompt=NEG, height=h, width=w, num_frames=frames,
                num_inference_steps=int(req.get("steps", 40)), guidance_scale=float(req.get("guidance", 5.0)), generator=g)
    if req.get("image"):
        img = load_image(req["image"]).resize((w, h))
        out = pipes["i2v"](image=img, **args).frames[0]
    else:
        out = pipes["t2v"](**args).frames[0]
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        export_to_video(out, f.name, fps=24)
        return open(f.name, "rb").read()


def output_fn(data, accept):
    # The model server only accepts bytes/str here (a (body, type) tuple fails with "Unsupported model output data type").
    return data
