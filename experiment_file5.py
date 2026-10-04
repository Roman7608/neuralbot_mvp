#!/usr/bin/env python3
"""
Эксперимент с распознаванием файла 5 (21 января - Евд - Станислав.wav).
Запускает оба движка (GigaAM и Whisper), сохраняет полный текст в Analytic/5.txt.
"""

import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
AUDIO_DIR = PROJECT_DIR / "Analytic"
AUDIO_FILES = sorted(AUDIO_DIR.glob("*.wav"))

FILE_INDEX = 5
audio_file = AUDIO_FILES[FILE_INDEX - 1] if len(AUDIO_FILES) >= FILE_INDEX else None

CHECK_PHRASES = [
    "дилерский центр",
    "чери викинги",
    "меня зовут евгений",
    "слушаю вас",
]


def _check_phrases(raw: str) -> list:
    low = raw.lower()
    return [f"  {'✓' if p in low else '✗'} «{p}»" for p in CHECK_PHRASES]


def main():
    if not audio_file or not audio_file.exists():
        print(f"❌ Файл {FILE_INDEX} не найден: {audio_file}")
        sys.exit(1)

    print("=== Эксперимент: распознавание файла 5 (оба движка) ===")
    print(f"  Файл: {audio_file.name}\n")

    from analyze_call_quality import (
        load_gigaam_model,
        transcribe_audio_gigaam,
        load_stt_model,
        transcribe_audio,
        cut_hostess_prelude_for_file,
    )

    # 1. GigaAM
    print("  📌 GigaAM-v3...")
    model_giga = load_gigaam_model()
    _, seg_giga, raw_giga = transcribe_audio_gigaam(audio_file, model_giga)
    _, after_giga = cut_hostess_prelude_for_file(FILE_INDEX, seg_giga, raw_giga)
    print(f"     Сегментов: {len(seg_giga)}")

    # 2. Whisper
    print("  📌 Whisper...")
    model_whisper = load_stt_model()
    _, seg_whisper, raw_whisper = transcribe_audio(audio_file, model_whisper)
    _, after_whisper = cut_hostess_prelude_for_file(FILE_INDEX, seg_whisper, raw_whisper)
    print(f"     Сегментов: {len(seg_whisper)}")

    # Сохраняем в Analytic/5.txt — целиком, оба варианта
    out_path = AUDIO_DIR / "5.txt"
    lines = [
        "=== Эксперимент: распознавание файла 5 ===",
        f"Файл: {audio_file.name}",
        "",
        "=" * 60,
        "## GIGAAM-v3 ##",
        "=" * 60,
        "",
        "--- Сырая транскрипция (до cut_hostess) ---",
        raw_giga,
        "",
        "--- После cut_hostess_prelude ---",
        after_giga,
        "",
        "--- Проверка фраз ---",
    ] + _check_phrases(raw_giga) + [
        "",
        "=" * 60,
        "## WHISPER ##",
        "=" * 60,
        "",
        "--- Сырая транскрипция (до cut_hostess) ---",
        raw_whisper,
        "",
        "--- После cut_hostess_prelude ---",
        after_whisper,
        "",
        "--- Проверка фраз ---",
    ] + _check_phrases(raw_whisper)

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  💾 Сохранено в {out_path}")


if __name__ == "__main__":
    main()
