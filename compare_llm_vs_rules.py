#!/usr/bin/env python3
"""
Сравнение оценки качества звонков: правила vs локальная LLM.

Читает транскрипции из Analytic/quality2.xlsx, запускает оба метода оценки
и выводит различия.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from analyze_call_quality import (
    CRITERIA_KEYS,
    EVALUATION_CRITERIA,
    evaluate_call_by_rules,
    evaluate_call_with_llm,
)

PROJECT_DIR = Path(__file__).resolve().parent
QUALITY_FILE = PROJECT_DIR / "Analytic" / "quality2.xlsx"
LLM_MODEL_PATH = os.environ.get("LLM_MODEL_PATH", "/home/romandemo/models/qwen/model-q4_K.gguf")


def _load_full_transcript_from_sheet(sheet_name: str) -> Optional[str]:
    """Читает полную транскрипцию из листа."""
    if not QUALITY_FILE.exists():
        return None
    df = pd.read_excel(QUALITY_FILE, sheet_name=sheet_name)
    if df.empty:
        return None

    role_col = text_col = None
    for c in df.columns:
        cl = c.lower()
        if "роль" in cl:
            role_col = c
        if "реплика" in cl or "текст" in cl:
            text_col = c
    if role_col is None or text_col is None:
        return None

    for _, row in df.iterrows():
        if "полная транскрипция" in str(row.get(role_col, "")).lower():
            return str(row.get(text_col, "")).strip() or None
    texts = [str(row.get(text_col, "")).strip() for _, row in df.iterrows()
             if str(row.get(text_col, "")).strip() and "полная транскрипция" not in str(row.get(role_col, "")).lower()]
    return " ".join(texts) if texts else None


def main() -> None:
    print("=" * 70)
    print("Сравнение: правила vs LLM")
    print("=" * 70)
    print(f"Файл: {QUALITY_FILE}")
    print(f"LLM:  {LLM_MODEL_PATH}")
    print()

    if not QUALITY_FILE.exists():
        print(f"❌ Файл {QUALITY_FILE} не найден.")
        return

    results = []

    for file_idx in range(2, 6):  # Файлы 2–5
        sheet_name = f"Файл {file_idx}"
        transcript = _load_full_transcript_from_sheet(sheet_name)
        if not transcript or not transcript.strip():
            print(f"[{sheet_name}] Нет транскрипции, пропуск")
            continue

        print(f"\n--- {sheet_name} ---")

        # Правила
        t0 = time.perf_counter()
        scores_rules = evaluate_call_by_rules(transcript)
        t_rules = time.perf_counter() - t0

        # LLM (общая реализация из analyze_call_quality)
        try:
            t1 = time.perf_counter()
            scores_llm = evaluate_call_with_llm(transcript, model_path=LLM_MODEL_PATH)
            t_llm = time.perf_counter() - t1
        except Exception as e:
            print(f"  ❌ LLM: {e}")
            scores_llm = None
            t_llm = 0

        total_rules = scores_rules.get("total_score", 0)
        total_llm = scores_llm.get("total_score", 0) if scores_llm else None

        print(f"  Правила: {total_rules:.2f}/5.0  ({t_rules:.2f} с)")
        if scores_llm:
            print(f"  LLM:     {total_llm:.2f}/5.0  ({t_llm:.2f} с)")
            diff = total_llm - total_rules
            print(f"  Разница: {diff:+.2f}")

            # Различия по критериям
            diffs = []
            for k in CRITERIA_KEYS:
                r = scores_rules.get(k, 0)
                l = scores_llm.get(k, 0)
                if abs(r - l) > 0.01:
                    diffs.append((k, r, l, l - r))
            if diffs:
                print("  Отличия по критериям:")
                for k, r, l, d in diffs:
                    name = EVALUATION_CRITERIA.get(k, {}).get("name", k)
                    print(f"    {name}: правила={r:.2f}, LLM={l:.2f} ({d:+.2f})")

        results.append({
            "file": sheet_name,
            "rules": total_rules,
            "llm": total_llm,
            "t_rules": t_rules,
            "t_llm": t_llm,
            "scores_rules": scores_rules,
            "scores_llm": scores_llm,
        })

    # Сводка
    print("\n" + "=" * 70)
    print("СВОДКА")
    print("=" * 70)
    valid = [r for r in results if r["llm"] is not None]
    if valid:
        avg_rules = sum(r["rules"] for r in valid) / len(valid)
        avg_llm = sum(r["llm"] for r in valid) / len(valid)
        avg_t_rules = sum(r["t_rules"] for r in valid) / len(valid)
        avg_t_llm = sum(r["t_llm"] for r in valid) / len(valid)
        print(f"Средний балл: правила {avg_rules:.2f}, LLM {avg_llm:.2f} (разница {avg_llm - avg_rules:+.2f})")
        print(f"Среднее время: правила {avg_t_rules:.3f} с, LLM {avg_t_llm:.1f} с")


if __name__ == "__main__":
    main()
