"""
Производственный календарь дилерского центра: нерабочие праздничные дни.

Используется когда клиент просит запись «как можно быстрее» / «ближайший день», чтобы
не предлагать заведомо нерабочую дату. 1С со своей стороны тоже учитывает календарь
и не отдаст слоты на праздники, но fallback‑логика поиска (`find_nearest_slot`)
работает без 1С — поэтому фильтр праздников нужен и здесь.

Состав списка:
  - автоматически нерабочий **только 9 мая** (все годы);
  - прочие даты — вручную по запросу заказчика:
    ``VIKINGI_EXTRA_HOLIDAYS`` (YYYY-MM-DD через запятую) — запись на сервис / слоты;
    ``working_hours.json`` → ``voice_bot_special_days`` — голосовой бот (см. bot_logic).

Федеральный календарь РФ (1 января, 12 июня, 4 ноября и т.д.) **не** подставляется
автоматически.

Выходные (сб/вс): дилерский центр Викинги работает в субботу и воскресенье,
поэтому выходные днём НЕ считаем нерабочими.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Iterable

logger = logging.getLogger(__name__)

# Единственная автоматическая нерабочая дата (повторяется каждый год).
_FIXED_HOLIDAYS_MMDD: tuple[tuple[int, int], ...] = (
    (5, 9),
)


def _parse_iso(s: str) -> date | None:
    try:
        return date.fromisoformat(s.strip())
    except Exception:
        return None


def _env_extra_holidays() -> set[date]:
    raw = os.environ.get("VIKINGI_EXTRA_HOLIDAYS", "").strip()
    if not raw:
        return set()
    out: set[date] = set()
    for chunk in raw.split(","):
        d = _parse_iso(chunk)
        if d:
            out.add(d)
    return out


def _holidays_for_year(year: int) -> set[date]:
    """Все нерабочие даты на год: 9 мая + VIKINGI_EXTRA_HOLIDAYS."""
    res: set[date] = set()
    for mm, dd in _FIXED_HOLIDAYS_MMDD:
        try:
            res.add(date(year, mm, dd))
        except ValueError:
            continue
    return res


def is_holiday(d: date) -> bool:
    """Является ли дата нерабочим праздничным днём (без учёта сб/вс)."""
    if d in _env_extra_holidays():
        return True
    return d in _holidays_for_year(d.year)


def is_business_day(d: date) -> bool:
    """
    Рабочий ли день дилерского центра Викинги.
    Сб/вс считаются рабочими; нерабочими — только праздники.
    """
    return not is_holiday(d)


def next_business_day(d: date, *, max_skip: int = 30) -> date:
    """
    Возвращает дату d, если она рабочая, иначе ближайшую следующую рабочую.
    `max_skip` — страховка от бесконечного цикла.
    """
    cur = d
    for _ in range(max_skip + 1):
        if is_business_day(cur):
            return cur
        cur = cur + timedelta(days=1)
    logger.warning(
        "next_business_day: превышен лимит %d дней от %s — возвращаем как есть",
        max_skip, d.isoformat()
    )
    return cur


def filter_business_days(dates: Iterable[date]) -> list[date]:
    return [d for d in dates if is_business_day(d)]
