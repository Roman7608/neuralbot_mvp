"""
Сопоставление записи звонка в analytics (calls, импорт SPRecord) с сессией голосового бота по времени,
чтобы подставить caller_phone из voice_bot_sessions.

Окно совпадения: VOICE_BOT_MATCH_WINDOW_SEC (по умолчанию 180 с).
Часовой пояс якоря звонка в карточке: VOICE_BOT_MATCH_TZ (по умолчанию Europe/Samara).
Якорь сессии бота: последнее voice_bot_transfer_events.logged_at, иначе ended_at, иначе started_at.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

logger = logging.getLogger(__name__)


def _match_window_seconds() -> float:
    try:
        return float(os.environ.get("VOICE_BOT_MATCH_WINDOW_SEC", "180"))
    except ValueError:
        return 180.0


def _match_tz_name() -> str:
    return (os.environ.get("VOICE_BOT_MATCH_TZ") or "Europe/Samara").strip() or "Europe/Samara"


def _parse_call_datetime(call_date: Any, call_time: Any) -> Optional[datetime]:
    """Дата/время звонка из строки calls → timezone-aware datetime."""
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(_match_tz_name())
        if hasattr(call_date, "year"):
            d = call_date
        else:
            ds = str(call_date).strip()[:10]
            d = date.fromisoformat(ds)
        if hasattr(call_time, "hour"):
            t = call_time
        else:
            ts = str(call_time).strip()
            parts = ts.replace(".", ":").split(":")
            h = int(parts[0]) if parts and parts[0].isdigit() else 0
            m = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            sec = int(parts[2]) if len(parts) > 2 and parts[2][:2].isdigit() else 0
            t = time(h, m, sec)
        return datetime.combine(d, t, tzinfo=tz)
    except Exception as e:
        logger.warning("voice_bot_call_match: не разобрать дату/время звонка: %s", e)
        return None


def _delta_seconds_anchor_to_call(anchor_ts: Any, call_date: Any, call_time: Any) -> Optional[float]:
    """Разница anchor_ts − время звонка (секунды); знак совпадает с SQL delta для того же звонка."""
    call_dt = _parse_call_datetime(call_date, call_time)
    if call_dt is None or anchor_ts is None:
        return None
    try:
        if getattr(anchor_ts, "tzinfo", None):
            call_dt = call_dt.astimezone(anchor_ts.tzinfo)
        return float((anchor_ts - call_dt).total_seconds())
    except Exception:
        return None


def find_voice_bot_session_for_call_time(
    call_date: Any,
    call_time: Any,
    window_seconds: Optional[float] = None,
    exclude_call_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Ищет сессию голосового бота с заполненным caller_phone, ближайшую по времени к call_date/call_time.

    Returns:
        {"voice_bot_session_id": int, "caller_phone": str, "delta_sec": float} или None.
    """
    call_dt = _parse_call_datetime(call_date, call_time)
    if call_dt is None:
        return None
    w = float(window_seconds) if window_seconds is not None else _match_window_seconds()

    try:
        from database.postgresql_manager import CallAnalyticsDB, PostgreSQLManager
    except ImportError:
        logger.warning("voice_bot_call_match: PostgreSQLManager недоступен")
        return None

    sql = """
WITH anchors AS (
  SELECT s.id AS sid,
         TRIM(s.caller_phone) AS caller_phone,
         COALESCE(
           (SELECT MAX(te.logged_at) FROM voice_bot_transfer_events te WHERE te.session_id = s.id),
           s.ended_at,
           s.started_at
         ) AS anchor_ts
  FROM voice_bot_sessions s
  WHERE s.caller_phone IS NOT NULL AND LENGTH(TRIM(s.caller_phone)) > 0
)
SELECT sid, caller_phone, anchor_ts,
       EXTRACT(EPOCH FROM (anchor_ts - %(call_ts)s::timestamptz)) AS delta_sec
FROM anchors
WHERE anchor_ts IS NOT NULL
  AND ABS(EXTRACT(EPOCH FROM (anchor_ts - %(call_ts)s::timestamptz))) <= %(window)s
ORDER BY ABS(EXTRACT(EPOCH FROM (anchor_ts - %(call_ts)s::timestamptz))) ASC
LIMIT 15
"""

    try:
        params: Dict[str, Any] = {"call_ts": call_dt, "window": w}
        with PostgreSQLManager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows: List[Tuple[Any, ...]] = cur.fetchall()
            for row in rows:
                sid, phone, anchor_ts, delta_sec_raw = row[0], row[1], row[2], row[3]
                delta_sec = float(delta_sec_raw) if delta_sec_raw is not None else 0.0
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, call_date, call_time FROM calls WHERE voice_bot_session_id = %s LIMIT 1",
                        (sid,),
                    )
                    lk = cur.fetchone()
                if lk is None or (exclude_call_id is not None and lk[0] == exclude_call_id):
                    return {
                        "voice_bot_session_id": int(sid),
                        "caller_phone": str(phone).strip(),
                        "delta_sec": delta_sec,
                    }
                oid, ocd, oct = int(lk[0]), lk[1], lk[2]
                d_linked = _delta_seconds_anchor_to_call(anchor_ts, ocd, oct)
                if d_linked is None:
                    continue
                if abs(delta_sec) + 1e-3 < abs(d_linked):
                    CallAnalyticsDB.update_call_caller_phone_link(oid, None, None)
                    return {
                        "voice_bot_session_id": int(sid),
                        "caller_phone": str(phone).strip(),
                        "delta_sec": delta_sec,
                    }
        return None
    except Exception as e:
        logger.warning("voice_bot_call_match: запрос БД: %s", e, exc_info=True)
        return None


