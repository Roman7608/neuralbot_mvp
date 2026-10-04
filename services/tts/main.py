"""
TTS microservice — Silero TTS v5 на CPU.
Принимает текст, возвращает WAV-аудио.
Кэширует повторяющиеся фразы.
"""

import io
import os
import hashlib
import logging
import time
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager
from collections import OrderedDict

import torch
import numpy as np
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import Response

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tts-service")

from licensing.checker import verify_license, start_periodic_check
verify_license("tts")
start_periodic_check("tts")

MODEL_PATH = os.getenv("SILERO_MODEL_PATH", "/models/tts/v5_1_ru.pt")
SPEAKER = os.getenv("SILERO_SPEAKER", "xenia")
SAMPLE_RATE = int(os.getenv("SILERO_SAMPLE_RATE", "48000"))
SPEED = float(os.getenv("SILERO_SPEED", "1.1"))
CACHE_MAX_SIZE = int(os.getenv("TTS_CACHE_SIZE", "500"))

_model = None
_cache: OrderedDict = OrderedDict()


def _load_model(path: str):
    logger.info("Loading Silero TTS from %s...", path)
    t0 = time.perf_counter()
    model = torch.package.PackageImporter(path).load_pickle("tts_models", "model")
    model.to(torch.device("cpu"))
    logger.info("TTS model loaded in %.1fs", time.perf_counter() - t0)
    return model


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    _model = _load_model(MODEL_PATH)
    yield
    logger.info("TTS service shutting down")


app = FastAPI(title="Vikingi TTS Service", lifespan=lifespan)


def _synthesize(text: str, speaker: str, sample_rate: int, speed: float) -> bytes:
    """Synthesize text to WAV bytes."""
    cache_key = hashlib.md5(f"{text}|{speaker}|{sample_rate}|{speed}".encode()).hexdigest()

    if cache_key in _cache:
        _cache.move_to_end(cache_key)
        return _cache[cache_key]

    audio_tensor = _model.apply_tts(
        text=text,
        speaker=speaker,
        sample_rate=sample_rate,
    )

    audio_np = audio_tensor.numpy()

    if speed != 1.0:
        target_len = int(len(audio_np) / speed)
        indices = np.linspace(0, len(audio_np) - 1, target_len).astype(int)
        audio_np = audio_np[indices]

    buf = io.BytesIO()
    import soundfile as sf
    sf.write(buf, audio_np, sample_rate, format="WAV", subtype="PCM_16")
    wav_bytes = buf.getvalue()

    _cache[cache_key] = wav_bytes
    if len(_cache) > CACHE_MAX_SIZE:
        _cache.popitem(last=False)

    return wav_bytes


@app.post("/synthesize")
async def synthesize(
    text: str = Form(...),
    speaker: str = Form(SPEAKER),
    sample_rate: int = Form(SAMPLE_RATE),
    speed: float = Form(SPEED),
):
    """Синтезировать текст в WAV-аудио."""
    if not _model:
        raise HTTPException(status_code=503, detail="TTS model not loaded")
    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty text")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="Text too long (max 2000 chars)")

    try:
        wav_bytes = _synthesize(text.strip(), speaker, sample_rate, speed)
    except Exception as e:
        logger.exception("TTS error")
        raise HTTPException(status_code=500, detail=str(e))

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"Content-Length": str(len(wav_bytes))},
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": MODEL_PATH if _model else None,
        "speaker": SPEAKER,
        "sample_rate": SAMPLE_RATE,
        "cache_size": len(_cache),
    }
