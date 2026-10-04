#!/usr/bin/env python3
"""
Маскирование тональных участков (мелодия перевода, гудки) в аудио перед STT.

В паузах при переводе звонка часто играет громкая мелодия; Whisper реагирует на неё
галлюцинациями и может «проглатывать» следующую фразу (например приветствие по имени).
Детектируем участки с концентрированной энергией в полосе 250–600 Hz (типичные тоны
телефонии) и заменяем их на тишину, чтобы STT не отвлекался на тон.

Использование:
  from transfer_tone_mask import mask_transfer_tones_in_file
  path_clean = mask_transfer_tones_in_file(wav_path, output_path=None)
"""

import os
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import soundfile as sf


def _bin_range(sr: int, frame_len: int, freq_lo: float, freq_hi: float) -> tuple:
    """Индексы бинов FFT для полосы freq_lo..freq_hi Hz."""
    k_lo = int(freq_lo * frame_len / sr)
    k_hi = int(freq_hi * frame_len / sr) + 1
    return max(0, k_lo), min(frame_len // 2 + 1, k_hi)


def mask_transfer_tones(
    audio: np.ndarray,
    sr: int,
    freq_lo: float = 250.0,
    freq_hi: float = 600.0,
    frame_len: int = 1024,
    concentration_threshold: float = 0.55,
    band_energy_ratio_min: float = 0.12,
    min_tonal_sec: float = 0.2,
    fade_ms: float = 10.0,
) -> np.ndarray:
    """
    Заменяет тональные участки на тишину (с коротким затуханием на границах).

    Параметры:
      freq_lo, freq_hi — полоса частот тонов перевода (Hz).
      frame_len — длина кадра для FFT (сэмплов).
      concentration_threshold — порог «узкополосности»: max_bin/sum_bin в полосе.
      band_energy_ratio_min — минимальная доля энергии в полосе от общей (чтобы не помечать тишину).
      min_tonal_sec — минимальная длительность тонального участка для маскирования (сек).
      fade_ms — длительность линейного затухания на границах (мс), чтобы избежать щелчков.
    """
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    n = len(audio)
    out = audio.astype(np.float64).copy()
    if n < frame_len:
        return out

    k_lo, k_hi = _bin_range(sr, frame_len, freq_lo, freq_hi)
    if k_hi <= k_lo:
        return out

    hop = frame_len
    n_frames = (n - frame_len) // hop + 1
    window = np.hanning(frame_len)
    tonal_frame = np.zeros(n_frames, dtype=bool)

    for i in range(n_frames):
        start = i * hop
        end = start + frame_len
        frame = out[start:end] * window
        spec = np.abs(np.fft.rfft(frame))
        band = spec[k_lo:k_hi]
        band_sum = band.sum()
        total_sum = spec.sum()
        if total_sum < 1e-12:
            continue
        if band_sum / (total_sum + 1e-12) < band_energy_ratio_min:
            continue
        concentration = band.max() / (band_sum + 1e-12)
        if concentration >= concentration_threshold:
            tonal_frame[i] = True

    min_run_frames = max(1, int(min_tonal_sec * sr / hop))
    fade_samples = max(0, int(sr * fade_ms / 1000))

    i = 0
    while i < n_frames:
        if not tonal_frame[i]:
            i += 1
            continue
        j = i
        while j < n_frames and tonal_frame[j]:
            j += 1
        run_len = j - i
        if run_len >= min_run_frames:
            s0 = i * hop
            s1 = min((j - 1) * hop + frame_len, n)
            segment_len = s1 - s0
            if fade_samples > 0 and segment_len > 2 * fade_samples:
                fade_in_len = min(fade_samples, segment_len // 2)
                fade_out_len = min(fade_samples, segment_len // 2)
                out[s0 : s0 + fade_in_len] *= np.linspace(1, 0, fade_in_len)
                out[s1 - fade_out_len : s1] *= np.linspace(0, 1, fade_out_len)
                out[s0 + fade_in_len : s1 - fade_out_len] = 0.0
            else:
                out[s0:s1] = 0.0
        i = j

    return out.astype(audio.dtype)


def detect_tonal_segments(
    audio: np.ndarray,
    sr: int,
    freq_lo: float = 250.0,
    freq_hi: float = 600.0,
    frame_len: int = 1024,
    concentration_threshold: float = 0.55,
    band_energy_ratio_min: float = 0.12,
    min_tonal_sec: float = 0.2,
) -> List[Tuple[int, int]]:
    """
    Возвращает список тональных участков в сэмплах: [(start, end), ...].
    Используется для двухпроходной транскрипции: режем аудио по границам гудков.
    """
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    n = len(audio)
    if n < frame_len:
        return []

    k_lo, k_hi = _bin_range(sr, frame_len, freq_lo, freq_hi)
    if k_hi <= k_lo:
        return []

    hop = frame_len
    n_frames = (n - frame_len) // hop + 1
    window = np.hanning(frame_len)
    tonal_frame = np.zeros(n_frames, dtype=bool)

    for i in range(n_frames):
        start = i * hop
        end = start + frame_len
        frame = audio[start:end].astype(np.float64) * window
        spec = np.abs(np.fft.rfft(frame))
        band = spec[k_lo:k_hi]
        band_sum = band.sum()
        total_sum = spec.sum()
        if total_sum < 1e-12:
            continue
        if band_sum / (total_sum + 1e-12) < band_energy_ratio_min:
            continue
        concentration = band.max() / (band_sum + 1e-12)
        if concentration >= concentration_threshold:
            tonal_frame[i] = True

    min_run_frames = max(1, int(min_tonal_sec * sr / hop))
    result: List[Tuple[int, int]] = []
    i = 0
    while i < n_frames:
        if not tonal_frame[i]:
            i += 1
            continue
        j = i
        while j < n_frames and tonal_frame[j]:
            j += 1
        run_len = j - i
        if run_len >= min_run_frames:
            s0 = i * hop
            s1 = min((j - 1) * hop + frame_len, n)
            result.append((s0, s1))
        i = j
    return result


def mask_transfer_tones_in_file(
    wav_in: Path,
    wav_out: Optional[Path] = None,
    **kwargs,
) -> Path:
    """
    Читает WAV, маскирует тональные участки, записывает результат.
    Если wav_out не указан — во временный файл.
    Возвращает путь к выходному файлу.
    """
    audio, sr = sf.read(str(wav_in))
    masked = mask_transfer_tones(audio, sr, **kwargs)
    if wav_out is None:
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        wav_out = Path(path)
    sf.write(str(wav_out), masked, sr)
    return wav_out
