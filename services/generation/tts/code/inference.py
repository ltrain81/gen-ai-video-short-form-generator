"""TTS: openbmb/VoxCPM2 (Apache-2.0, multilingual incl. Korean). Request: {text, voicePrompt?: str (voice design)}"""
import io

import soundfile as sf
from voxcpm import VoxCPM

from shared import MODEL_ID, load_json


def model_fn(model_dir):
    return VoxCPM.from_pretrained(MODEL_ID or "openbmb/VoxCPM2", load_denoiser=False)


def input_fn(body, content_type):
    return load_json(body, content_type)


def predict_fn(req, model):
    text = req["text"][:4000]
    if req.get("voicePrompt"):  # voice design: "(description) text"
        text = f"({req['voicePrompt'][:200]}){text}"
    wav = model.generate(text=text, cfg_value=float(req.get("cfg", 2.0)), inference_timesteps=int(req.get("steps", 10)))
    buf = io.BytesIO()
    sf.write(buf, wav, model.tts_model.sample_rate, format="WAV")
    return buf.getvalue()


def output_fn(data, accept):
    return data, "audio/wav"
