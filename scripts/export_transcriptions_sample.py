#!/usr/bin/env python3
"""Экспорт транскрипций звонков за дату в текстовый файл."""
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from database.postgresql_manager import CallAnalyticsDB, PostgreSQLManager


def main():
    date_str = "2026-03-20"
    limit = 10
    out_path = PROJECT_DIR / "storage" / "transcriptions_2026-03-20_10sample.txt"

    rows = CallAnalyticsDB.list_calls(date_from=date_str, date_to=date_str, limit=limit)
    if not rows:
        print("Звонков не найдено")
        return 1

    lines = [f"=== Транскрипции за {date_str} (первые {limit} звонков) ===\n"]
    for i, row in enumerate(rows, 1):
        call_id = row["id"]
        detail = CallAnalyticsDB.get_call_with_details(call_id)
        if not detail:
            continue
        tr = detail.get("transcription")
        text = (tr.get("transcription_text") or "(нет транскрипции)") if tr else "(нет транскрипции)"
        fname = row.get("file_name", "?")
        lines.append(f"--- [{i}] call_id={call_id} | {fname} ---")
        lines.append(text)
        lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Записано: {out_path}")
    print("\n" + "=" * 60 + "\n")
    print(out_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
