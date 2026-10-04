"""
Рабочее расписание дилерского центра.
Загружается из working_hours.json.
"""

import json
import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Путь к файлу расписания (относительно корня проекта)
DEFAULT_SCHEDULE_PATH = Path(__file__).resolve().parent.parent.parent / "working_hours.json"

# Диапазон «в течение 15 минут» — ответ бота должен попадать в [work_start, work_end - 15 min]
RESPONSE_DEADLINE_MINUTES = 15


def _load_schedule(path: Path | None = None) -> dict:
    """Загружает JSON с расписанием."""
    path = path or DEFAULT_SCHEDULE_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Не удалось загрузить working_hours.json: {e}, используется значение по умолчанию")
        return {
            "default": {"start": "09:00", "end": "21:00"},
            "date_range": {"from": "2025-02-07", "to": "2026-12-21"},
            "overrides": {},
        }


def _parse_time(s: str) -> time:
    """Парсит 'HH:MM' в time."""
    parts = s.strip().split(":")
    return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


def _get_day_schedule(d: date, schedule: dict) -> tuple[time, time] | None:
    """
    Возвращает (start, end) для даты или None, если выходной (00:00-00:00).
    """
    key = d.strftime("%Y-%m-%d")
    overrides = schedule.get("overrides", {})
    default = schedule.get("default", {"start": "09:00", "end": "21:00"})

    if key in overrides:
        rec = overrides[key]
    else:
        rec = default

    start_s = rec.get("start", "09:00")
    end_s = rec.get("end", "21:00")

    start_t = _parse_time(start_s)
    end_t = _parse_time(end_s)

    if start_t == time(0, 0) and end_t == time(0, 0):
        return None
    return (start_t, end_t)


def _in_schedule_range(now: datetime, schedule: dict) -> bool:
    """
    True, если now попадает в диапазон [work_start, work_end - 15 min].
    """
    d = now.date()
    wh = _get_day_schedule(d, schedule)
    if not wh:
        return False
    work_start, work_end = wh
    deadline = (datetime.combine(d, work_end) - timedelta(minutes=RESPONSE_DEADLINE_MINUTES)).time()
    t = now.time()
    return work_start <= t <= deadline


def _next_working_day(start: date, schedule: dict, max_days: int = 14) -> tuple[date, time, time] | None:
    """Находит следующий рабочий день и его расписание."""
    for _ in range(max_days):
        wh = _get_day_schedule(start, schedule)
        if wh:
            return (start, wh[0], wh[1])
        start += timedelta(days=1)
    return None


def _format_time_for_message(t: time) -> str:
    """Форматирует время для сообщения: 9-00, 9-15."""
    return f"{t.hour}-{t.minute:02d}"


def _day_label_ru(d: date, base: date) -> str:
    """Текст для даты: сегодня, завтра, в понедельник и т.д."""
    delta = (d - base).days
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "завтра"
    # День недели
    weekdays = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    wd = weekdays[d.weekday()]
    return f"в {wd}"


# Фраза «Ваш запрос передан …» — дополнение с предлогом (винительный/куда); None → «передан специалисту»
DEPARTMENT_TRANSFER_OBJECT = {
    "ОП Chery/Tenet": "в отдел продаж Chery/Tenet",
    "Отдел продаж автомобилей с пробегом": "в отдел продаж автомобилей с пробегом",
    "Приемка слесарного цеха": "на приёмку слесарного цеха",
    "Приемка кузовного цеха": "на приёмку кузовного цеха",
    "Отдел запчастей": "в отдел запасных частей",
    "Прочие": None,
}

def dealer_local_now() -> datetime:
    """
    Текущие дата/время по **локальным часам процесса**: ``datetime.now()`` (учитывает ``TZ`` в Linux).

    В Docker для max-bot / telegram-bot в compose задано ``TZ`` (на server7 по умолчанию
    ``Europe/Samara``, как у хоста). Без ``TZ`` в контейнере обычно UTC — окна перезвона будут неверны.
    """
    return datetime.now()


