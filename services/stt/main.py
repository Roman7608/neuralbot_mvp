"""
STT microservice — GigaAM на GPU (опционально запасной STT-движок).
Принимает аудио (WAV bytes или файл), возвращает транскрипцию.
Приоритет: realtime (бот) > batch (аналитика).
Очередь через asyncio.PriorityQueue + Redis для статистики/мониторинга.
"""

import io
import os
import tempfile
import time
import logging
import asyncio
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import numpy as np
import soundfile as sf
from fastapi import FastAPI, UploadFile, File, Form, HTTPException

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stt-service")

from licensing.checker import verify_license, start_periodic_check
verify_license("stt")
start_periodic_check("stt")

STT_ENGINE = os.getenv("STT_ENGINE", "gigaam").lower()  # gigaam | whisper
# Основной путь при STT_ENGINE=whisper; имена WHISPER_* оставлены для совместимости со старым compose.
MODEL_PATH_PRIMARY = os.getenv("STT_PRIMARY_MODEL_PATH") or os.getenv(
    "WHISPER_MODEL_PATH_VOICE", "Systran/faster-whisper-medium"
)
MODEL_PATH_ALT = os.getenv("STT_ALT_MODEL_PATH") or os.getenv(
    "WHISPER_MODEL_PATH_LARGE", "models/whisper/faster-whisper-large-v3"
)
MODEL_PATH_BACKUP = os.getenv("STT_BACKUP_MODEL_PATH") or os.getenv("WHISPER_MODEL_PATH_BACKUP", "")
LOAD_STT_ALT_MODEL = (
    os.getenv("LOAD_STT_ALT_MODEL") or os.getenv("LOAD_WHISPER_LARGE", "")
).lower() in ("1", "true", "yes")
GIGAAM_MODEL_PATH = os.getenv("GIGAAM_MODEL_PATH", "models/gigaam-v3")
DEVICE = os.getenv("STT_DEVICE", "cuda")
COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "float16")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

_model_primary = None
_model_backup = None
_redis = None
_engine = "whisper"  # gigaam | whisper

PRIORITY_REALTIME = 0
PRIORITY_NORMAL = 5
PRIORITY_BATCH = 10

_stats = {"total": 0, "realtime": 0, "batch": 0, "errors": 0, "avg_time_ms": 0.0}
_gpu_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
_request_counter = 0


@dataclass(order=True)
class STTRequest:
    priority: int
    seq: int = field(compare=True)
    model: object = field(compare=False)
    audio_data: Optional[np.ndarray] = field(compare=False, default=None)
    wav_path: Optional[str] = field(compare=False, default=None)
    sample_rate: int = field(compare=False, default=16000)
    language: str = field(compare=False, default="ru")
    future: Optional[asyncio.Future] = field(compare=False, default=None)


