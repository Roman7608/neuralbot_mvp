#!/usr/bin/env python3
"""
Аналитика ОП для всех .wav в Analytic/SALE/: оба канала смешиваются в моно, транскрипция на GPU.
Оценка по критериям p3–p16, запись в Analytic/SALE/quality.xlsx.
Шаблон критериев берётся из существующего Analytic/SALE/quality.xlsx (если есть).
Листы: «Оценка качества» (сводка + критерии), «Файл 1» … «Файл N» (полная транскрипция).

Запуск (во внешнем терминале для GPU):
  .venv/bin/python run_sale_op_analytics.py
  .venv/bin/python run_sale_op_analytics.py --segment-by-tones   # сегментация по гудкам, результат в quality_probe.xlsx
  USE_GIGAAM=1 .venv/bin/python run_sale_op_analytics.py         # STT: GigaAM (результат нормализуется так же, как для Whisper)
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Any

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

SALE_DIR = PROJECT_DIR / "Analytic" / "SALE"
OUTPUT_FILE = SALE_DIR / "quality.xlsx"
PROBE_FILE = SALE_DIR / "quality_probe.xlsx"
EMPLOYEE_CHANNEL = 1

import os
from analyze_op_mono import run_op_mono
from analyze_call_quality import EVALUATION_CRITERIA, load_stt_model, load_gigaam_model


def _audio_files(sale_dir: Path) -> List[Path]:
    """Все .wav в папке, отсортированные по числу в имени (1.wav, 2.wav, …, 10.wav), иначе по строке."""
    out = list(sale_dir.glob("*.wav"))

    def _sort_key(p: Path):
        stem = p.stem
        try:
            return (0, int(stem), p.name)
        except ValueError:
            return (1, 0, p.name)

    return sorted(out, key=_sort_key)


def read_criteria_from_existing(excel_path: Path, sheet_name: str = "Оценка качества") -> Optional[List[List[Any]]]:
    """Читает блок «СИСТЕМА ОЦЕНКИ КРИТЕРИЕВ» из существующего quality.xlsx. Возвращает список строк или None."""
    if not excel_path.exists():
        return None
    try:
        wb = load_workbook(excel_path, read_only=True, data_only=True)
        if sheet_name not in wb.sheetnames:
            return None
        ws = wb[sheet_name]
        start_row = None
        for i, row in enumerate(ws.iter_rows(min_row=1, values_only=True), start=1):
            if row and isinstance(row[0], str) and row[0].strip().startswith("СИСТЕМА ОЦЕНКИ КРИТЕРИЕВ"):
                start_row = i
                break
        if start_row is None:
            return None
        return [list(row) for row in ws.iter_rows(min_row=start_row, max_row=ws.max_row, values_only=True)]
    except Exception:
        return None


def copy_criteria_block(
    writer: pd.ExcelWriter,
    criteria_rows: Optional[List[List[Any]]],
    sheet_name: str = "Оценка качества",
) -> None:
    """Добавляет блок критериев в конец листа: из criteria_rows или fallback из EVALUATION_CRITERIA."""
    ws_out = writer.sheets.get(sheet_name)
    if ws_out is None:
        return
    insert_row = ws_out.max_row + 2
    if criteria_rows:
        for r_offset, row in enumerate(criteria_rows):
            for c_idx, value in enumerate(row, start=1):
                ws_out.cell(row=insert_row + r_offset, column=c_idx, value=value)
    else:
        _append_criteria_fallback(writer, sheet_name)


def _append_criteria_fallback(writer: pd.ExcelWriter, sheet_name: str) -> None:
    """Добавляет минимальный блок критериев из EVALUATION_CRITERIA, если шаблона нет."""
    ws = writer.sheets.get(sheet_name)
    if ws is None:
        return
    row = ws.max_row + 2
    ws.cell(row=row, column=1, value="СИСТЕМА ОЦЕНКИ КРИТЕРИЕВ (3–16)")
    row += 1
    for key, meta in EVALUATION_CRITERIA.items():
        ws.cell(row=row, column=1, value=meta.get("name", key))
        ws.cell(row=row, column=2, value=meta.get("description", ""))
        row += 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Аналитика ОП: все .wav → оценка качества")
    parser.add_argument("--segment-by-tones", action="store_true", help="Сегментация по гудкам (вариант C), результат в quality_probe.xlsx")
    args = parser.parse_args()

    if not SALE_DIR.exists():
        print(f"Папка не найдена: {SALE_DIR}")
        return 1

    files = _audio_files(SALE_DIR)
    if not files:
        print(f"В {SALE_DIR} нет файлов .wav")
        return 1

    use_segment_by_tones = args.segment_by_tones
    output_path = PROBE_FILE if use_segment_by_tones else OUTPUT_FILE
    if use_segment_by_tones:
        print("Режим: сегментация по гудкам, результат → quality_probe.xlsx", flush=True)

    # Загрузка модели: USE_GIGAAM=1 — GigaAM (с нормализацией), иначе Whisper
    use_gigaam = os.environ.get("USE_GIGAAM", "").strip().lower() in ("1", "true", "yes")
    if use_gigaam:
        print("STT: GigaAM-v3 (нормализация применяется)", flush=True)
        model = load_gigaam_model()
    else:
        print("STT: Whisper (нормализация применяется)", flush=True)
        model = load_stt_model()

    # Шаблон критериев из quality.xlsx (для сравнения с предыдущей итерацией оба файла используют один блок)
    criteria_rows = read_criteria_from_existing(OUTPUT_FILE, "Оценка качества")
    if criteria_rows:
        print("Шаблон критериев взят из quality.xlsx", flush=True)
    else:
        print("Шаблон критериев не найден, будет использована встроенная система критериев", flush=True)

    print(f"Найдено файлов: {len(files)}. Обработка: оба канала (моно), ОП...", flush=True)

    rows: List[dict] = []
    transcripts_for_sheets: List[dict] = []  # {"file_number": int, "file_name": str, "transcript": str}

    for i, path in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] {path.name} ...", flush=True)
        result = run_op_mono(
            path,
            employee_channel=EMPLOYEE_CHANNEL,
            model=model,
            use_both_channels=True,
            use_mask_transfer_tones=not use_segment_by_tones,
            use_segment_by_tones=use_segment_by_tones,
            use_gigaam=use_gigaam,
        )

        transcript = result.get("transcript", "")
        transcripts_for_sheets.append({
            "file_number": i,
            "file_name": path.name,
            "transcript": transcript or "(пусто)",
        })

        if "error" in result:
            print(f"  Ошибка: {result['error']}", flush=True)
            row = {"Файл": path.name, "Менеджер": "—", "Клиент": "—", "Общий балл": 0.0}
            for key in EVALUATION_CRITERIA:
                row[EVALUATION_CRITERIA[key]["name"]] = 0.0
            rows.append(row)
            continue

        scores = result.get("scores", {})
        row = {
            "Файл": path.name,
            "Менеджер": result.get("manager_name", "—"),
            "Клиент": result.get("customer_name", "—"),
        }
        for key in EVALUATION_CRITERIA:
            row[EVALUATION_CRITERIA[key]["name"]] = round(scores.get(key, 0.0), 2)
        row["Общий балл"] = round(scores.get("total_score", 0.0), 2)
        rows.append(row)
        print(f"  Балл: {scores.get('total_score', 0):.2f}/5.0", flush=True)

    if not rows:
        print("Нет результатов для записи.")
        return 1

    # Порядок колонок
    col_order = ["Файл", "Менеджер", "Клиент"] + [EVALUATION_CRITERIA[k]["name"] for k in EVALUATION_CRITERIA] + ["Общий балл"]
    df = pd.DataFrame(rows)[col_order]

    SALE_DIR.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl", mode="w") as writer:
        df.to_excel(writer, sheet_name="Оценка качества", index=False, startrow=0)
        copy_criteria_block(writer, criteria_rows, "Оценка качества")

        # Листы с полной транскрипцией по каждому файлу (колонка B — перенос по словам, ширина ~50 символов)
        TRANSCRIPT_COL_WIDTH = 50
        for trans in transcripts_for_sheets:
            sheet_name = f"Файл {trans['file_number']}"
            if len(sheet_name) > 31:
                sheet_name = sheet_name[:31]
            df_t = pd.DataFrame([
                {"Тип": "Полная транскрипция (канал 1)", "Текст": trans["transcript"]},
            ])
            df_t.to_excel(writer, sheet_name=sheet_name, index=False)
            ws = writer.sheets[sheet_name]
            ws.column_dimensions["B"].width = TRANSCRIPT_COL_WIDTH
            for row in range(2, ws.max_row + 1):
                cell = ws.cell(row=row, column=2)
                if cell.value:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")

    print(f"\nРезультаты записаны в {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
