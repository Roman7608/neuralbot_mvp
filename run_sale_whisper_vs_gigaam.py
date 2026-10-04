#!/usr/bin/env python3
"""
Сравнение Whisper и GigaAM на 10 файлах ОП: сегментация по гудкам (0.6 с overlap),
Whisper, GigaAM, Гибрид (сегмент 0=Whisper, после гудков=GigaAM), Гибрид умный (после гудков — с приветствием или длиннее).
Сохранение в Analytic/SALE/whisper_vs_gigaam.xlsx.

Запуск (GPU):
  cd /home/romandemo/Vikingi && .venv/bin/python run_sale_whisper_vs_gigaam.py
"""

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

import pandas as pd
import soundfile as sf

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

SALE_DIR = PROJECT_DIR / "Analytic" / "SALE"
OUTPUT_XLSX = SALE_DIR / "whisper_vs_gigaam.xlsx"
EMPLOYEE_CHANNEL = 1

from analyze_op_mono import (
    extract_both_channels_mono,
    get_tonal_speech_segments,
)
from analyze_call_quality import (
    load_stt_model,
    load_gigaam_model,
    transcribe_audio,
    transcribe_audio_gigaam,
)


def _audio_files(sale_dir: Path) -> List[Path]:
    out = list(sale_dir.glob("*.wav"))
    def _key(p: Path):
        try:
            return (0, int(p.stem), p.name)
        except ValueError:
            return (1, 0, p.name)
    return sorted(out, key=_key)


def _transcribe_segments_with_whisper(
    audio, sr, speech_intervals, model, return_per_segment: bool = False
):
    pieces = []
    temp_paths = []
    try:
        for i, (start, end) in enumerate(speech_intervals):
            seg = audio[start:end]
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            temp_paths.append(Path(path))
            sf.write(path, seg, sr)
            no_speech = 0.3 if i > 0 else 0.4
            txt, _, _ = transcribe_audio(
                Path(path), model,
                vad_filter=False,
                no_speech_threshold=no_speech,
                no_initial_prompt=(i > 0),
            )
            if txt and txt.strip():
                pieces.append(txt.strip())
            else:
                pieces.append("")
        if return_per_segment:
            return pieces
        return " ".join(p for p in pieces if p) if pieces else ""
    finally:
        for p in temp_paths:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass


def _transcribe_segments_with_gigaam(
    audio, sr, speech_intervals, model, return_per_segment: bool = False
):
    pieces = []
    temp_paths = []
    try:
        for start, end in speech_intervals:
            seg = audio[start:end]
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            temp_paths.append(Path(path))
            sf.write(path, seg, sr)
            txt, _, _ = transcribe_audio_gigaam(Path(path), model)
            if txt and txt.strip():
                pieces.append(txt.strip())
            else:
                pieces.append("")
        if return_per_segment:
            return pieces
        return " ".join(p for p in pieces if p) if pieces else ""
    finally:
        for p in temp_paths:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass


# Фразы приветствия менеджера после гудков — для «умного» выбора сегмента.
# Порядок: «здравствуйте … менеджер» или «менеджер … здравствуйте» (файл 9).
# Формат «X, это Y, менеджер отдела продаж» (файл 6).
_GREETING_PATTERN_A = re.compile(
    r"(меня\s+зовут|здравствуйте|добрый\s+день).*?(менеджер|отдел\s+продаж|викинги|заставн|дилерский)",
    re.IGNORECASE | re.DOTALL,
)
_GREETING_PATTERN_B = re.compile(
    r"(менеджер|отдел\s+продаж|викинги|заставн|дилерский).*?(меня\s+зовут|здравствуйте|добрый\s+день)",
    re.IGNORECASE | re.DOTALL,
)
_GREETING_PATTERN_C = re.compile(
    r"это\s+[а-яё]+[,.\s].*?(менеджер|отдел\s+продаж)",
    re.IGNORECASE | re.DOTALL,
)


def _has_greeting(text: str) -> bool:
    if not text or len(text) < 30:
        return False
    return (
        _GREETING_PATTERN_A.search(text) is not None
        or _GREETING_PATTERN_B.search(text) is not None
        or _GREETING_PATTERN_C.search(text) is not None
    )


def _build_hybrid_simple(segments_w: List[str], segments_g: List[str]) -> str:
    """Гибрид: сегмент 0 = Whisper, сегменты после гудков = GigaAM."""
    n = max(len(segments_w), len(segments_g))
    out = []
    for i in range(n):
        w = segments_w[i] if i < len(segments_w) else ""
        g = segments_g[i] if i < len(segments_g) else ""
        if i == 0:
            out.append(w or g)
        else:
            out.append(g or w)
    return " ".join(p for p in out if p).strip() or "(пусто)"


