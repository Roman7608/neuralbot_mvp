"""
Пересчёт оценки качества в existing `quality2.xlsx` БЕЗ повторной транскрипции.

Что делает скрипт:
- читает Analytic/quality2.xlsx,
- для файлов 1–5 берёт листы «Файл 1»…«Файл 5», читает полную транскрипцию
  (строка с Роль="Полная транскрипция"),
- вызывает evaluate_call_auto(full_transcript) из analyze_call_quality.py (правила или LLM при USE_LLM_EVALUATE=1),
- обновляет в листе «Оценка качества» строки 1–5 (критерии и «Общий балл»).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from analyze_call_quality import evaluate_call_auto


PROJECT_DIR = Path(__file__).resolve().parent
QUALITY_FILE = PROJECT_DIR / "Analytic" / "quality2.xlsx"

# Маппинг ключей scores -> названия колонок в сводной таблице
SCORE_TO_COLUMN = {
    "p3_intro": "3. Представление ПК",
    "p4_ask_name_form": "4. Как обращаться по имени",
    "p5_name_usage_3plus": "5. Имя ≥3 раз",
    "p6_car_interest": "6. Какой авто интересует",
    "p7_familiar_with_car": "7. Знаком ли с авто",
    "p8_for_whom": "8. Для кого авто",
    "p9_purchase_timing": "9. Сроки покупки",
    "p10_payment_form": "10. Форма оплаты",
    "p11_current_car": "11. Текущий авто",
    "p12_invite_to_dc": "12. Приглашение в ДЦ",
    "p13_test_drive": "13. Тест-драйв",
    "p14_ask_contacts": "14. Запрос контактов",
    "p15_send_contacts": "15. Отправка контактов",
    "p16_thanks": "16. Благодарность",
}


def _load_full_transcript_from_sheet(sheet_name: str) -> Optional[str]:
    """Читает полную транскрипцию из листа (строка с Роль='Полная транскрипция')."""
    df = pd.read_excel(QUALITY_FILE, sheet_name=sheet_name)
    if df.empty:
        return None

    role_col = text_col = None
    for c in df.columns:
        cl = c.lower()
        if "роль" in cl or "role" in cl:
            role_col = c
        if "реплика" in cl or "текст" in cl or "text" in cl:
            text_col = c

    if role_col is None or text_col is None:
        return None

    for _, row in df.iterrows():
        raw_role = str(row.get(role_col, "")).strip().lower()
        if "полная транскрипция" in raw_role:
            text = str(row.get(text_col, "")).strip()
            return text if text else None

    # Если строки «Полная транскрипция» нет — склеиваем все реплики
    texts = []
    for _, row in df.iterrows():
        text = str(row.get(text_col, "")).strip()
        if text and "полная транскрипция" not in str(row.get(role_col, "")).lower():
            texts.append(text)
    return " ".join(texts) if texts else None


def recalc_quality() -> None:
    if not QUALITY_FILE.exists():
        raise FileNotFoundError(f"Файл {QUALITY_FILE} не найден.")

    print(f"=== Пересчёт оценки качества в {QUALITY_FILE.name} ===")

    summary_df = pd.read_excel(QUALITY_FILE, sheet_name="Оценка качества")

    for file_idx in range(1, 6):
        sheet_name = f"Файл {file_idx}"
        print(f"\n--- Файл {file_idx}: {sheet_name} ---")

        transcript = _load_full_transcript_from_sheet(sheet_name)
        if not transcript or not transcript.strip():
            print("  ⚠️ Нет полной транскрипции для оценки, пропускаем.")
            continue

        # Файл 1 — тестовый, не оценивается; в сводной строки 0–3 = файлы 2–5
        row_idx = file_idx - 2 if file_idx >= 2 else -1
        if row_idx < 0:
            print("  ⚠️ Файл 1 — тестовый, пропускаем.")
            continue
        if row_idx >= len(summary_df):
            print(f"  ⚠️ В сводной таблице нет строки для файла {file_idx}, пропускаем.")
            continue

        scores = evaluate_call_auto(transcript)

        for key, col in SCORE_TO_COLUMN.items():
            if key in scores and col in summary_df.columns:
                summary_df.at[row_idx, col] = f"{scores[key]:.2f}"

        if "total_score" in scores and "Общий балл" in summary_df.columns:
            summary_df.at[row_idx, "Общий балл"] = f"{scores['total_score']:.2f}/5.0"

        print(f"  Общий балл: {scores.get('total_score', 0):.2f}/5.0")
        print("  ✅ Оценка пересчитана.")

    with pd.ExcelWriter(QUALITY_FILE, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        summary_df.to_excel(writer, sheet_name="Оценка качества", index=False)

    print(f"\n✅ Пересчёт завершён, данные обновлены в {QUALITY_FILE.name}")


if __name__ == "__main__":
    recalc_quality()