def run_batch(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    call_ids: Optional[list] = None,
    dry_run: bool = False,
) -> Tuple[int, int]:
    """Пробует матчинг для звонков без caller_phone. Возвращает (обработано, с матчем)."""
    from database.postgresql_manager import CallAnalyticsDB, PostgreSQLManager

    matched = 0
    processed = 0
    conditions = ["c.caller_phone IS NULL"]
    params: list = []
    if call_ids:
        placeholders = ",".join(["%s"] * len(call_ids))
        conditions.append(f"c.id IN ({placeholders})")
        params.extend(call_ids)
    else:
        conditions.append("COALESCE(c.call_source, 'sprecord') = 'sprecord'")
        if date_from:
            conditions.append("c.call_date >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("c.call_date <= %s")
            params.append(date_to)

    where_sql = " AND ".join(conditions)
    q = f"SELECT c.id, c.call_date, c.call_time FROM calls c WHERE {where_sql} ORDER BY c.call_date, c.call_time"

    with PostgreSQLManager.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(q, params)
            rows = cur.fetchall()

    for row in rows:
        cid = row[0]
        processed += 1
        m = find_voice_bot_session_for_call_time(row[1], row[2], exclude_call_id=cid)
        if not m:
            continue
        if dry_run:
            print(f"call_id={cid} -> session={m['voice_bot_session_id']} phone={m['caller_phone']} d={m['delta_sec']:.1f}s")
            matched += 1
            continue
        if CallAnalyticsDB.update_call_caller_phone_link(
            cid,
            m["caller_phone"],
            m["voice_bot_session_id"],
        ):
            matched += 1

    return processed, matched


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="Матчинг caller_phone из voice_bot_sessions для записей calls")
    p.add_argument("--call-ids", help="Список id через запятую")
    p.add_argument("--date-from", help="YYYY-MM-DD")
    p.add_argument("--date-to", help="YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true", help="Только показать совпадения, без записи в БД")
    args = p.parse_args()

    ids = None
    if args.call_ids:
        ids = [int(x.strip()) for x in args.call_ids.split(",") if x.strip()]

    proc, m = run_batch(date_from=args.date_from, date_to=args.date_to, call_ids=ids, dry_run=args.dry_run)
    print(f"Обработано: {proc}, с подстановкой номера: {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
