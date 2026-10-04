#!/usr/bin/env python3
"""
Быстрая проверка по уже сохранённым транскриптам в Analytic/SALE/quality.xlsx:
читает листы 1–10 (транскрипт в B2), пересчитывает имена и оценки, выводит таблицу.
Без GigaAM — только правила.

Запуск:
  cd /home/romandemo/Vikingi && .venv/bin/python run_sale_quick_check.py
  .venv/bin/python run_sale_quick_check.py --write   # обновить quality.xlsx
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

SALE_DIR = PROJECT_DIR / "Analytic" / "SALE"
QUALITY_XLSX = SALE_DIR / "quality.xlsx"


def main():
    parser = argparse.ArgumentParser(description="Быстрая проверка имён и оценок по quality.xlsx")
    parser.add_argument("--write", action="store_true", help="Обновить quality.xlsx (имена и оценки)")
    args = parser.parse_args()

    if not QUALITY_XLSX.exists():
        print(f"Файл не найден: {QUALITY_XLSX}", file=sys.stderr)
        print("Сначала запустите: .venv/bin/python run_sale_gigaam_llm_norm.py", file=sys.stderr)
        return 1

    from analyze_call_quality import (
        extract_manager_name_from_full_transcript,
        extract_customer_name_from_full_transcript,
        evaluate_call_by_rules,
    )

    xl = pd.ExcelFile(QUALITY_XLSX)
    rows = []
    for i in range(1, 11):
        sheet = str(i)
        if sheet not in xl.sheet_names:
            continue
        df = pd.read_excel(QUALITY_XLSX, sheet_name=sheet, header=None)
        text = (df.iloc[1, 1] if df.shape[0] > 1 and df.shape[1] > 1 else "")
        text = (str(text).strip() if pd.notna(text) else "") or "(пусто)"
        fname = str(df.iloc[1, 0]) if df.shape[0] > 1 and df.shape[1] > 1 else f"{i}.wav"

        manager = extract_manager_name_from_full_transcript(text) or ""
        customer = extract_customer_name_from_full_transcript(text, manager or None) or ""
        scores = evaluate_call_by_rules(text)

        row = {
            "Файл": fname,
            "Клиент": customer,
            "Менеджер": manager,
            **{k: f"{v:.2f}" for k, v in scores.items() if k != "total_score"},
            "Балл": f"{scores['total_score']:.2f}",
        }
        rows.append(row)

    # Таблица в консоль
    df = pd.DataFrame(rows)
    cols_show = ["Файл", "Клиент", "Менеджер", "p3_intro", "p4_ask_name_form", "p5_name_usage_3plus", "Балл"]
    cols_show = [c for c in cols_show if c in df.columns]
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    pd.set_option("display.unicode.east_asian_width", True)
    print(df[cols_show].to_string(index=False))
    print()
    print("Полные оценки (3–16):")
    print(df.drop(columns=["Файл", "Клиент", "Менеджер"], errors="ignore").to_string(index=False))

    if args.write and rows:
        # Обновить лист «Оценка»
        df_full = pd.DataFrame(rows)
        rename = {
            "Клиент": "Имя клиента",
            "Менеджер": "Имя менеджера",
            "Балл": "total_score",
        }
        df_full = df_full.rename(columns=rename)
        df_full["total_score"] = df_full["total_score"].astype(str) + "/5.0"
        table_cols = ["Файл", "Имя клиента", "Имя менеджера"] + [
            c for c in df_full.columns if c.startswith("p") and c != "total_score"
        ] + ["total_score"]
        table_cols = [c for c in table_cols if c in df_full.columns]
        df_out = df_full[table_cols]
        with pd.ExcelFile(QUALITY_XLSX, engine="openpyxl") as xl_reader:
            other_sheets = {n: pd.read_excel(QUALITY_XLSX, sheet_name=n, header=None)
                           for n in xl_reader.sheet_names if n != "Оценка"}
        with pd.ExcelWriter(QUALITY_XLSX, engine="openpyxl") as writer:
            df_out.to_excel(writer, sheet_name="Оценка", index=False)
            for name, sheet_df in other_sheets.items():
                sheet_df.to_excel(writer, sheet_name=name, index=False, header=False)
        print(f"\nОбновлён: {QUALITY_XLSX}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
