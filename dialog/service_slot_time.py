"""Сетка времени слотов для голосовой записи (шаг 30 минут)."""

from __future__ import annotations

from datetime import datetime, timedelta

SLOT_TIME_STEP_MINUTES = 30
SLOT_TIME_FIELDS = ("acceptance_time", "time_start", "time", "start_time")
SLOT_ROUND_HALF_HOUR_THRESHOLD_MINUTES = 10


def slot_time_hhmm_from_raw(raw: dict) -> str:
    """Время начала слота из ответа 1С (новый acceptance_time или legacy time_start)."""
    for field in SLOT_TIME_FIELDS:
        val = str(raw.get(field) or "").strip()
        if val and ":" in val:
            return val[:5]
    return ""


def snap_slot_start_to_step(dt: datetime, step_minutes: int = SLOT_TIME_STEP_MINUTES) -> datetime:
    """
    Привязка начала слота к сетке (вниз): 12:21 → 12:00, 15:40 → 15:30.
    """
    step = max(1, int(step_minutes))
    minute = (dt.minute // step) * step
    return dt.replace(minute=minute, second=0, microsecond=0)


def snap_hhmm_to_step(hhmm: str, step_minutes: int = SLOT_TIME_STEP_MINUTES) -> str:
    """HH:MM → ближайшее время на сетке (вниз)."""
    parts = (hhmm or "10:00").split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    snapped = snap_slot_start_to_step(
        datetime(2000, 1, 1, h, m),
        step_minutes=step_minutes,
    )
    return snapped.strftime("%H:%M")


def round_slot_start_to_half_hour_if_close(
    dt: datetime,
    threshold_minutes: int = SLOT_ROUND_HALF_HOUR_THRESHOLD_MINUTES,
) -> datetime:
    """
    Округляет время к ближайшим :00/:30, если разница не больше порога.

    Примеры при threshold=10:
    - 10:50 -> 11:00
    - 11:10 -> 11:00
    - 11:20 -> 11:30
    - 11:40 -> 11:30
    """
    threshold = max(0, int(threshold_minutes))
    base = dt.replace(second=0, microsecond=0)
    minutes_from_hour = base.minute
    floor_minute = 0 if minutes_from_hour < 30 else 30
    floor_candidate = base.replace(minute=floor_minute)
    ceil_candidate = floor_candidate + timedelta(minutes=30)

    floor_delta = abs(int((base - floor_candidate).total_seconds() // 60))
    ceil_delta = abs(int((ceil_candidate - base).total_seconds() // 60))

    rounded = floor_candidate if floor_delta <= ceil_delta else ceil_candidate
    # Не переносим предложение на следующий календарный день.
    if rounded.date() != base.date():
        rounded = floor_candidate
    chosen_delta = abs(int((rounded - base).total_seconds() // 60))
    if chosen_delta <= threshold:
        return rounded
    return base


def round_slot_start_to_half_hour_if_available(
    slot_start: datetime,
    *,
    available_slots: list[tuple[datetime, str, str, str]],
    post_id: str = "",
    acceptor_id: str = "",
    threshold_minutes: int = SLOT_ROUND_HALF_HOUR_THRESHOLD_MINUTES,
) -> datetime:
    """
    Безопасное округление слота к :00/:30.

    Округляем только если округлённое время присутствует в текущем наборе
    доступных слотов 1С и совпадает с тем же post_id/acceptor_id. Иначе
    оставляем исходное время, чтобы не создавать наложения в 1С.
    """
    base = slot_start.replace(second=0, microsecond=0)
    rounded = round_slot_start_to_half_hour_if_close(
        base,
        threshold_minutes=threshold_minutes,
    )
    if rounded == base:
        return base

    target_key = rounded.strftime("%Y-%m-%d %H:%M")
    expected_post = (post_id or "").strip()
    expected_acceptor = (acceptor_id or "").strip()
    for row in available_slots or []:
        if not isinstance(row, (tuple, list)) or len(row) < 3:
            continue
        dt_item = row[0]
        if not isinstance(dt_item, datetime):
            continue
        if dt_item.replace(second=0, microsecond=0).strftime("%Y-%m-%d %H:%M") != target_key:
            continue
        row_post = str(row[1] or "").strip()
        row_acceptor = str(row[2] or "").strip()
        if expected_post and row_post != expected_post:
            continue
        if expected_acceptor and row_acceptor != expected_acceptor:
            continue
        return rounded
    return base


def normalize_1c_slots(slots: list[dict]) -> list[dict]:
    """
    Слоты из 1С: привязка начала к сетке 30 мин (09:11→09:00) и дедупликация.
  Клиенту не озвучиваем «кривые» минуты из ответа 1С.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for raw in slots or []:
        if not isinstance(raw, dict):
            continue
        date_s = str(raw.get("date") or "").strip()
        time_raw = slot_time_hhmm_from_raw(raw)
        if not date_s or not time_raw:
            continue
        snapped = snap_hhmm_to_step(time_raw)
        post = str(raw.get("post_id") or "").strip()
        acceptor = str(raw.get("acceptor_id") or "").strip()
        key = f"{date_s}|{snapped}|{post}|{acceptor}"
        if key in seen:
            continue
        seen.add(key)
        item = dict(raw)
        for field in SLOT_TIME_FIELDS:
            if field in item and item[field]:
                item[field] = snapped
        if not slot_time_hhmm_from_raw(item):
            item["acceptance_time"] = snapped
        out.append(item)
    out.sort(
        key=lambda s: (
            str(s.get("date") or ""),
            slot_time_hhmm_from_raw(s),
        )
    )
    return out
