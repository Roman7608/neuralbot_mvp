"""Подбор слотов на день с учётом окон времени и fallback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from dialog.service_slot_day_part import (
    closest_slot_start_minutes,
    day_part_time_window,
    slot_start_in_window,
)

try:
    from telegram_bot.services.service_booking_service import (
        DEFAULT_LABOR_MINUTES,
        WORK_END_HOUR,
        WORK_START_HOUR,
        list_slots_on_day_1c,
    )
except Exception:
    DEFAULT_LABOR_MINUTES = 150
    WORK_START_HOUR = 8
    WORK_END_HOUR = 20
    list_slots_on_day_1c = None  # type: ignore


@dataclass
class PickedSlot:
    date_str: str
    time_str: str
    post_id: str
    start_iso: str
    acceptor_id: str = ""
    mechanic_name: str = ""
    outside_requested_window: bool = False
    evening_fallback: bool = False


def _station_close_dt(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time()).replace(hour=WORK_END_HOUR)


def _slot_fits_work_day(start: datetime, duration_min: int) -> bool:
    end = start + timedelta(minutes=duration_min)
    open_at = start.replace(hour=WORK_START_HOUR, minute=0, second=0, microsecond=0)
    close_at = _station_close_dt(start.date())
    return start >= open_at and end <= close_at


def _pick_best_from_list(
    slots: list[tuple[datetime, str, str, str]],
    *,
    duration_min: int,
    time_window: Optional[tuple[int, int]] = None,
    after_dt: Optional[datetime] = None,
    exclude_times: Optional[set[str]] = None,
) -> Optional[tuple[datetime, str, str, str]]:
    exclude_times = exclude_times or set()
    candidates: list[tuple[datetime, str, str, str]] = []
    for dt_item, post, acceptor, mechanic_name in slots:
        key = dt_item.strftime("%H:%M")
        if key in exclude_times:
            continue
        if after_dt and dt_item < after_dt:
            continue
        if not _slot_fits_work_day(dt_item, duration_min):
            continue
        if time_window and not slot_start_in_window(dt_item, duration_min, time_window):
            continue
        candidates.append((dt_item, post, acceptor, mechanic_name))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0]


def _pick_closest_outside_window(
    slots: list[tuple[datetime, str, str, str]],
    *,
    duration_min: int,
    window: tuple[int, int],
    after_dt: Optional[datetime] = None,
    exclude_times: Optional[set[str]] = None,
) -> Optional[tuple[datetime, str, str, str]]:
    """Ближайший слот вне окна, но влезающий в рабочий день."""
    exclude_times = exclude_times or set()
    target = closest_slot_start_minutes(window)
    try:
        target_min = int(target)
    except (TypeError, ValueError):
        target_min = 0
    best: Optional[tuple[datetime, str, str, str, int]] = None
    for dt_item, post, acceptor, mechanic_name in slots:
        if not isinstance(dt_item, datetime):
            continue
        key = dt_item.strftime("%H:%M")
        if key in exclude_times:
            continue
        if after_dt and dt_item < after_dt:
            continue
        if not _slot_fits_work_day(dt_item, duration_min):
            continue
        if slot_start_in_window(dt_item, duration_min, window):
            continue
        dist = abs(dt_item.hour * 60 + dt_item.minute - target_min)
        if best is None or dist < best[4]:
            best = (dt_item, post, acceptor, mechanic_name, dist)
    if not best:
        return None
    return best[0], best[1], best[2], best[3]


def _to_picked(
    dt_item: datetime,
    post: str,
    acceptor: str = "",
    mechanic_name: str = "",
    **flags,
) -> PickedSlot:
    return PickedSlot(
        date_str=dt_item.strftime("%Y-%m-%d"),
        time_str=dt_item.strftime("%H:%M"),
        post_id=post or "",
        start_iso=dt_item.isoformat(),
        acceptor_id=acceptor or "",
        mechanic_name=mechanic_name or "",
        **flags,
    )


async def pick_slot_for_day(
    target_date: date,
    duration_min: int,
    *,
    day_part: Optional[str] = None,
    after_dt: Optional[datetime] = None,
    exclude_times: Optional[set[str]] = None,
    allow_evening_after_lunch: bool = False,
) -> Optional[PickedSlot]:
    """
    Подбор одного слота на день:
    1) в окне day_part;
    2) fallback — ближайший вне окна;
    3) для after_lunch — вечерний слот (17:00+), если allow_evening_after_lunch.
    """
    if not list_slots_on_day_1c:
        return None
    raw = await list_slots_on_day_1c(target_date, slot_duration_min=duration_min)
    slots: list[tuple[datetime, str, str, str]] = []
    if not isinstance(raw, list):
        return None
    for slot_row in raw:
        if not isinstance(slot_row, (tuple, list)) or len(slot_row) < 3:
            continue
        dt_item, post, acceptor = slot_row[0], slot_row[1], slot_row[2]
        mechanic_name = slot_row[3] if len(slot_row) >= 4 else ""
        if not isinstance(dt_item, datetime):
            continue
        # Продуктовое правило: на приёмку записываем только в :00/:30.
        if dt_item.minute not in (0, 30):
            continue
        slots.append((dt_item.replace(second=0, microsecond=0), str(post or ""), str(acceptor or ""), str(mechanic_name or "")))
    slots.sort(key=lambda x: x[0])

    time_window = day_part_time_window(day_part)
    chosen = _pick_best_from_list(
        slots,
        duration_min=duration_min,
        time_window=time_window,
        after_dt=after_dt,
        exclude_times=exclude_times,
    )
    if chosen:
        return _to_picked(chosen[0], chosen[1], chosen[2], chosen[3])

    if time_window:
        closest = _pick_closest_outside_window(
            slots,
            duration_min=duration_min,
            window=time_window,
            after_dt=after_dt,
            exclude_times=exclude_times,
        )
        if closest:
            evening = day_part == "after_lunch" and closest[0].hour >= 17
            return _to_picked(
                closest[0],
                closest[1],
                closest[2],
                closest[3],
                outside_requested_window=True,
                evening_fallback=evening,
            )

    if allow_evening_after_lunch and day_part == "after_lunch":
        evening_window = day_part_time_window("evening")
        chosen_ev = _pick_best_from_list(
            slots,
            duration_min=duration_min,
            time_window=evening_window,
            after_dt=after_dt,
            exclude_times=exclude_times,
        )
        if chosen_ev:
            return _to_picked(
                chosen_ev[0],
                chosen_ev[1],
                chosen_ev[2],
                chosen_ev[3],
                outside_requested_window=True,
                evening_fallback=True,
            )

    # Без окна — самый ранний после after_dt.
    chosen_any = _pick_best_from_list(
        slots,
        duration_min=duration_min,
        time_window=None,
        after_dt=after_dt,
        exclude_times=exclude_times,
    )
    if chosen_any:
        return _to_picked(chosen_any[0], chosen_any[1], chosen_any[2], chosen_any[3])
    return None
