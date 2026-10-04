#!/usr/bin/env python3
"""
Сравнение качества транскрипции: Whisper large, Whisper medium, GigaAM.
Берёт N звонков из середины дня, транскрибирует каждым STT, выводит результат.

Запуск (на машине с GPU):
  ./run_local.sh python -m call_analytics.compare_whisper_gigaam
  ./run_local.sh python -m call_analytics.compare_whisper_gigaam --date 2026-03-20 --limit 10

LLM в транскрибации не участвует (только STT). Нормализация — по правилам (без USE_LLM_NORMALIZE).
"""

import argparse
import os
import sys
from pathlib import Path

# Отключаем LLM в нормализации — чистое сравнение STT
os.environ["USE_LLM_NORMALIZE"] = "0"

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description="Сравнение Whisper large / medium / GigaAM")
    parser.add_argument("--date", default="2026-03-20", help="Дата звонков (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=10, help="Количество звонков (из середины дня)")
    args = parser.parse_args()

    from database.postgresql_manager import CallAnalyticsDB

    # Берём звонки из середины дня (сначала все за дату, затем средние N)
    all_rows = CallAnalyticsDB.list_calls(
        date_from=args.date,
        date_to=args.date,
        limit=500,
    )
    n = len(all_rows)
    if n <= args.limit:
        rows = all_rows
    else:
        mid = (n - args.limit) // 2
        rows = all_rows[mid : mid + args.limit]
    call_ids = [r["id"] for r in rows if r.get("file_path")]
    if not call_ids:
        print(f"Звонков за {args.date} не найдено")
        return 1

    print(f"Сравнение Whisper large / Whisper medium / GigaAM: {len(call_ids)} звонков (середина дня)")
    print("Загрузка моделей...")

    from analyze_call_quality import (
        load_gigaam_model,
        load_stt_model,
        transcribe_audio,
        transcribe_audio_gigaam,
    )
    from config import WHISPER_MODEL_PATH_VOICE, WHISPER_DEVICE

    # Whisper large
    whisper_large = load_stt_model()
    # Whisper medium (отдельная загрузка)
    try:
        from faster_whisper import WhisperModel
        import torch
        use_cuda = torch.cuda.is_available() if WHISPER_DEVICE == "cuda" else False
        device = WHISPER_DEVICE if use_cuda else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        print(f"  Whisper medium: {WHISPER_MODEL_PATH_VOICE}")
        whisper_medium = WhisperModel(
            WHISPER_MODEL_PATH_VOICE,
            device=device,
            compute_type=compute_type,
            local_files_only=False,
        )
    except Exception as e:
        print(f"  Ошибка загрузки Whisper medium: {e}")
        whisper_medium = None
    gigaam_model = load_gigaam_model()

    base_dir = PROJECT_DIR
    results = []

    for i, call_id in enumerate(call_ids, 1):
        row = CallAnalyticsDB.get_call_with_details(call_id)
        if not row or not row.get("file_path"):
            continue
        file_path = Path(row["file_path"])
        if not file_path.is_absolute():
            file_path = base_dir / file_path
        if not file_path.exists():
            print(f"  [{i}] call_id={call_id} — файл не найден")
            continue

        print(f"\n[{i}/{len(call_ids)}] call_id={call_id} {file_path.name}")

        txt_whisper_large = ""
        txt_whisper_medium = ""
        txt_gigaam = ""

        try:
            txt_whisper_large, _, _ = transcribe_audio(file_path, whisper_large)
        except Exception as e:
            txt_whisper_large = f"[Ошибка: {e}]"

        if whisper_medium:
            try:
                txt_whisper_medium, _, _ = transcribe_audio(file_path, whisper_medium)
            except Exception as e:
                txt_whisper_medium = f"[Ошибка: {e}]"
        else:
            txt_whisper_medium = "[модель не загружена]"

        try:
            txt_gigaam, _, _ = transcribe_audio_gigaam(file_path, gigaam_model, normalize_with_llm=False)
        except Exception as e:
            txt_gigaam = f"[Ошибка: {e}]"

        results.append({
            "call_id": call_id,
            "file": file_path.name,
            "whisper_large": txt_whisper_large or "",
            "whisper_medium": txt_whisper_medium or "",
            "gigaam": txt_gigaam or "",
        })

        print("--- Whisper large ---")
        print((txt_whisper_large or "(пусто)")[:400])
        print("--- Whisper medium ---")
        print((txt_whisper_medium or "(пусто)")[:400])
        print("--- GigaAM ---")
        print((txt_gigaam or "(пусто)")[:400])

    # Сохранение в файл
    out_path = PROJECT_DIR / "storage" / "compare_whisper_gigaam.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(f"\n{'='*60}\ncall_id={r['call_id']} {r['file']}\n")
            f.write(f"{'='*60}\n--- Whisper large ---\n{r['whisper_large']}\n")
            f.write(f"--- Whisper medium ---\n{r['whisper_medium']}\n")
            f.write(f"--- GigaAM ---\n{r['gigaam']}\n")
    print(f"\nРезультат сохранён: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
