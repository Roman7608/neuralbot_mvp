"""
Energy-based Voice Activity Detection for telephony audio.

Adapted from demo_main.py VAD logic for use with AudioSocket (8 kHz PCM).
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_ENERGY_THRESHOLD = 0.0015  # телефония: тише порог — не терять слабый первый слог (риск шума)
DEFAULT_SILENCE_MS = 350
DEFAULT_MAX_DURATION_SEC = 3.0


def detect_speech_energy(chunk: np.ndarray, threshold: float = DEFAULT_ENERGY_THRESHOLD) -> bool:
    """Return True if chunk RMS energy exceeds threshold."""
    if chunk.size == 0:
        return False
    rms = np.sqrt(np.mean(chunk.astype(np.float64) ** 2))
    return rms > threshold


class VADCollector:
    """
    Collects audio chunks and detects speech boundaries.

    Usage:
        vad = VADCollector(sample_rate=8000)
        for chunk in audio_stream:
            vad.feed(chunk)
            if vad.speech_ended:
                audio = vad.get_audio()
                # process audio
                vad.reset()
    """

    def __init__(
        self,
        sample_rate: int = 8000,
        energy_threshold: float = DEFAULT_ENERGY_THRESHOLD,
        silence_ms: int = DEFAULT_SILENCE_MS,
        max_duration_sec: float = DEFAULT_MAX_DURATION_SEC,
    ):
        self.sample_rate = sample_rate
        self.energy_threshold = energy_threshold
        self.silence_ms = silence_ms
        self.max_duration_sec = max_duration_sec

        self._buffer: list[np.ndarray] = []
        self._total_samples = 0
        self._speech_detected = False
        self._silence_samples = 0
        self._silence_limit = int(sample_rate * silence_ms / 1000)
        self._max_samples = int(sample_rate * max_duration_sec)

    @property
    def speech_ended(self) -> bool:
        if not self._speech_detected:
            return False
        if self._silence_samples >= self._silence_limit:
            return True
        if self._total_samples >= self._max_samples:
            return True
        return False

    @property
    def is_collecting(self) -> bool:
        return self._speech_detected

    def feed(self, chunk: np.ndarray) -> None:
        """Feed a chunk of float32 audio (already normalized to -1..1)."""
        self._buffer.append(chunk)
        self._total_samples += len(chunk)

        if detect_speech_energy(chunk, self.energy_threshold):
            self._speech_detected = True
            self._silence_samples = 0
        else:
            if self._speech_detected:
                self._silence_samples += len(chunk)

    def get_audio(self) -> np.ndarray:
        """Return concatenated audio buffer as float32."""
        if not self._buffer:
            return np.array([], dtype=np.float32)
        return np.concatenate(self._buffer)

    def reset(self) -> None:
        """Reset state for the next utterance."""
        self._buffer.clear()
        self._total_samples = 0
        self._speech_detected = False
        self._silence_samples = 0
