#!/usr/bin/env python3
"""
Эксперименты с одним файлом ОП (по умолчанию 6.wav), результат в Analytic/SALE/quality_probe.xlsx.
Используется для проверки транскрипции после гудков перевода (например, эксперимент E: без маски тонов).

Запуск:
  .venv/bin/python run_sale_probe.py                    # файл 6, с маской гудков
  .venv/bin/python run_sale_probe.py --no-mask           # эксперимент E: без маски
  .venv/bin/python run_sale_probe.py --segment-by-tones # вариант C: резать по гудкам, сегменты отдельно
  .venv/bin/python run_sale_probe.py --file 4 --no-mask
"""

import argparse
import sys
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

SALE_DIR = PROJECT_DIR / "Analytic" / "SALE"
QUALITY_FILE = SALE_DIR / "quality.xlsx"
PROBE_FILE = SALE_DIR / "quality_probe.xlsx"
EMPLOYEE_CHANNEL = 1

from analyze_op_mono import run_op_mono
from analyze_call_quality import EVALUATION_CRITERIA, load_stt_model
from run_sale_op_analytics import read_criteria_from_existing, copy_criteria_block, _append_criteria_fallback


def main() -> int:
    parser = argparse.ArgumentParser(description="ОП: один файл в quality_probe.xlsx")
    parser.add_argument("--file", type=int, default=6, help="Номер файла (например 6 для 6.wav)")
    parser.add_argument("--no-mask", action="store_true", help="Не маскировать тоны перевода (эксперимент E)")
    parser.add_argument("--segment-by-tones", action="store_true", help="Вариант C: резать по гудкам, транскрибировать сегменты отдельно")
    args = parser.parse_args()

    if not SALE_DIR.exists():
        print(f"Папка не найдена: {SALE_DIR}")
        return 1

    wav_name = f"{args.file}.wav"
    wav_path = SALE_DIR / wav_name
    if not wav_path.exists():
        print(f"Файл не найден: {wav_path}")
        return 1

    use_mask = not args.no_mask and not args.segment_by_tones
    if args.segment_by_tones:
        print(f"Файл: {wav_name}, режим: сегментация по гудкам (вариант C)", flush=True)
    else:
        print(f"Файл: {wav_name}, маска гудков: {'да' if use_mask else 'нет (эксперимент E)'}", flush=True)

    model = load_stt_model()
    criteria_rows = read_criteria_from_existing(QUALITY_FILE, "Оценка качества")
    if criteria_rows:
        print("Шаблон критериев взят из quality.xlsx", flush=True)
    else:
        print("Используется встроенная система критериев", flush=True)

    result = run_op_mono(
        wav_path,
        employee_channel=EMPLOYEE_CHANNEL,
        model=model,
        use_both_channels=True,
        use_mask_transfer_tones=use_mask,
        use_segment_by_tones=args.segment_by_tones,
    )

    transcript = result.get("transcript", "") or "(пусто)"
    if "error" in result:
        print(f"Ошибка: {result['error']}", flush=True)
        row = {"Файл": wav_name, "Менеджер": "—", "Клиент": "—", "Общий балл": 0.0}
        for key in EVALUATION_CRITERIA:
            row[EVALUATION_CRITERIA[key]["name"]] = 0.0
    else:
        scores = result.get("scores", {})
        row = {
            "Файл": wav_name,
            "Менеджер": result.get("manager_name", "—"),
            "Клиент": result.get("customer_name", "—"),
        }
        for key in EVALUATION_CRITERIA:
            row[EVALUATION_CRITERIA[key]["name"]] = round(scores.get(key, 0.0), 2)
        row["Общий балл"] = round(scores.get("total_score", 0.0), 2)
        print(f"Балл: {scores.get('total_score', 0):.2f}/5.0", flush=True)

    col_order = ["Файл", "Менеджер", "Клиент"] + [EVALUATION_CRITERIA[k]["name"] for k in EVALUATION_CRITERIA] + ["Общий балл"]
    df = pd.DataFrame([row])[col_order]

    SALE_DIR.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(PROBE_FILE, engine="openpyxl", mode="w") as writer:
        df.to_excel(writer, sheet_name="Оценка качества", index=False, startrow=0)
        copy_criteria_block(writer, criteria_rows, "Оценка качества")

        sheet_name = f"Файл {args.file}"[:31]
        df_t = pd.DataFrame([
            {"Тип": "Полная транскрипция (канал 1)", "Текст": transcript},
        ])
        df_t.to_excel(writer, sheet_name=sheet_name, index=False)
        ws = writer.sheets[sheet_name]
        ws.column_dimensions["B"].width = 50
        for r in range(2, ws.max_row + 1):
            c = ws.cell(row=r, column=2)
            if c.value:
                c.alignment = Alignment(wrap_text=True, vertical="top")

    print(f"Результат записан в {PROBE_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
