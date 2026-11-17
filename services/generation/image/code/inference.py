"""Text-to-image: Tongyi-MAI/Z-Image-Turbo (Apache-2.0). Request: {prompt, aspect, negative?, seed?, steps?}"""
import torch
from diffusers import DiffusionPipeline

from shared import MODEL_ID, device, dims, load_json, png_bytes


def model_fn(model_dir):
    pipe = DiffusionPipeline.from_pretrained(MODEL_ID or "Tongyi-MAI/Z-Image-Turbo", torch_dtype=torch.bfloat16)
    return pipe.to(device())


def input_fn(body, content_type):
    return load_json(body, content_type)


def predict_fn(req, pipe):
    w, h = dims(req.get("aspect", "9:16"), int(req.get("longSide", 1536)))
    g = torch.Generator(device()).manual_seed(int(req.get("seed", 0)) or torch.seed() % (2**31))
    img = pipe(prompt=req["prompt"], height=h, width=w, num_inference_steps=int(req.get("steps", 9)),
               guidance_scale=float(req.get("guidance", 0.0)), generator=g).images[0]
    return png_bytes(img)


def output_fn(data, accept):
    return data, "image/png"
