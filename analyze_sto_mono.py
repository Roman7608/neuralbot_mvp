#!/usr/bin/env python3
"""
Анализ качества звонков Сервиса (СТО) по каналу сотрудника.

Логика: стерео WAV — в одном канале клиент, в другом ассистент/стажёр сервиса.
Транскрибируем только канал сотрудника (без диаризации и смешивания).
Оценка по критериям 7–29 (evaluate_sto_by_rules).

Использование: python analyze_sto_mono.py [путь_к_wav]
По умолчанию: Analytic/STO/2.wav
Канал сотрудника: EMPLOYEE_CHANNEL=0 или 1 (по умолчанию 1 — правый).
"""

import os
import sys
import tempfile
from pathlib import Path

import soundfile as sf

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from text_normalization import normalize_transcript
from analyze_call_quality import load_stt_model, transcribe_audio
from analyze_sto_quality import (
    evaluate_sto_by_rules,
    extract_employee_name_from_transcript,
    STO_CRITERIA,
)

DEFAULT_STO_FILE = PROJECT_DIR / "Analytic" / "STO" / "2.wav"
EMPLOYEE_CHANNEL = int(os.environ.get("EMPLOYEE_CHANNEL", "1"))

NUM_CRITERIA = 23
POINTS_PER_CRITERION = 100.0 / NUM_CRITERIA


def extract_employee_channel_mono(wav_path: Path, channel: int = 1) -> Path:
    """Извлекает один канал (речь сотрудника) из стерео WAV."""
    audio, sr = sf.read(str(wav_path))
    if audio.ndim == 1:
        return wav_path
    if audio.shape[1] < 2:
        return wav_path
    mono = audio[:, channel]
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    sf.write(path, mono, sr)
    return Path(path)


def run_sto_mono(wav_path: Path, employee_channel: int = EMPLOYEE_CHANNEL) -> dict:
    """
    Обработка одного файла СТО: извлечение канала сотрудника, транскрипция, оценка.
    """
    if not wav_path.exists():
        return {"error": f"Файл не найден: {wav_path}", "transcript": "", "scores": {}}

    temp_mono = None
    try:
        print(f"  🔊 Извлечение канала сотрудника (канал {employee_channel})...", flush=True)
        temp_mono = extract_employee_channel_mono(wav_path, employee_channel)

        model = load_stt_model()
        transcript, _, _ = transcribe_audio(
            temp_mono, model, no_initial_prompt=True, vad_filter=False, no_speech_threshold=0.4
        )
        transcript = normalize_transcript(transcript)

        scores = evaluate_sto_by_rules(transcript)
        employee_name = extract_employee_name_from_transcript(transcript)
        total_points = sum(scores.get(i, 0) for i in range(7, 30)) * POINTS_PER_CRITERION

        return {
            "file": str(wav_path.name),
            "transcript": transcript,
            "scores": scores,
            "employee_name": employee_name,
            "total_points": total_points,
        }
    finally:
        if temp_mono and temp_mono != wav_path and temp_mono.exists():
            try:
                temp_mono.unlink()
            except Exception:
                pass


def main():
    wav_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_STO_FILE
    print(f"=== СТО (моно-канал сотрудника): {wav_path} ===", flush=True)
    result = run_sto_mono(wav_path)
    if "error" in result:
        print(f"Ошибка: {result['error']}", flush=True)
        return 1
    print(f"Сотрудник: {result['employee_name']}", flush=True)
    print(f"Итого: {result['total_points']:.1f}/100", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
