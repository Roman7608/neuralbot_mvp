"""Альтернативные слоты до/после запрошенной даты (выбор времени записи)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from dialog.service_slot_pick import PickedSlot
from services.voice.voice_phrases import MONTHS_TTS_GENITIVE


def slot_info_to_sd_entry(info: Any) -> dict[str, str]:
    start: datetime = info.start
    return {
        "date": start.strftime("%Y-%m-%d"),
        "time": start.strftime("%H:%M"),
        "post": (info.post_id or "").strip(),
        "acceptor": (info.acceptor_id or "").strip(),
        "mechanic_name": (getattr(info, "mechanic_name", "") or "").strip(),
        "iso": start.isoformat(),
    }


def sd_entry_to_picked(entry: Optional[dict[str, str]]) -> Optional[PickedSlot]:
    if not entry:
        return None
    date_str = (entry.get("date") or "").strip()
    time_str = (entry.get("time") or "").strip()
    if not date_str or not time_str:
        return None
    return PickedSlot(
        date_str,
        time_str,
        (entry.get("post") or "").strip(),
        (entry.get("iso") or "").strip(),
        (entry.get("acceptor") or "").strip(),
        (entry.get("mechanic_name") or "").strip(),
    )


def _slot_part_tts(slot_date: date, time_hhmm: str) -> tuple[str, str, str]:
    from dialog.bot_logic import day_to_ordinal_ru, _slot_time_hhmm_to_tts

    day_txt = day_to_ordinal_ru(slot_date.day)
    month_txt = MONTHS_TTS_GENITIVE[slot_date.month]
    time_txt = _slot_time_hhmm_to_tts(time_hhmm)
    return day_txt, month_txt, time_txt


def format_slots_around_date_tts(
    requested_date: date,
    *,
    before_date: Optional[date] = None,
    before_time: Optional[str] = None,
    after_date: Optional[date] = None,
    after_time: Optional[str] = None,
) -> str:
    """Озвучивание: на запрошенную дату нет; ближайшие до и после с временем."""
    req_day, req_month, _ = _slot_part_tts(requested_date, "08:00")
    parts = [
        f"К сожалению, на {req_day} {req_month} нет свободных слотов.",
    ]
    if before_date and before_time:
        b_day, b_month, b_time = _slot_part_tts(before_date, before_time)
        parts.append(
            f"Ближайший свободный слот до этой даты — {b_day} {b_month} в {b_time}."
        )
    if after_date and after_time:
        a_day, a_month, a_time = _slot_part_tts(after_date, after_time)
        parts.append(
            f"Ближайший свободный слот после этой даты — {a_day} {a_month} в {a_time}."
        )
    if before_date and after_date:
        parts.append("Скажите «раньше» или «позже», или назовите дату.")
    elif before_date or after_date:
        parts.append("Назовите дату из предложенных или другую дату.")
    else:
        parts.append("Назовите другую дату, пожалуйста.")
    return " ".join(parts)


def is_slot_alt_before_stt(t_low: str) -> bool:
    return any(
        w in t_low
        for w in (
            "раньше",
            "пораньше",
            "до этого",
            "первый вариант",
        )
    )


def is_slot_alt_after_stt(t_low: str) -> bool:
    return any(
        w in t_low
        for w in (
            "позже",
            "попозже",
            "после",
            "второй вариант",
        )
    )


def pick_alt_from_parsed_date(
    parsed: date,
    before_entry: Optional[dict[str, str]],
    after_entry: Optional[dict[str, str]],
) -> Optional[str]:
    """'before' | 'after' если дата совпала с альтернативой."""
    key = parsed.strftime("%Y-%m-%d")
    if before_entry and before_entry.get("date") == key:
        return "before"
    if after_entry and after_entry.get("date") == key:
        return "after"
    return None