def _load_auxiliary_stt_model(model_path: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    logger.info("Loading auxiliary STT model %s on %s (%s)...", model_path, device, compute_type)
    t0 = time.perf_counter()
    model = WhisperModel(model_path, device=device, compute_type=compute_type)
    logger.info("Auxiliary STT model loaded in %.1fs", time.perf_counter() - t0)
    return model


def _load_gigaam_model():
    """Загрузка GigaAM-v3 с патчем для meta tensors (PyTorch 2.8+)."""
    try:
        from transformers import AutoModel
    except ImportError:
        raise RuntimeError("transformers не установлен. pip install transformers torch torchaudio")
    path = Path(GIGAAM_MODEL_PATH)
    if not (path / "config.json").exists():
        raise FileNotFoundError(
            f"GigaAM-v3 не найдена в {path}. Запустите: python download_gigaam.py"
        )
    logger.info("Loading GigaAM-v3 from %s...", path)
    t0 = time.perf_counter()
    import torch
    from torchaudio import functional as F_ta
    from torchaudio.functional import functional as _F_impl

    _orig_melscale_fbanks = F_ta.melscale_fbanks

    def _patched_melscale_fbanks(n_freqs, f_min, f_max, n_mels, sample_rate, norm=None, mel_scale="htk"):
        import warnings
        if norm is not None and norm != "slaney":
            raise ValueError('norm must be one of None or "slaney"')
        all_freqs = torch.linspace(0, sample_rate // 2, n_freqs)
        m_min = _F_impl._hz_to_mel(f_min, mel_scale=mel_scale)
        m_max = _F_impl._hz_to_mel(f_max, mel_scale=mel_scale)
        m_pts = torch.linspace(m_min, m_max, n_mels + 2)
        f_pts = _F_impl._mel_to_hz(m_pts, mel_scale=mel_scale)
        fb = _F_impl._create_triangular_filterbank(all_freqs, f_pts)
        if norm is not None and norm == "slaney":
            enorm = 2.0 / (f_pts[2 : n_mels + 2] - f_pts[:n_mels])
            fb *= enorm.unsqueeze(0)
        try:
            if (fb.max(dim=0).values == 0.0).any():
                warnings.warn(
                    f"At least one mel filterbank has all zero values. n_mels={n_mels}, n_freqs={n_freqs}"
                )
        except RuntimeError as e:
            if "meta tensors" not in str(e):
                raise
        return fb

    F_ta.melscale_fbanks = _patched_melscale_fbanks
    from transformers import modeling_utils
    _orig_finalize = modeling_utils.PreTrainedModel._finalize_model_loading

    def _patched_finalize(model, load_config, loading_info):
        if not hasattr(model, "all_tied_weights_keys"):
            model.all_tied_weights_keys = getattr(model, "_tied_weights_keys", None) or {}
        _call = getattr(_orig_finalize, "__func__", _orig_finalize)
        return _call(model, load_config, loading_info)

    modeling_utils.PreTrainedModel._finalize_model_loading = staticmethod(_patched_finalize)
    try:
        model = AutoModel.from_pretrained(
            str(path),
            trust_remote_code=True,
            local_files_only=True,
            low_cpu_mem_usage=False,
        )
    finally:
        F_ta.melscale_fbanks = _orig_melscale_fbanks
        modeling_utils.PreTrainedModel._finalize_model_loading = _orig_finalize
    model.eval()
    if torch.cuda.is_available():
        model = model.cuda()
        logger.info("GigaAM on GPU: %s", torch.cuda.get_device_name(0))
    logger.info("GigaAM loaded in %.1fs", time.perf_counter() - t0)
    return model


async def _gpu_worker():
    """Background worker: берёт задачи из очереди по приоритету, выполняет на GPU."""
    loop = asyncio.get_event_loop()
    logger.info("GPU worker started")
    while True:
        req: STTRequest = await _gpu_queue.get()
        try:
            t0 = time.perf_counter()
            result = await loop.run_in_executor(
                None, _transcribe, req.model, req.audio_data, req.sample_rate, req.language, req.wav_path
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            result["processing_ms"] = round(elapsed_ms, 1)

            _stats["total"] += 1
            if req.priority <= PRIORITY_REALTIME:
                _stats["realtime"] += 1
            else:
                _stats["batch"] += 1
            n = _stats["total"]
            _stats["avg_time_ms"] = _stats["avg_time_ms"] * (n - 1) / n + elapsed_ms / n

            _publish_stats(result, req.priority, elapsed_ms)
            text_preview = (result.get("text") or "")[:80].replace("\n", " ")
            logger.info("STT result: %.0fms -> '%s'", elapsed_ms, text_preview)
            req.future.set_result(result)
        except Exception as e:
            _stats["errors"] += 1
            logger.exception("GPU worker error")
            req.future.set_exception(e)
        finally:
            _gpu_queue.task_done()


def _publish_stats(result: dict, priority: int, elapsed_ms: float):
    """Publish stats to Redis if available."""
    if not _redis:
        return
    try:
        import json
        _redis.publish("stt:completed", json.dumps({
            "priority": priority,
            "text_len": len(result.get("text", "")),
            "duration": result.get("duration", 0),
            "processing_ms": round(elapsed_ms, 1),
            "queue_size": _gpu_queue.qsize(),
        }))
        _redis.set("stt:queue_size", _gpu_queue.qsize())
        _redis.set("stt:last_processing_ms", round(elapsed_ms, 1))
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model_primary, _model_backup, _redis, _engine
    _engine = STT_ENGINE
    if STT_ENGINE == "gigaam":
        _model_primary = _load_gigaam_model()
    else:
        _model_primary = _load_auxiliary_stt_model(MODEL_PATH_PRIMARY, DEVICE, COMPUTE_TYPE)
    try:
        if MODEL_PATH_BACKUP:
            _model_backup = _load_auxiliary_stt_model(MODEL_PATH_BACKUP, DEVICE, COMPUTE_TYPE)
            logger.info("Backup STT model loaded from %s", MODEL_PATH_BACKUP)
        elif LOAD_STT_ALT_MODEL:
            alt_path = MODEL_PATH_ALT
            if alt_path and (Path(alt_path).exists() or "/" in alt_path):
                _model_backup = _load_auxiliary_stt_model(alt_path, DEVICE, COMPUTE_TYPE)
    except Exception as e:
        logger.warning("Backup/alternate STT model not loaded: %s", e)

    try:
        import redis
        _redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        _redis.ping()
        logger.info("Redis connected: %s", REDIS_URL)
    except Exception as e:
        logger.warning("Redis not available (stats disabled): %s", e)
        _redis = None

    worker_task = asyncio.create_task(_gpu_worker())
    yield
    worker_task.cancel()
    logger.info("STT service shutting down")


app = FastAPI(title="Vikingi STT Service", lifespan=lifespan)


def _transcribe(model, audio_data: Optional[np.ndarray], sample_rate: int, language: str, wav_path: Optional[str] = None):
    """Транскрипция: GigaAM по wav_path или запасной движок по float PCM в audio_data."""
    if wav_path:
        # GigaAM: model.transcribe(wav_file) -> str
        text = model.transcribe(wav_path) or ""
        try:
            info = sf.info(wav_path)
            dur = info.duration
        except Exception:
            dur = 0.0
        return {
            "text": text.strip(),
            "segments": [{"start": 0, "end": dur, "text": text.strip()}] if text.strip() else [],
            "language": language,
            "duration": round(dur, 2),
        }
    if sample_rate != 16000:
        import scipy.signal
        ratio = 16000 / sample_rate
        n_samples = int(len(audio_data) * ratio)
        audio_data = scipy.signal.resample(audio_data, n_samples)

    if audio_data.ndim > 1:
        audio_data = audio_data.mean(axis=1)

    audio_data = audio_data.astype(np.float32)

    segments, info = model.transcribe(audio_data, language=language, beam_size=5, vad_filter=True)
    result_segments = []
    full_text_parts = []
    for seg in segments:
        result_segments.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
        })
        full_text_parts.append(seg.text.strip())

    return {
        "text": " ".join(full_text_parts),
        "segments": result_segments,
        "language": info.language,
        "duration": round(info.duration, 2),
    }


async def _enqueue(model, audio_data=None, sample_rate=16000, language="ru", priority=PRIORITY_NORMAL, wav_path=None) -> dict:
    """Put a request into the priority queue and wait for result."""
    global _request_counter
    _request_counter += 1
    future = asyncio.get_event_loop().create_future()
    req = STTRequest(
        priority=priority,
        seq=_request_counter,
        model=model,
        audio_data=audio_data,
        wav_path=wav_path,
        sample_rate=sample_rate,
        language=language,
        future=future,
    )
    await _gpu_queue.put(req)
    return await future


def _want_backup_stt(model_size: str) -> bool:
    """primary|medium → основная модель; backup|large|alt → запасная (если загружена)."""
    v = (model_size or "primary").lower().strip()
    return v in ("backup", "large", "alt", "secondary")


@app.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    model_size: str = Form("primary"),
    language: str = Form("ru"),
    priority: str = Form("normal"),
):
    """
    Транскрибировать аудиофайл.
    model_size: primary (основная, GigaAM при engine=gigaam) | backup (запасная, если загружена).
      Устаревшие значения medium и large эквивалентны primary и backup.
    priority: realtime (бот, приоритет 0) | normal (5) | batch (аналитика, 10)
    """
    use_backup = _want_backup_stt(model_size) and _model_backup
    model = _model_backup if use_backup else _model_primary
    if not model:
        raise HTTPException(status_code=503, detail="STT model not loaded")

    try:
        content = await file.read()
        buf = io.BytesIO(content)
        audio_data, sample_rate = sf.read(buf, dtype="float32")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot read audio: {e}")

    prio_map = {"realtime": PRIORITY_REALTIME, "normal": PRIORITY_NORMAL, "batch": PRIORITY_BATCH}
    prio = prio_map.get(priority, PRIORITY_NORMAL)

    try:
        if _engine == "gigaam" and not use_backup:
            wav_path = _audio_to_wav_temp(audio_data, sample_rate)
            try:
                result = await _enqueue(
                    model, wav_path=wav_path, sample_rate=sample_rate, language=language, priority=prio
                )
                return result
            finally:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
        result = await _enqueue(model, audio_data=audio_data, sample_rate=sample_rate, language=language, priority=prio)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("POST /transcribe failed")
        msg = (str(e) or "").strip() or type(e).__name__
        raise HTTPException(status_code=500, detail=msg) from e


