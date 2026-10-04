"""
Сервис записи на ТО: слоты, трудоёмкость, интеграция с 1С (пока заглушки).
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Норма Vikingi: одно регламентное ТО — 2,5 часа (150 мин) в duration_min для 1С.
# Дефолт 1С без параметра duration_min — 60 мин; мы параметр всегда передаём.
# operation_unknown — клиент не назвал работы; не меньше DEFAULT_LABOR_MINUTES.
DEFAULT_LABOR_MINUTES = 150
LABOR_MINUTES_OPERATION_UNKNOWN = 150
# Локальная заглушка слотов (без 1С): та же длительность, без буфера.
SLOT_DURATION_MINUTES = DEFAULT_LABOR_MINUTES
# Окно поиска по умолчанию (дней)
DEFAULT_DAYS_RANGE = 3
# Большие диапазоны перегружают HTTP-сервис 1С и упираются в 10-секундный таймаут.
# Поэтому запрашиваем расписание короткими последовательными окнами.
SLOT_SEARCH_CHUNK_DAYS = 3
# Рабочие часы дилерского центра Викинги
WORK_START_HOUR = 8
WORK_END_HOUR = 20

# Диапазоны по пожеланиям клиента: (start_hour, end_hour)
TIME_PREFERENCE_MORNING = (9, 12)   # утром, с утра, до обеда
TIME_PREFERENCE_NOON = (11, 15)     # в середине дня, в обед
TIME_PREFERENCE_AFTERNOON = (14, 17)  # вторая половина дня, после обеда
TIME_PREFERENCE_EVENING = (16, 20)  # вечером


def parse_time_preference(text: str) -> tuple[int, int] | None:
    """
    Извлекает пожелание по времени из текста.
    :return: (start_hour, end_hour) или None
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    # Проверяем «после обеда» до «обед», чтобы не спутать
    if any(w in t for w in ["вторая половина дня", "после обеда", "послеобед", "пополудни"]):
        return TIME_PREFERENCE_AFTERNOON
    if any(w in t for w in ["утром", "с утра", "до обеда", "утрен"]):
        return TIME_PREFERENCE_MORNING
    if any(w in t for w in ["в середине дня", "в обед", "обед", "полдень"]):
        return TIME_PREFERENCE_NOON
    if any(w in t for w in ["вечером", "вечер", "к вечеру"]):
        return TIME_PREFERENCE_EVENING
    return None


def parse_explicit_time_hour(text: str) -> tuple[int, bool] | None:
    """
    Извлекает явное время из фраз: «после 14-00», «в 14:00», «в 14 хочу».
    :return: (час 0-23, is_after) или None. is_after=True для «после N», False для «в N».
    """
    import re
    t = (text or "").strip().lower()
    if not t:
        return None
    # «после 14-00», «после 14:00», «после 14»
    m = re.search(r"после\s+(\d{1,2})(?:\s*[-:]\s*(\d{2}))?", t)
    if m:
        h = int(m.group(1))
        return (min(h, 23), True)
    # «в 14-00», «в 14:00», «в 14 хочу»
    m = re.search(r"\bв\s+(\d{1,2})(?:\s*[-:]\s*(\d{2}))?", t)
    if m:
        h = int(m.group(1))
        return (min(h, 23), True)  # is_after=True — ищем слот не раньше этого часа
    return None


