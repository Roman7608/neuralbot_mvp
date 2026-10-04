#!/usr/bin/env python3
"""
Очистка старых записей звонков: удаление из БД и файлов на HDD.

- Удаляет звонки старше N дней (по умолчанию 61).
- Сначала удаляет файлы с диска, затем записи из calls (CASCADE очистит транскрипции, оценки и т.д.).
- Путь к файлам берётся из calls.file_path.

Запуск:
  python3 scripts/cleanup_old_calls.py [--days 61]
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

try:
    from postgresql_config import get_analytics_connection_string
    PG_DSN = get_analytics_connection_string()
except ImportError:
    PG_DSN = "dbname=vikingi_analytics user=analytics_user password=TOP host=localhost port=5433"

import psycopg2


def main() -> int:
    parser = argparse.ArgumentParser(description="Удаление старых записей звонков из БД и с HDD.")
    parser.add_argument(
        "--days",
        type=int,
        default=61,
        help="Удалять записи старше N дней (по умолчанию 61)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Только показать, что будет удалено")
    args = parser.parse_args()

    cutoff = dt.date.today() - dt.timedelta(days=args.days)
    print(f"🧹 Очистка записей старше {args.days} дней (call_date < {cutoff})")

    conn = psycopg2.connect(PG_DSN)
    deleted_files = 0
    deleted_db = 0
    errors = []
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, file_path FROM calls WHERE call_date < %s ORDER BY call_date",
                    (cutoff,),
                )
                rows = cur.fetchall()
        if not rows:
            print("  Записей для удаления нет.")
            return 0
        print(f"  Найдено записей: {len(rows)}")

        for call_id, file_path in rows:
            if args.dry_run:
                print(f"  [dry-run] {file_path}")
                deleted_files += 1
                continue
            p = Path(file_path)
            if p.exists():
                try:
                    p.unlink()
                    deleted_files += 1
                except OSError as e:
                    errors.append(f"{file_path}: {e}")
            else:
                deleted_files += 1  # файла нет — всё равно удалим из БД

        if not args.dry_run:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM calls WHERE call_date < %s", (cutoff,))
                    deleted_db = cur.rowcount

        if errors:
            for e in errors:
                print(f"  ⚠️ {e}")
        print(f"  ✅ Удалено файлов: {deleted_files}, записей в БД: {deleted_db if not args.dry_run else '(dry-run)'}")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