def _audio_to_wav_temp(audio_data: np.ndarray, sample_rate: int) -> str:
    """Записать аудио во временный WAV (16 kHz mono для GigaAM)."""
    if sample_rate != 16000:
        import scipy.signal
        ratio = 16000 / sample_rate
        n_samples = int(len(audio_data) * ratio)
        audio_data = scipy.signal.resample(audio_data, n_samples)
    if audio_data.ndim > 1:
        audio_data = audio_data.mean(axis=1)
    audio_float = audio_data.astype(np.float32)

    # RMS для диагностики аудиопотока
    rms = float(np.sqrt(np.mean(audio_float.astype(np.float64) ** 2)))
    duration_ms = len(audio_float) / 16000 * 1000
    logger.info("GigaAM input: %.1fms, RMS=%.6f", duration_ms, rms)

    # Warmup: 0.5 с тишины в начале (как в аналитике) — лучше распознавание начала фразы
    warmup_sec = float(os.environ.get("GIGAAM_WARMUP_SEC", "1.0"))
    if warmup_sec > 0:
        n_silence = int(warmup_sec * 16000)
        silence = np.zeros(n_silence, dtype=np.float32)
        audio_float = np.concatenate([silence, audio_float])

    audio_data = (np.clip(audio_float, -1.0, 1.0) * 32767).astype(np.int16)
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    sf.write(path, audio_data, 16000, subtype="PCM_16")
    return path


