"""Music: ACE-Step (Apache-2.0). Request: {prompt (tags/style), lyrics? ('[instrumental]' default), seconds (5-240)}"""
import os
import tempfile

from shared import load_json


def model_fn(model_dir):
    from acestep.pipeline_ace_step import ACEStepPipeline
    return ACEStepPipeline(checkpoint_dir=os.environ.get("ACESTEP_DIR", "/tmp/acestep"), dtype="bfloat16",
                           torch_compile=False)


def input_fn(body, content_type):
    return load_json(body, content_type)


def predict_fn(req, pipe):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "music.wav")
        pipe(audio_duration=float(min(240, max(5, req.get("seconds", 30)))), prompt=req["prompt"][:500],
             lyrics=req.get("lyrics") or "[instrumental]", infer_step=int(req.get("steps", 60)),
             guidance_scale=float(req.get("guidance", 15.0)), save_path=out, format="wav")
        wav = next((os.path.join(d, f) for f in os.listdir(d) if f.endswith(".wav")), out)
        return open(wav, "rb").read()


def output_fn(data, accept):
    # The model server only accepts bytes/str here (a (body, type) tuple fails with "Unsupported model output data type").
    return data
