"""Reference-based image editing / character consistency: Qwen/Qwen-Image-Edit-2509 (Apache-2.0).
Request: {prompt, images:[url|b64] (1-3 references), aspect?, steps?, seed?}"""
import torch
from diffusers import DiffusionPipeline

from shared import MODEL_ID, load_image, load_json, png_bytes


def model_fn(model_dir):
    pipe = DiffusionPipeline.from_pretrained(MODEL_ID or "Qwen/Qwen-Image-Edit-2509", torch_dtype=torch.bfloat16)
    pipe.enable_model_cpu_offload()  # ~57 GB of weights on a single 48 GB GPU
    return pipe


def input_fn(body, content_type):
    return load_json(body, content_type)


def predict_fn(req, pipe):
    refs = [load_image(u) for u in req.get("images", [])[:3]]
    g = torch.Generator("cpu").manual_seed(int(req.get("seed", 0)) or torch.seed() % (2**31))
    out = pipe(image=refs if len(refs) > 1 else refs[0], prompt=req["prompt"], negative_prompt=" ",
               num_inference_steps=int(req.get("steps", 40)), true_cfg_scale=float(req.get("cfg", 4.0)), generator=g)
    return png_bytes(out.images[0])


def output_fn(data, accept):
    # The model server only accepts bytes/str here (a (body, type) tuple fails with "Unsupported model output data type").
    return data