@app.post("/transcribe/bytes")
async def transcribe_bytes(
    file: UploadFile = File(...),
    sample_rate: int = Form(16000),
    language: str = Form("ru"),
):
    """
    Транскрибировать сырые PCM16 байты (real-time от бота, приоритет 0).
    """
    model = _model_primary
    if not model:
        raise HTTPException(status_code=503, detail="STT model not loaded")

    try:
        raw = await file.read()
        audio_data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot read PCM: {e}")

    try:
        if _engine == "gigaam":
            wav_path = _audio_to_wav_temp(audio_data, sample_rate)
            try:
                result = await _enqueue(
                    model, wav_path=wav_path, sample_rate=sample_rate, language=language, priority=PRIORITY_REALTIME
                )
                return result
            finally:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
        result = await _enqueue(
            model, audio_data=audio_data, sample_rate=sample_rate, language=language, priority=PRIORITY_REALTIME
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("POST /transcribe/bytes failed")
        msg = (str(e) or "").strip() or type(e).__name__
        raise HTTPException(status_code=500, detail=msg) from e


@app.get("/health")
def health():
    out = {
        "status": "ok",
        "engine": _engine,
        "device": DEVICE,
        "queue_size": _gpu_queue.qsize(),
        "redis": _redis is not None,
        "stats": _stats,
    }
    if _engine == "gigaam":
        out["gigaam_model"] = GIGAAM_MODEL_PATH if _model_primary else None
        out["backup_stt_loaded"] = bool(_model_backup)
    else:
        out["primary_stt_path"] = MODEL_PATH_PRIMARY if _model_primary else None
        out["backup_stt_loaded"] = bool(_model_backup)
    return out
