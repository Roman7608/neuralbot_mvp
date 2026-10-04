#!/usr/bin/env python3
"""
Разовый импорт записей звонков за 7 марта из локального postgres (5432) в Docker postgres (5433).
После импорта запустить: USE_GIGAAM=1 USE_LLM_NORMALIZE=1 ./run_local.sh python -m call_analytics.retranscribe_reclassify --date-from 2026-03-07 --date-to 2026-03-07
"""
import os
import sys

# Настройки
SRC = {"host": "localhost", "port": 5432, "user": "analytics_user", "password": "TOP", "database": "vikingi_analytics"}
DST = {"host": "localhost", "port": 5433, "user": "analytics_user", "password": os.environ.get("POSTGRES_PASSWORD", "TOP"), "database": "vikingi_analytics"}
DATE = "2026-03-07"

def main():
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        print("Требуется psycopg2: pip install psycopg2-binary")
        sys.exit(1)

    print(f"Чтение звонков за {DATE} из {SRC['host']}:{SRC['port']}...")
    conn_src = psycopg2.connect(**SRC)
    cur_src = conn_src.cursor(cursor_factory=RealDictCursor)
    cur_src.execute(
        "SELECT file_path, file_name, internal_number, call_date, call_time, duration_seconds, "
        "file_size_bytes, COALESCE(department,'OTHER') as department, COALESCE(source_type,'auto') as source_type "
        "FROM calls WHERE call_date = %s ORDER BY call_time",
        (DATE,),
    )
    rows = cur_src.fetchall()
    conn_src.close()
    print(f"  Найдено: {len(rows)} записей")

    if not rows:
        print("Нет записей для импорта.")
        sys.exit(0)

    print(f"Импорт в {DST['host']}:{DST['port']}...")
    conn_dst = psycopg2.connect(**DST)
    cur_dst = conn_dst.cursor()
    inserted = 0
    for r in rows:
        try:
            cur_dst.execute(
                """INSERT INTO calls (file_path, file_name, internal_number, call_date, call_time,
                    duration_seconds, file_size_bytes, department, source_type, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                ON CONFLICT (file_path) DO NOTHING""",
                (
                    r["file_path"], r["file_name"], r["internal_number"], r["call_date"], r["call_time"],
                    r["duration_seconds"], r["file_size_bytes"], r["department"], r["source_type"],
                ),
            )
            if cur_dst.rowcount:
                inserted += 1
        except Exception as e:
            print(f"  Ошибка для {r['file_path']}: {e}")
    conn_dst.commit()
    conn_dst.close()
    print(f"  Импортировано: {inserted} записей")
    print("")
    print("Теперь запустите транскрибацию:")
    print("  USE_GIGAAM=1 USE_LLM_NORMALIZE=1 ./run_local.sh python -m call_analytics.retranscribe_reclassify --date-from 2026-03-07 --date-to 2026-03-07")

if __name__ == "__main__":
    main()