def parse_later_hours(text: str) -> int | None:
    """
    Извлекает «на N часов позже» из текста.
    :return: число часов или None
    """
    import re
    t = (text or "").strip().lower()
    # "часика на 3 позже", "на 2 часа позже", "на час позже"
    m = re.search(r"(?:часика\s+)?на\s+(\d+)\s*(?:час|часа|часов|часика)?", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*(?:час|часа|часов)\s+позже", t)
    if m:
        return int(m.group(1))
    if "на час" in t or "час позже" in t:
        return 1
    return None


@dataclass
class SlotInfo:
    """Информация о доступном слоте."""
    start: datetime
    end: datetime
    in_priority_range: bool  # В диапазоне +3 дня
    post_id: Optional[str] = None
    acceptor_id: Optional[str] = None
    mechanic_name: Optional[str] = None


def _parse_1c_slot_dict(
    s: dict,
    *,
    time_window: tuple[int, int] | None = None,
    after_datetime: Optional[datetime] = None,
) -> Optional[tuple[datetime, str, str, str]]:
    """Один слот из 1С → (start_dt, post_id, acceptor_id, mechanic_name) или None."""
    from dialog.service_slot_time import slot_time_hhmm_from_raw
    from dialog.business_calendar import is_business_day

    s_date = str(s.get("date") or "").strip()
    s_time = slot_time_hhmm_from_raw(s)
    if not s_date or not s_time:
        return None
    try:
        dt = datetime.strptime(f"{s_date} {s_time}", "%Y-%m-%d %H:%M")
    except Exception:
        return None
    # Время из 1С не округляем вниз (15:41 -> 15:40), чтобы не создавать наложение.
    dt = dt.replace(second=0, microsecond=0)
    if dt.hour < WORK_START_HOUR or dt.hour >= WORK_END_HOUR:
        return None
    if time_window:
        w_start, w_end = time_window
        if not (w_start <= dt.hour < w_end):
            return None
    if after_datetime and dt < after_datetime:
        return None
    if not is_business_day(dt.date()):
        return None
    post_id = str(s.get("post_id") or "").strip()
    acceptor_id = str(s.get("acceptor_id") or "").strip()
    mechanic_name = str(s.get("post_name") or s.get("acceptor_name") or "").strip()
    return dt, post_id, acceptor_id, mechanic_name


def _dedupe_parsed_slots(
    parsed: list[tuple[datetime, str, str, str]],
) -> list[tuple[datetime, str, str, str]]:
    deduped: list[tuple[datetime, str, str, str]] = []
    seen_keys: set[str] = set()
    for dt_item, post_item, acceptor_item, mechanic_name in sorted(parsed, key=lambda item: item[0]):
        key = dt_item.strftime("%Y-%m-%d %H:%M")
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append((dt_item, post_item, acceptor_item, mechanic_name))
    return deduped


def _prefer_half_hour_slot_rows(
    rows: list[tuple[datetime, str, str, str]],
) -> list[tuple[datetime, str, str, str]]:
    """
    Оставляет только слоты начала/середины часа (:00/:30).
    Продуктовое правило для записи на приёмку.
    """
    return [r for r in rows if isinstance(r[0], datetime) and r[0].minute in (0, 30)]


def _skip_slot_on_current_sunday(slot_start: datetime, now_local: datetime) -> bool:
    """
    Если сегодня воскресенье, не предлагаем слоты на текущий день.
    Будущие воскресенья и любые даты с понедельника и далее — допустимы.
    """
    return now_local.weekday() == 6 and slot_start.date() == now_local.date()


def get_labor_and_cost(
    work_description: str = "",
    *,
    brand: str | None = None,
    model: str | None = None,
    mileage_km: int | None = None,
) -> tuple[int, float]:
    """
    Трудоёмкость (мин) и стоимость по сводной таблице ТО (server2).

    Если переданы brand/model/mileage_km — читаем Excel через
    dialog.sto_to_summary_table. Иначе — fallback 150 мин / 0 ₽.
    """
    if brand or model:
        try:
            from dialog.sto_to_summary_table import get_labor_and_cost_from_table

            duration, price, result = get_labor_and_cost_from_table(
                brand=brand,
                model=model,
                mileage_km=mileage_km,
            )
            if result.found:
                return duration, price
            if brand or model:
                return LABOR_MINUTES_OPERATION_UNKNOWN, 0.0
        except Exception as exc:
            logger.warning("Сводная таблица ТО недоступна: %s", exc)
    return DEFAULT_LABOR_MINUTES, 0.0


def get_available_slots(
    target_date: date,
    slot_duration_min: int = SLOT_DURATION_MINUTES,
    from_datetime: Optional[datetime] = None,
    days_priority: int = DEFAULT_DAYS_RANGE,
    time_window: tuple[int, int] | None = None,
) -> list[SlotInfo]:
    """
    Заглушка: возвращает доступные слоты.
    time_window: (start_hour, end_hour) — только слоты в этом диапазоне.
    """
    now = from_datetime or datetime.now()
    cutoff = now + timedelta(days=days_priority)
    slots: list[SlotInfo] = []

    start_h, end_h = (time_window if time_window else (WORK_START_HOUR, WORK_END_HOUR))
    day_start = datetime.combine(target_date, datetime.min.time()).replace(hour=start_h, minute=0, second=0)
    day_end = datetime.combine(target_date, datetime.min.time()).replace(hour=end_h, minute=0, second=0)

    slot_start = day_start
    while slot_start + timedelta(minutes=slot_duration_min) <= day_end:
        slot_end = slot_start + timedelta(minutes=slot_duration_min)
        if slot_start >= now:
            in_priority = slot_start <= cutoff
            slots.append(SlotInfo(start=slot_start, end=slot_end, in_priority_range=in_priority))
        slot_start += timedelta(minutes=30)
    return slots


def find_nearest_slot(
    preferred_date: date,
    slot_duration_min: int = SLOT_DURATION_MINUTES,
    days_priority: int = DEFAULT_DAYS_RANGE,
    time_window: tuple[int, int] | None = None,
    after_datetime: Optional[datetime] = None,
) -> tuple[Optional[SlotInfo], bool]:
    """
    Ищет ближайший свободный слот (fallback без 1С).
    after_datetime: не предлагать слоты раньше этого момента (для «позже»).
    Праздничные нерабочие дни пропускаются.
    """
    from dialog.business_calendar import is_business_day

    now = after_datetime or datetime.now()
    cutoff = now + timedelta(days=days_priority)

    if is_business_day(preferred_date):
        slots = get_available_slots(
            preferred_date,
            slot_duration_min=slot_duration_min,
            from_datetime=now,
            days_priority=days_priority,
            time_window=time_window,
        )
        if slots:
            return slots[0], slots[0].in_priority_range

    current = preferred_date
    for _ in range(14):
        current += timedelta(days=1)
        if not is_business_day(current):
            continue
        slots = get_available_slots(
            current,
            slot_duration_min=slot_duration_min,
            from_datetime=now,
            days_priority=days_priority,
            time_window=time_window,
        )
        if slots:
            return slots[0], slots[0].in_priority_range

    return None, False


async def create_1c_booking(
    fio: str,
    phone: str,
    car_brand: str,
    car_model: str,
    car_year: str,
    car_mileage: str,
    work_wishes: str,
    slot_start: datetime,
    *,
    post_id: str = "",
    acceptor_id: str = "",
    duration_min: Optional[int] = None,
    transmission: Optional[str] = None,
    engine_volume: Optional[str] = None,
    drive: Optional[str] = None,
) -> Optional[str]:
    """
    Создание записи в 1С Alfa 6.0 через HTTP API (GET /service/appointments).
    Передаём в query 1С как базовые, так и расширенные поля
    (brand/model/mileage/listofworks).
    """
    from dialog.dealer_time import dealer_local_now_naive

    comparable_start = slot_start.replace(tzinfo=None) if slot_start.tzinfo else slot_start
    if comparable_start <= dealer_local_now_naive():
        logger.warning(
            "1С create_1c_booking отклонён: слот уже прошёл (%s)",
            slot_start.isoformat(),
        )
        return None
    try:
        from telegram_bot.services.ics_alfa_service import ICSAlfaService
        from telegram_bot_config import ICS_ALFA_SERVICE_POST_ID

        effective_post = (post_id or ICS_ALFA_SERVICE_POST_ID or "").strip()
        effective_acceptor = (acceptor_id or "").strip()
        effective_duration = max(
            int(duration_min or DEFAULT_LABOR_MINUTES),
            DEFAULT_LABOR_MINUTES,
        )
        if not effective_post:
            logger.warning(
                "1С create_1c_booking: post_id пуст — запись может быть отклонена 1С",
            )
        if not effective_acceptor:
            logger.warning(
                "1С create_1c_booking: acceptor_id пуст — запись может быть отклонена 1С",
            )
        from dialog.service_speech_parse import (
            normalize_car_model_for_1c,
            resolve_mileage_km_for_booking,
        )

        desired_date = slot_start.strftime("%Y-%m-%d")
        desired_time = slot_start.strftime("%H:%M")
        brand_value = (car_brand or "").strip()
        model_value = normalize_car_model_for_1c(brand_value, (car_model or "").strip())
        effective_mileage = resolve_mileage_km_for_booking(
            mileage=car_mileage,
            work_list=work_wishes,
            car_year=car_year,
        )
        year_value = str(car_year).strip() if car_year else ""
        tx_value = (transmission or "0").strip() if transmission is not None else "0"
        vol_value = (engine_volume or "0").strip() if engine_volume is not None else "0"
        drive_value = (drive or "0").strip() if drive is not None else "0"
        try:
            from dialog.voice_to_booking_trace import voice_to_booking_trace

            voice_to_booking_trace(
                "booking_1c_request",
                desired_date=desired_date,
                desired_time=desired_time,
                duration_min=effective_duration,
                post_id=effective_post or None,
                acceptor_id=effective_acceptor or None,
                fio=(fio or "").strip()[:120] or None,
                phone=(phone or "").strip()[:32] or None,
                car_brand=brand_value[:64] or None,
                car_model=model_value[:80] or None,
                car_year=year_value[:8] or None,
                transmission=tx_value,
                engine_volume=vol_value,
                drive=drive_value,
                car_mileage=effective_mileage,
                work_wishes=(work_wishes or "").strip()[:200] or None,
                note="extended_1c_query_brand_model_mileage_listofworks",
            )
        except Exception:
            pass
        logger.info(
            "1С create_appointment request: date=%s time=%s duration_min=%s post_id=%s acceptor_id=%s "
            "fio=%r phone=%r brand=%r model=%r car_year=%r transmission=%r engine_volume=%r drive=%r "
            "mileage=%r listofworks=%r",
            desired_date,
            desired_time,
            effective_duration,
            effective_post,
            effective_acceptor,
            fio,
            phone,
            brand_value or None,
            model_value or None,
            year_value or None,
            tx_value,
            vol_value,
            drive_value,
            effective_mileage,
            (work_wishes or "").strip() or None,
        )
        svc = ICSAlfaService()
        result = await svc.create_appointment(
            fio=fio,
            phone=phone,
            desired_date=desired_date,
            desired_time=desired_time,
            duration_min=effective_duration,
            post_id=effective_post,
            acceptor_id=effective_acceptor,
            car_brand=brand_value or None,
            model=model_value or None,
            mileage=effective_mileage,
            listofworks=(work_wishes or "").strip() or None,
            car_year=year_value or None,
            transmission=tx_value,
            engine_volume=vol_value,
            drive=drive_value,
        )
        await svc.close()
        if result and result.get("appointment_id"):
            logger.info("1С запись создана: %s", result["appointment_id"])
            return result["appointment_id"]
        import uuid

        local_id = f"local_{uuid.uuid4().hex[:12]}"
        logger.warning(
            "1С create_1c_booking: запись не создана в 1С, локальный id=%s "
            "(date=%s time=%s mileage=%r brand=%r model=%r)",
            local_id,
            desired_date,
            desired_time,
            effective_mileage,
            brand_value or None,
            model_value or None,
        )
        try:
            from dialog.voice_to_booking_trace import voice_to_booking_trace

            voice_to_booking_trace(
                "booking_1c_local_fallback",
                booking_id=local_id,
                desired_date=desired_date,
                desired_time=desired_time,
                car_mileage=effective_mileage,
                car_brand=brand_value or None,
                car_model=model_value or None,
            )
        except Exception:
            pass
        return local_id
    except Exception as exc:
        logger.warning("1С недоступна, запись не создана: %s", exc)
    return None


async def list_slots_on_day_1c(
    target_date: date,
    slot_duration_min: int = DEFAULT_LABOR_MINUTES,
    post_id: Optional[str] = None,
) -> list[tuple[datetime, str, str, str]]:
    """Все свободные слоты на один день (отсортированы по времени)."""
    try:
        from telegram_bot.services.ics_alfa_service import ICSAlfaService
        from telegram_bot_config import ICS_ALFA_SERVICE_POST_ID

        svc = ICSAlfaService()
        if not svc.initialized:
            await svc.close()
            return []

        from telegram_bot.services.ics_alfa_service import slots_date_to_exclusive

        date_str = target_date.strftime("%Y-%m-%d")
        effective_post = post_id or ICS_ALFA_SERVICE_POST_ID or None
        raw_slots = await svc.get_service_slots(
            date_from=date_str,
            date_to=slots_date_to_exclusive(target_date, 1),
            duration_min=max(int(slot_duration_min or DEFAULT_LABOR_MINUTES), DEFAULT_LABOR_MINUTES),
            post_id=effective_post,
        )
        await svc.close()

        slot_minutes = max(int(slot_duration_min or DEFAULT_LABOR_MINUTES), DEFAULT_LABOR_MINUTES)
        parsed: list[tuple[datetime, str, str, str]] = []
        for s in raw_slots or []:
            row = _parse_1c_slot_dict(s)
            if row:
                parsed.append(row)

        return _dedupe_parsed_slots(parsed)
    except Exception as exc:
        logger.warning("1С список слотов на день недоступен: %s", exc)
        return []


async def find_slots_around_date_1c(
    target_date: date,
    slot_duration_min: int = DEFAULT_LABOR_MINUTES,
    search_days: int = 45,
    post_id: Optional[str] = None,
    time_window: tuple[int, int] | None = None,
    min_time_hhmm: Optional[str] = None,
) -> tuple[Optional[SlotInfo], Optional[SlotInfo]]:
    """
    Ближайшие слоты строго до и строго после target_date (первый слот дня).
    """
    duration = max(int(slot_duration_min or DEFAULT_LABOR_MINUTES), DEFAULT_LABOR_MINUTES)
    before_info: Optional[SlotInfo] = None
    after_info: Optional[SlotInfo] = None
    from dialog.dealer_time import dealer_local_now_naive

    now = dealer_local_now_naive()
    today = now.date()
    min_hhmm: Optional[tuple[int, int]] = None
    if min_time_hhmm:
        try:
            hh, mm = map(int, str(min_time_hhmm).split(":")[:2])
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                min_hhmm = (hh, mm)
        except Exception:
            min_hhmm = None

    def _slot_matches_request_window(dt: datetime) -> bool:
        if time_window:
            w_start, w_end = time_window
            if not (w_start <= dt.hour < w_end):
                return False
        if min_hhmm:
            if (dt.hour, dt.minute) < min_hhmm:
                return False
        return True

    probe = target_date - timedelta(days=1)
    for _ in range(search_days):
        if probe < today:
            break
        slots = await list_slots_on_day_1c(probe, slot_duration_min=duration, post_id=post_id)
        future_slots = [
            slot
            for slot in slots
            if slot[0] > now and not _skip_slot_on_current_sunday(slot[0], now)
        ]
        future_slots = [slot for slot in future_slots if _slot_matches_request_window(slot[0])]
        future_slots = _prefer_half_hour_slot_rows(future_slots)
        if future_slots:
            from dialog.service_slot_time import round_slot_start_to_half_hour_if_available

            start_raw, best_post, best_acceptor, *extra = future_slots[0]
            start = round_slot_start_to_half_hour_if_available(
                start_raw,
                available_slots=future_slots,
                post_id=best_post or "",
                acceptor_id=best_acceptor or "",
            )
            best_mechanic = str(extra[0]).strip() if extra else ""
            before_info = SlotInfo(
                start=start,
                end=start + timedelta(minutes=duration),
                in_priority_range=False,
                post_id=best_post or None,
                acceptor_id=best_acceptor or None,
                mechanic_name=best_mechanic or None,
            )
            break
        probe -= timedelta(days=1)

    probe = max(target_date + timedelta(days=1), today)
    for _ in range(search_days):
        slots = await list_slots_on_day_1c(probe, slot_duration_min=duration, post_id=post_id)
        future_slots = [
            slot
            for slot in slots
            if slot[0] > now and not _skip_slot_on_current_sunday(slot[0], now)
        ]
        future_slots = [slot for slot in future_slots if _slot_matches_request_window(slot[0])]
        future_slots = _prefer_half_hour_slot_rows(future_slots)
        if future_slots:
            from dialog.service_slot_time import round_slot_start_to_half_hour_if_available

            start_raw, best_post, best_acceptor, *extra = future_slots[0]
            start = round_slot_start_to_half_hour_if_available(
                start_raw,
                available_slots=future_slots,
                post_id=best_post or "",
                acceptor_id=best_acceptor or "",
            )
            best_mechanic = str(extra[0]).strip() if extra else ""
            after_info = SlotInfo(
                start=start,
                end=start + timedelta(minutes=duration),
                in_priority_range=False,
                post_id=best_post or None,
                acceptor_id=best_acceptor or None,
                mechanic_name=best_mechanic or None,
            )
            break
        probe += timedelta(days=1)

    return before_info, after_info


async def find_nearest_slot_1c(
    preferred_date: date,
    slot_duration_min: int = DEFAULT_LABOR_MINUTES,
    days_priority: int = DEFAULT_DAYS_RANGE,
    time_window: tuple[int, int] | None = None,
    after_datetime: Optional[datetime] = None,
    post_id: Optional[str] = None,
    raise_on_error: bool = False,
) -> tuple[Optional[SlotInfo], bool]:
    """Ищет первый свободный слот в 1С короткими последовательными диапазонами."""
    try:
        from telegram_bot.services.ics_alfa_service import ICSAlfaService
        from telegram_bot_config import ICS_ALFA_SERVICE_POST_ID
        from dialog.dealer_time import dealer_local_now_naive

        svc = ICSAlfaService()
        if not svc.initialized:
            await svc.close()
            return None, False

        from telegram_bot.services.ics_alfa_service import slots_date_to_exclusive

        effective_post = post_id or ICS_ALFA_SERVICE_POST_ID or None
        slot_minutes = max(int(slot_duration_min or DEFAULT_LABOR_MINUTES), DEFAULT_LABOR_MINUTES)
        now_local = dealer_local_now_naive()
        remaining_days = max(int(days_priority or 1), 1)
        chunk_start = preferred_date
        while remaining_days > 0:
            chunk_days = min(SLOT_SEARCH_CHUNK_DAYS, remaining_days)
            date_from = chunk_start.strftime("%Y-%m-%d")
            date_to = slots_date_to_exclusive(chunk_start, chunk_days)
            raw_slots = await svc.get_service_slots(
                date_from=date_from,
                date_to=date_to,
                duration_min=slot_minutes,
                post_id=effective_post,
            )

            parsed: list[tuple[datetime, str, str, str]] = []
            for s in raw_slots or []:
                row = _parse_1c_slot_dict(
                    s,
                    time_window=time_window,
                    after_datetime=after_datetime,
                )
                if row:
                    parsed.append(row)

            deduped = _dedupe_parsed_slots(parsed)
            deduped = [
                row
                for row in deduped
                if row[0] > now_local and not _skip_slot_on_current_sunday(row[0], now_local)
            ]
            deduped = _prefer_half_hour_slot_rows(deduped)
            if deduped:
                from dialog.service_slot_time import round_slot_start_to_half_hour_if_available

                start_raw, best_post, best_acceptor, *extra = deduped[0]
                start = round_slot_start_to_half_hour_if_available(
                    start_raw,
                    available_slots=deduped,
                    post_id=best_post or "",
                    acceptor_id=best_acceptor or "",
                )
                best_mechanic = str(extra[0]).strip() if extra else ""
                end = start + timedelta(minutes=slot_minutes)
                return SlotInfo(
                    start=start,
                    end=end,
                    in_priority_range=True,
                    post_id=best_post or None,
                    acceptor_id=best_acceptor or None,
                    mechanic_name=best_mechanic or None,
                ), True

            chunk_start += timedelta(days=chunk_days)
            remaining_days -= chunk_days
        return None, False
    except Exception as exc:
        logger.warning("1С поиск слота недоступен: %s", exc)
        if raise_on_error:
            raise
        return None, False
    finally:
        if "svc" in locals():
            await svc.close()


async def cancel_1c_booking(booking_id: str) -> bool:
    """Отмена записи в 1С. При недоступности — True (запись в логе)."""
    try:
        from telegram_bot.services.ics_alfa_service import ICSAlfaService
        svc = ICSAlfaService()
        ok = await svc.cancel_appointment(booking_id)
        await svc.close()
        if ok:
            logger.info("1С запись отменена: %s", booking_id)
            return True
    except Exception as exc:
        logger.warning("1С отмена: ошибка — %s", exc)
    logger.info("Отмена записи (локально): %s", booking_id)
    return True