def _build_hybrid_smart(segments_w: List[str], segments_g: List[str]) -> str:
    """После гудков (i>0): берём сегмент с приветствием; если оба/ни одного — длиннее."""
    n = max(len(segments_w), len(segments_g))
    out = []
    for i in range(n):
        w = (segments_w[i] if i < len(segments_w) else "").strip()
        g = (segments_g[i] if i < len(segments_g) else "").strip()
        if i == 0:
            out.append(w or g)
        else:
            has_w = _has_greeting(w)
            has_g = _has_greeting(g)
            if has_g and not has_w:
                out.append(g)
            elif has_w and not has_g:
                out.append(w)
            else:
                out.append(g if len(g) >= len(w) else w)
    return " ".join(p for p in out if p).strip() or "(пусто)"


def main() -> int:
    if not SALE_DIR.exists():
        print(f"Папка не найдена: {SALE_DIR}")
        return 1

    files = _audio_files(SALE_DIR)
    if not files:
        print(f"В {SALE_DIR} нет .wav")
        return 1

    print("Загрузка Whisper (faster-whisper large-v3)...", flush=True)
    model_whisper = load_stt_model()
    print("Загрузка GigaAM-v3...", flush=True)
    model_gigaam = load_gigaam_model()

    rows = []
    for i, wav_path in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] {wav_path.name}", flush=True)
        mono_path = None
        try:
            print("  🔊 Моно (оба канала)...", flush=True)
            mono_path = extract_both_channels_mono(wav_path)
            audio, sr, speech_intervals = get_tonal_speech_segments(mono_path)
            n_seg = len(speech_intervals)
            print(f"  ✂️ Сегментов: {n_seg}", flush=True)

            print("  📝 Whisper...", flush=True)
            seg_whisper = _transcribe_segments_with_whisper(
                audio, sr, speech_intervals, model_whisper, return_per_segment=True
            )
            print("  📝 GigaAM...", flush=True)
            seg_gigaam = _transcribe_segments_with_gigaam(
                audio, sr, speech_intervals, model_gigaam, return_per_segment=True
            )
            text_whisper = " ".join(p for p in seg_whisper if p).strip() or "(пусто)"
            text_gigaam = " ".join(p for p in seg_gigaam if p).strip() or "(пусто)"
            hybrid_simple = _build_hybrid_simple(seg_whisper, seg_gigaam)
            hybrid_smart = _build_hybrid_smart(seg_whisper, seg_gigaam)

            rows.append({
                "Файл": wav_path.name,
                "Транскрипт Whisper": text_whisper,
                "Транскрипт GigaAM": text_gigaam,
                "Гибрид (0=W, 1+=G)": hybrid_simple,
                "Гибрид умный": hybrid_smart,
            })
        except Exception as e:
            print(f"  ❌ Ошибка: {e}", flush=True)
            rows.append({
                "Файл": wav_path.name,
                "Транскрипт Whisper": f"(ошибка: {e})",
                "Транскрипт GigaAM": f"(ошибка: {e})",
                "Гибрид (0=W, 1+=G)": f"(ошибка: {e})",
                "Гибрид умный": f"(ошибка: {e})",
            })
        finally:
            if mono_path and mono_path != wav_path and mono_path.exists():
                try:
                    mono_path.unlink()
                except Exception:
                    pass

    SALE_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Сравнение", index=False)
        ws = writer.sheets["Сравнение"]
        for col, w in [("B", 60), ("C", 60), ("D", 60), ("E", 60)]:
            ws.column_dimensions[col].width = w
        # Лист «Вывод»
        pd.DataFrame([
            ["Колонки: Whisper, GigaAM, Гибрид (сегмент 0=Whisper, после гудков=GigaAM), Гибрид умный (после гудков — с приветствием или длиннее)."],
            ["Если и GigaAM в 4, 6, 9 неудовлетворительный: 1) Сравнить с колонками Гибрид и Гибрид умный. 2) Второй проход: только первые 3–5 сек после тона с no_speech=0.2. 3) Ручная вставка приветствия по образцу. 4) Другой STT (Vosk, облако) только для сегментов после гудков."],
        ]).to_excel(writer, sheet_name="Вывод", index=False, header=False)

    print(f"\nРезультат записан в {OUTPUT_XLSX}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
