"""
Время для отчётов админки: тот же часовой пояс, что и сессия БД (POSTGRESQL_SESSION_TIMEZONE).
Все datetime в JSON API отдаются в этой зоне с явным смещением (+04:00 и т.д.).
"""

from __future__ import annotations

import calendar
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

_report_zone = None  # type: Optional[ZoneInfo]


def report_timezone_name() -> str:
    if os.environ.get("ADMIN_REPORT_TIMEZONE"):
        return os.environ["ADMIN_REPORT_TIMEZONE"].strip()
    try:
        from postgresql_config import POSTGRESQL_SESSION_TIMEZONE

        return str(POSTGRESQL_SESSION_TIMEZONE).strip()
    except ImportError:
        pass
    return (os.environ.get("TZ") or "Europe/Samara").strip()


def report_zone() -> ZoneInfo:
    global _report_zone
    if _report_zone is None:
        _report_zone = ZoneInfo(report_timezone_name())
    return _report_zone


def now_in_report_zone() -> datetime:
    return datetime.now(report_zone())


def format_datetime_for_report(dt: datetime) -> str:
    """Дата/время для отчётов: в зоне сервера, с offset в ISO."""
    z = report_zone()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=z)
    else:
        dt = dt.astimezone(z)
    return dt.isoformat()


def _add_months(d: date, months: int) -> date:
    m_index = d.month - 1 + months
    y = d.year + m_index // 12
    m = m_index % 12 + 1
    last = calendar.monthrange(y, m)[1]
    day = min(d.day, last)
    return date(y, m, day)


def _monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _sunday_of_week(d: date) -> date:
    return _monday_of_week(d) + timedelta(days=6)


def build_admin_config_payload() -> Dict[str, Any]:
    """
    Для GET /api/admin_config: календарные даты в TZ отчёта и периоды для списков звонков.
    """
    now = now_in_report_zone()
    today = now.date()
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=7)
    first = today.replace(day=1)
    if today.month == 12:
        next_m = date(today.year + 1, 1, 1)
    else:
        next_m = date(today.year, today.month + 1, 1)
    month_last = next_m - timedelta(days=1)
    call_periods: Dict[str, Dict[str, str]] = {
        "1w": {"date_from": week_ago.isoformat(), "date_to": today.isoformat()},
    }
    for key, m in [("1", 1), ("3", 3), ("6", 6), ("12", 12)]:
        call_periods[key] = {
            "date_from": _add_months(today, -m).isoformat(),
            "date_to": today.isoformat(),
        }
    try:
        from config import USE_LEGACY_CLASSIFY_TRANSCRIPT

        transcript_classifier = "legacy" if USE_LEGACY_CLASSIFY_TRANSCRIPT else "v2"
    except ImportError:
        transcript_classifier = "v2"
    return {
        "report_timezone": report_timezone_name(),
        "transcript_classifier": transcript_classifier,
        "today": today.isoformat(),
        "yesterday": yesterday.isoformat(),
        "week_ago": week_ago.isoformat(),
        "month_first": first.isoformat(),
        "month_last": month_last.isoformat(),
        "week_monday": _monday_of_week(today).isoformat(),
        "week_sunday": _sunday_of_week(today).isoformat(),
        "call_periods": call_periods,
    }
