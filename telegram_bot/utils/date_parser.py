"""
Парсер дат из естественного языка (русский).
"""

import re
from datetime import date, timedelta
from typing import Optional

# Названия месяцев в родительном падеже (февраля, марта...)
MONTHS_GENITIVE = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
MONTHS_NOMINATIVE = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}
# Дни недели (понедельник, во вторник...)
WEEKDAY_NAMES = {
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3, "пятница": 4,
    "суббота": 5, "воскресенье": 6,
    "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
}


def parse_date(text: str, base_date: Optional[date] = None) -> Optional[date]:
    """
    Парсит дату из текста на русском.
    
    Поддерживает:
    - завтра, послезавтра
    - 15 февраля, 15.02, 15.02.2025
    - понедельник, во вторник, в понедельник
    - сегодня
    
    :param text: Текст сообщения
    :param base_date: Базовая дата (по умолчанию — сегодня)
    :return: date или None
    """
    if not text or not base_date:
        base_date = date.today()
    
    t = text.strip().lower()
    if not t:
        return None

    # Сегодня
    if t in ("сегодня", "сейчас"):
        return base_date

    # Завтра, послезавтра
    if t == "завтра":
        return base_date + timedelta(days=1)
    if t in ("послезавтра", "после завтра"):
        return base_date + timedelta(days=2)
    
    # День недели: понедельник, во вторник, в понедельник
    for name, wd in WEEKDAY_NAMES.items():
        if name in t and len(name) >= 2:
            days_ahead = (wd - base_date.weekday()) % 7
            if days_ahead == 0 and name not in ("воскресенье", "вс"):
                days_ahead = 7  # Следующая неделя
            return base_date + timedelta(days=days_ahead)

    # 15 февраля / 15 февраля 2025
    for month_name, month_num in MONTHS_GENITIVE.items():
        if month_name in t:
            m = re.search(r"(\d{1,2})\s*" + re.escape(month_name) + r"(?:\s+(\d{4}))?", t)
            if m:
                day = int(m.group(1))
                year = int(m.group(2)) if m.group(2) else base_date.year
                try:
                    return date(year, month_num, day)
                except ValueError:
                    return None

    # 15.02 / 15.02.2025 / 15-02 / 15-02-2025
    m = re.search(r"(\d{1,2})[./\-](\d{1,2})(?:[./\-](\d{2,4}))?", t)
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else base_date.year
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None

    # Только число 15 (день в текущем месяце)
    m = re.search(r"^(\d{1,2})$", t)
    if m:
        day = int(m.group(1))
        try:
            d = date(base_date.year, base_date.month, day)
            if d >= base_date:
                return d
            # Прошлая дата — значит следующий месяц
            if base_date.month == 12:
                return date(base_date.year + 1, 1, day)
            return date(base_date.year, base_date.month + 1, day)
        except ValueError:
            return None

    return None