def _to_dealer_local(current_time: datetime | None) -> datetime:
    if current_time is None:
        return dealer_local_now()
    if current_time.tzinfo is None:
        return current_time
    return current_time.astimezone()


# Окна обещаний по локальному времени сервера (см. dealer_local_now)
_TODAY_MORNING_WINDOW_END = time(8, 0)  # 0:00–7:59 → перезвонят сегодня 8:00–9:00
_QUICK_RESPONSE_START = time(8, 0)
_QUICK_RESPONSE_END = time(20, 45)  # 8:00–20:45 → в течение 15 минут


def is_dealer_quick_response_window(current_time: datetime | None = None) -> bool:
    """Совпадает с фразой «свяжутся в течение 15 минут» (8:00–20:45 по локальному времени сервера)."""
    t = _to_dealer_local(current_time).time()
    return _QUICK_RESPONSE_START <= t <= _QUICK_RESPONSE_END


def _contact_callback_phrase(now_local: datetime) -> str:
    t = now_local.time()
    if t < _TODAY_MORNING_WINDOW_END:
        return "С вами свяжутся сегодня с 8-00 до 9-00."
    if _QUICK_RESPONSE_START <= t <= _QUICK_RESPONSE_END:
        return "С вами свяжутся в течение 15 минут."
    return "С вами свяжутся завтра с 8-00 до 9-00."


def _lead_passed_prefix(department: str) -> str:
    if department not in DEPARTMENT_TRANSFER_OBJECT:
        return f"Ваш запрос передан в отдел ({department})."
    obj = DEPARTMENT_TRANSFER_OBJECT[department]
    if obj is None:
        return "Ваш запрос передан специалисту."
    return f"Ваш запрос передан {obj}."


def get_response_contact_phrase(department: str, current_time: datetime | None = None) -> str:
    """
    Фраза о передаче запроса и когда перезвонят. Время — **локальное время сервера**
    (``datetime.now()`` / наивный ``current_time`` как часы сервера; с tz — через ``.astimezone()``).

    - 0:00–7:59: сегодня с 8:00 до 9:00
    - 8:00–20:45: в течение 15 минут
    - после 20:45: завтра с 8:00 до 9:00
    """
    now_local = _to_dealer_local(current_time)
    return f"{_lead_passed_prefix(department)} {_contact_callback_phrase(now_local)}"


def is_working_hours(current_time: datetime | None = None) -> bool:
    """Попадает ли текущее время в рабочие часы (начало дня ... конец дня минус 15 минут)."""
    schedule = _load_schedule()
    now = current_time or datetime.now()
    return _in_schedule_range(now, schedule)


def is_working_day(d: date, schedule: dict | None = None) -> bool:
    """Является ли дата рабочим днём (не 00:00–00:00)."""
    sched = schedule or _load_schedule()
    return _get_day_schedule(d, sched) is not None


def get_working_hours_for_day(d: date, schedule: dict | None = None) -> tuple[str, str] | None:
    """
    Возвращает часы работы для даты в формате ('09:00', '21:00') или None для выходного.
    """
    sched = schedule or _load_schedule()
    wh = _get_day_schedule(d, sched)
    if not wh:
        return None
    start, end = wh
    return (start.strftime("%H:%M"), end.strftime("%H:%M"))


def get_working_hours_message(d: date) -> str:
    """Сообщение о часах работы в указанный день."""
    wh = get_working_hours_for_day(d)
    if not wh:
        return f"{d.strftime('%d.%m.%Y')} — выходной день."
    return f"{d.strftime('%d.%m.%Y')} дилерский центр работает с {wh[0]} до {wh[1]}."


def is_tomorrow_working(schedule: dict | None = None) -> bool:
    """Рабочий ли завтра день."""
    tomorrow = date.today() + timedelta(days=1)
    return is_working_day(tomorrow, schedule or _load_schedule())
