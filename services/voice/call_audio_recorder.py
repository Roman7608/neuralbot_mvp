"""
Запись разговора голосового бота на диск (стерео WAV: L — клиент, R — бот), 8 kHz.

Путь относительно VOICE_BOT_RECORDINGS_ROOT сохраняется в voice_bot_sessions.audio_storage_path.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

SR = 8000


class VoiceCallStereoRecorder:
    """Накапливает фрагменты PCM int16 mono и в конце пишет стерео WAV."""

    def __init__(
        self,
        call_uuid: str,
        root: Path,
        *,
        min_free_bytes: int,
        max_minutes: int,
    ):
        self.call_uuid = call_uuid
        self.root = Path(root)
        self.min_free_bytes = min_free_bytes
        self.max_samples = int(max(1, max_minutes) * 60 * SR)
        self.t0 = time.perf_counter()
        self._client: list[tuple[int, np.ndarray]] = []
        self._bot: list[tuple[int, np.ndarray]] = []
        self._enabled = False
        self._rel_path: str = ""
        self._out: Optional[Path] = None

        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("[%s] Voice recording: cannot mkdir %s: %s", call_uuid, self.root, e)
            return

        try:
            du = shutil.disk_usage(str(self.root))
        except OSError as e:
            logger.warning("[%s] Voice recording: disk_usage failed: %s", call_uuid, e)
            return

        if du.free < min_free_bytes:
            logger.warning(
                "[%s] Voice recording disabled: free %s < min_free %s",
                call_uuid,
                du.free,
                min_free_bytes,
            )
            return

        date_dir = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        safe_uuid = call_uuid.replace("/", "_")
        self._rel_path = f"{date_dir}/{safe_uuid}_call.wav"
        self._out = self.root / self._rel_path
        try:
            self._out.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("[%s] Voice recording: cannot mkdir parent: %s", call_uuid, e)
            return

        self._enabled = True
        logger.info("[%s] Voice recording enabled -> %s", call_uuid, self._out)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def feed_client_pcm(self, pcm_i16: np.ndarray) -> None:
        if not self._enabled or pcm_i16.size == 0:
            return
        end = int((time.perf_counter() - self.t0) * SR)
        start = max(0, end - len(pcm_i16))
        self._client.append((start, pcm_i16.astype(np.int16, copy=True)))

    def feed_bot_pcm(self, pcm_i16: np.ndarray) -> None:
        if not self._enabled or pcm_i16.size == 0:
            return
        end = int((time.perf_counter() - self.t0) * SR)
        start = max(0, end - len(pcm_i16))
        self._bot.append((start, pcm_i16.astype(np.int16, copy=True)))

    def finalize(self) -> Optional[str]:
        if not self._enabled or self._out is None:
            return None
        self._enabled = False

        duration_s = max(0.0, time.perf_counter() - self.t0)
        max_len = min(int(duration_s * SR) + SR // 2, self.max_samples)
        if max_len <= 0:
            return None

        try:
            du = shutil.disk_usage(str(self.root))
            need = max_len * 4 * 2
            if du.free < need + self.min_free_bytes:
                logger.warning(
                    "[%s] Voice recording skipped finalize: low disk (need~%s, free=%s)",
                    self.call_uuid,
                    need,
                    du.free,
                )
                return None
        except OSError:
            pass

        L = np.zeros(max_len, dtype=np.int32)
        R = np.zeros(max_len, dtype=np.int32)

        for start, pcm in self._client:
            pcm_len = len(pcm)
            seg_end = start + pcm_len
            s = max(0, start)
            e = min(max_len, seg_end)
            if e <= s:
                continue
            j0 = s - start
            j1 = e - start
            L[s:e] += pcm[j0:j1].astype(np.int32)

        for start, pcm in self._bot:
            pcm_len = len(pcm)
            seg_end = start + pcm_len
            s = max(0, start)
            e = min(max_len, seg_end)
            if e <= s:
                continue
            j0 = s - start
            j1 = e - start
            R[s:e] += pcm[j0:j1].astype(np.int32)

        L16 = np.clip(L, -32768, 32767).astype(np.int16)
        R16 = np.clip(R, -32768, 32767).astype(np.int16)
        stereo = np.stack([L16, R16], axis=1)

        try:
            import soundfile as sf

            sf.write(str(self._out), stereo, SR, format="WAV", subtype="PCM_16")
            logger.info("[%s] Voice recording saved %s", self.call_uuid, self._out)
            return self._rel_path.replace("\\", "/")
        except Exception:
            logger.exception("[%s] Voice recording write failed", self.call_uuid)
            return None


def build_recorder_from_env(call_uuid: str) -> Optional[VoiceCallStereoRecorder]:
    root_s = (os.environ.get("VOICE_BOT_RECORDINGS_ROOT") or "").strip()
    if not root_s:
        return None
    min_free = int(os.environ.get("VOICE_BOT_RECORDINGS_MIN_FREE_MB", "512")) * 1024 * 1024
    max_min = int(os.environ.get("VOICE_BOT_RECORDINGS_MAX_MINUTES", "60"))
    return VoiceCallStereoRecorder(
        call_uuid,
        Path(root_s),
        min_free_bytes=min_free,
        max_minutes=max_min,
    )
