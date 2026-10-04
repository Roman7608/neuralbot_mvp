#!/usr/bin/env python3
"""
Сравнение трёх вариантов обработки тональных участков перед GigaAM:
1. Сегментация по тонам — транскрибируем только речевые сегменты
2. Обрезка по detect_dial_tone_end — отрезаем гудки в начале, транскрибируем остаток
4. Маскирование тонов — заменяем тональные участки на тишину, транскрибируем целиком

Запуск:
  USE_GIGAAM=1 python -m call_analytics.compare_tonal_variants 750
  python -m call_analytics.compare_tonal_variants /path/to/file.wav
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import numpy as np
import soundfile as sf
from text_normalization import normalize_text


def _detect_tonal_segments(
    audio: np.ndarray,
    sr: int,
    freq_lo: float = 180.0,
    freq_hi: float = 900.0,
    concentration_threshold: float = 0.35,
    window_sec: float = 0.2,
    step_sec: float = 0.1,
    min_tonal_sec: float = 0.15,
) -> list:
    """
    Детекция тональных участков по спектру (узкий пик в диапазоне freq_lo..freq_hi).
    Возвращает [(start_sample, end_sample), ...].
    """
    n = len(audio)
    window_len = int(window_sec * sr)
    step = int(step_sec * sr)
    if window_len < 64 or step < 1:
        return []
    tonal_windows = []
    pos = 0
    while pos + window_len <= n:
        chunk = audio[pos : pos + window_len].astype(np.float64)
        energy = np.sqrt(np.mean(chunk ** 2))
        if energy < 1e-6:
            pos += step
            continue
        nfft = min(2048, len(chunk))
        spec = np.abs(np.fft.rfft(chunk, n=nfft))
        freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
        mask = (freqs >= 100) & (freqs <= 2000)
        if not np.any(mask):
            pos += step
            continue
        peak_idx = np.argmax(spec[mask])
        peak_hz = freqs[mask][peak_idx]
        peak_val = spec[mask][peak_idx]
        total = np.sum(spec[mask])
        if total < 1e-10:
            pos += step
            continue
        is_tone = freq_lo <= peak_hz <= freq_hi and (peak_val / total) > concentration_threshold
        if is_tone:
            tonal_windows.append((pos, pos + window_len))
        pos += step
    if not tonal_windows:
        return []
    # Склеиваем пересекающиеся окна
    merged = []
    for s0, s1 in sorted(tonal_windows, key=lambda x: x[0]):
        if merged and s0 <= merged[-1][1] + step * 2:
            merged[-1] = (merged[-1][0], max(merged[-1][1], s1))
        else:
            merged.append((s0, s1))
    min_samples = int(min_tonal_sec * sr)
    return [(a, b) for a, b in merged if (b - a) >= min_samples]


def _get_speech_intervals(
    audio: np.ndarray, sr: int, overlap_sec: float = 0.6
) -> list:
    tonal = _detect_tonal_segments(audio, sr)
    n = len(audio)
    if not tonal:
        return [(0, n)]
    overlap_samp = int(overlap_sec * sr)
    intervals = []
    prev_end = 0
    for s0, s1 in sorted(tonal, key=lambda x: x[0]):
        if s0 > prev_end:
            intervals.append((prev_end, s0))
        prev_end = max(prev_end, max(0, s1 - overlap_samp))
    if prev_end < n:
        intervals.append((prev_end, n))
    return intervals


def _stereo_to_mono(audio: np.ndarray, weak_ratio: float = 0.15) -> np.ndarray:
    """Стерео → моно: (L+R)/2 или громкий канал, если второй слабый."""
    if audio.ndim != 2 or audio.shape[1] < 2:
        return audio if audio.ndim == 1 else audio[:, 0]
    ch0, ch1 = audio[:, 0].astype(np.float64), audio[:, 1].astype(np.float64)
    rms0 = np.sqrt(np.mean(ch0 ** 2)) + 1e-10
    rms1 = np.sqrt(np.mean(ch1 ** 2)) + 1e-10
    if min(rms0, rms1) / max(rms0, rms1) < weak_ratio:
        return (audio[:, 1] if rms1 > rms0 else audio[:, 0]).astype(np.float32)
    return ((ch0 + ch1) / 2.0).astype(np.float32)


# Вариант 1: сегментация по тонам
def variant1_segment_by_tones(file_path: Path, model) -> str:
    audio, sr = sf.read(str(file_path))
    audio = _stereo_to_mono(audio)
    intervals = _get_speech_intervals(audio, sr)
    pieces = []
    with tempfile.TemporaryDirectory(prefix="gigaam_seg_") as tmpdir:
        for i, (s0, s1) in enumerate(intervals):
            if s1 - s0 < int(0.5 * sr):  # пропускаем < 0.5 с
                continue
            chunk_path = Path(tmpdir) / f"seg_{i:03d}.wav"
            sf.write(str(chunk_path), audio[s0:s1], sr)
            txt = (model.transcribe(str(chunk_path)) or "").strip()
            if txt:
                pieces.append(txt)
    raw = " ".join(pieces)
    return normalize_text(raw)


# Вариант 2: обрезка по detect_dial_tone_end
def variant2_trim_dial_tone(file_path: Path, model) -> str:
    from analyze_call_quality import detect_dial_tone_end, trim_audio_after

    end_sec = detect_dial_tone_end(file_path)
    trimmed = trim_audio_after(file_path, end_sec) if end_sec > 0 else file_path
    if trimmed is None:
        trimmed = file_path
    try:
        txt, _, _ = model_transcribe(trimmed, model)
        return txt
    finally:
        if trimmed != file_path and hasattr(trimmed, "unlink"):
            try:
                trimmed.unlink(missing_ok=True)
            except Exception:
                pass


# Вариант 4: маскирование тонов (замена на тишину)
def variant4_mask_tones(file_path: Path, model) -> str:
    audio, sr = sf.read(str(file_path))
    audio = _stereo_to_mono(audio).copy()
    tonal = _detect_tonal_segments(audio, sr)
    for s0, s1 in tonal:
        audio[s0:s1] = 0.0
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = Path(f.name)
    try:
        sf.write(str(tmp_path), audio, sr)
        txt, _, _ = model_transcribe(tmp_path, model)
        return txt
    finally:
        tmp_path.unlink(missing_ok=True)


def model_transcribe(wav_path: Path, model) -> tuple:
    """Единая точка вызова GigaAM с нормализацией."""
    from analyze_call_quality import transcribe_audio_gigaam
    return transcribe_audio_gigaam(wav_path, model, normalize_with_llm=False)


def main():
    parser = argparse.ArgumentParser(
        description="Сравнение 3 вариантов обработки тонов перед GigaAM"
    )
    parser.add_argument(
        "input",
        help="call_id (750) или путь к WAV",
    )
    parser.add_argument(
        "--base-dir",
        default=PROJECT_DIR,
        type=Path,
        help="Базовая директория для относительных путей",
    )
    args = parser.parse_args()

    file_path = None
    inp = args.input.strip()
    base = Path(args.base_dir)

    if inp.isdigit():
        from database.postgresql_manager import CallAnalyticsDB

        row = CallAnalyticsDB.get_call_with_details(int(inp))
        if not row or not row.get("file_path"):
            print(f"Звонок {inp} не найден или нет file_path")
            return 1
        fp = Path(row["file_path"])
        file_path = base / fp if not fp.is_absolute() else fp
    else:
        file_path = Path(inp)

    if not file_path.exists():
        print(f"Файл не найден: {file_path}")
        return 1

    use_gigaam = os.environ.get("USE_GIGAAM", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not use_gigaam:
        print("Запустите с USE_GIGAAM=1")
        return 1

    from analyze_call_quality import load_gigaam_model

    print(f"Файл: {file_path}")
    print(f"Длительность: {sf.info(str(file_path)).duration:.1f} с")
    print("Загрузка GigaAM...", flush=True)
    model = load_gigaam_model()

    # Референс: как сейчас (без обработки тонов)
    print("\n--- Референс (текущий пайплайн, без обработки тонов) ---")
    try:
        ref, _, _ = model_transcribe(file_path, model)
        print(ref or "(пусто)")
    except Exception as e:
        print(f"Ошибка: {e}")
        ref = ""

    # Вариант 1
    print("\n--- Вариант 1: Сегментация по тонам (только речевые интервалы) ---")
    try:
        v1 = variant1_segment_by_tones(file_path, model)
        print(v1 or "(пусто)")
    except Exception as e:
        print(f"Ошибка: {e}")
        v1 = ""

    # Вариант 2
    print("\n--- Вариант 2: Обрезка по detect_dial_tone_end ---")
    try:
        from analyze_call_quality import detect_dial_tone_end

        end_sec = detect_dial_tone_end(file_path)
        print(f"  Конец гудков: {end_sec:.1f} с")
        v2 = variant2_trim_dial_tone(file_path, model)
        print(v2 or "(пусто)")
    except Exception as e:
        print(f"Ошибка: {e}")
        v2 = ""

    # Вариант 4
    print("\n--- Вариант 4: Маскирование тонов (замена на тишину) ---")
    try:
        v4 = variant4_mask_tones(file_path, model)
        print(v4 or "(пусто)")
    except Exception as e:
        print(f"Ошибка: {e}")
        v4 = ""

    print("\n--- Готово ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
