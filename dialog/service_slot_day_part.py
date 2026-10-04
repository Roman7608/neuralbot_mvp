"""Окна части дня и разбор STT для голосовой записи на ТО."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

# Утро 8–12, обед 12–14, после обеда 14–17, вечер с 17:00.
DAY_PART_WINDOWS: dict[str, tuple[int, int]] = {
    "morning": (8, 12),
    "lunch": (12, 14),
    "after_lunch": (14, 17),
    "evening": (17, 20),
    # Совместимость со старым кодом.
    "afternoon": (14, 17),
}

NEXT_DAY_PICKUP_FROM_HOUR = 17
NEXT_DAY_PICKUP_FROM_MINUTE = 30

_WEEKDAY_PATTERNS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (0, re.compile(r"\b(?:понедельник|дельник)\b", re.IGNORECASE)),
    (1, re.compile(r"\b(?:вторник|[аеёиэ]*торник)\b", re.IGNORECASE)),
    (2, re.compile(r"\b(?:среда|среду|реда)\b", re.IGNORECASE)),
    (3, re.compile(r"\b(?:четверг|етверг)\b", re.IGNORECASE)),
    (4, re.compile(r"\b(?:пятниц[ау]|тниц[ау])\b", re.IGNORECASE)),
    (5, re.compile(r"\b(?:суббот[ау]|ббот[ау])\b", re.IGNORECASE)),
    (
        6,
        re.compile(
            r"\b(?:воскресень[ея]|воскресение|сень[ея]|сение|сения)\b",
            re.IGNORECASE,
        ),
    ),
)
_WEEKDAY_RE = re.compile(
    r"\b(?:"
    r"понедельник|дельник|"
    r"вторник|[аеёиэ]*торник|"
    r"среда|среду|реда|"
    r"четверг|етверг|"
    r"пятниц[ау]|тниц[ау]|"
    r"суббот[ау]|ббот[ау]|"
    r"воскресень[ея]|воскресение|сень[ея]|сение|сения"
    r")\b",
    re.IGNORECASE,
)


def day_part_time_window(day_part: Optional[str]) -> Optional[tuple[int, int]]:
    if not day_part:
        return None
    return DAY_PART_WINDOWS.get(day_part)


def slot_needs_next_day_pickup(slot_time: str) -> bool:
    """Слот с 17:30 и позже — автомобиль готов на следующий день."""
    try:
        hh, mm = map(int, (slot_time or "00:00").split(":")[:2])
    except (TypeError, ValueError):
        return False
    return (hh, mm) >= (NEXT_DAY_PICKUP_FROM_HOUR, NEXT_DAY_PICKUP_FROM_MINUTE)


def extract_day_part(text: str) -> Optional[str]:
    """Разделяет «в обед» (12–14) и «после обеда» (14–17)."""
    t = (text or "").lower()
    if any(
        phrase in t
        for phrase in (
            "утро",
            "утром",
            "с утра",
            "до обеда",
            "первая половина дня",
            "в первую половину дня",
            "в первую половине дня",
        )
    ):
        return "morning"
    if any(phrase in t for phrase in ("после обеда", "после обеду", "послеобед")):
        return "after_lunch"
    if any(phrase in t for phrase in ("обед", "в обед", "в середине дня")):
        return "lunch"
    if any(phrase in t for phrase in ("днём", "днем")):
        return "after_lunch"
    if any(
        phrase in t
        for phrase in (
            "вечером",
            "во второй половине дня",
            "вторая половина дня",
        )
    ):
        return "evening"
    return None


def resolve_weekday_booking_date(text: str, reference: datetime) -> Optional[date]:
    """
    Ближайший день недели из реплики.
    Если сегодня уже этот день недели — +7 дней (правило для пятницы и др.).
    """
    target_weekday = extract_weekday_index(text)
    if target_weekday is None:
        return None
    current_weekday = reference.weekday()
    days_ahead = target_weekday - current_weekday
    if days_ahead <= 0:
        days_ahead += 7
    return (reference + timedelta(days=days_ahead)).date()


def extract_weekday_index(text: str) -> Optional[int]:
    """Распознает день недели (в т.ч. короткие/искаженные STT-формы)."""
    t = ((text or "").lower()).replace("ё", "е")
    for weekday_idx, weekday_re in _WEEKDAY_PATTERNS:
        if weekday_re.search(t):
            return weekday_idx
    return None


def slot_start_in_window(start: datetime, duration_min: int, window: tuple[int, int]) -> bool:
    w_start, w_end = window
    end = start + timedelta(minutes=duration_min)
    if start.hour < w_start or start.hour >= w_end:
        return False
    # Слот должен полностью влезать в окно (или до 20:00 станции).
    window_end = start.replace(hour=w_end, minute=0, second=0, microsecond=0)
    station_close = start.replace(hour=20, minute=0, second=0, microsecond=0)
    effective_end = min(window_end, station_close)
    return end <= effective_end


def closest_slot_start_minutes(window: tuple[int, int]) -> int:
    """Минуты от полуночи — целевое начало окна для fallback."""
    return window[0] * 60
