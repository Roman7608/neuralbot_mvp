"""Местное время дилера (server7 / Europe/Samara), не UTC и не Москва."""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo


def dealer_tz_name() -> str:
    try:
        from postgresql_config import POSTGRESQL_SESSION_TIMEZONE

        name = str(POSTGRESQL_SESSION_TIMEZONE).strip()
        if name:
            return name
    except Exception:
        pass
    return (os.environ.get("POSTGRESQL_SESSION_TIMEZONE") or os.environ.get("TZ") or "Europe/Samara").strip()


def dealer_tz() -> ZoneInfo:
    return ZoneInfo(dealer_tz_name())


def dealer_local_now() -> datetime:
    """Текущие дата/время с tz (Europe/Samara на server7)."""
    return datetime.now(dealer_tz())


def dealer_local_now_naive() -> datetime:
    """Naive datetime в местных часах — для date_parser и сравнения с датами слотов."""
    return dealer_local_now().replace(tzinfo=None)


def dealer_local_now_iso() -> str:
    """ISO с оффсетом (+04:00), для JSONL и логов."""
    return dealer_local_now().isoformat(timespec="seconds")
