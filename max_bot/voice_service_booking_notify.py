"""Уведомление MAX-сервисного чата о записи на ТО из голосового бота."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from max_bot.api_client import MaxApiClient
from max_bot.notify_max_channels import notify_max_department
from telegram_bot.services.group_router import GroupRouter
from telegram_bot.services.need_detector import ClientNeed
from telegram_bot.utils.phone_normalizer import normalize_phone
from dialog.service_speech_parse import mileage_label_for_storage_and_notify

logger = logging.getLogger(__name__)

_router = GroupRouter()

_DEFAULT_SKIP_NOTIFY_PHONES = "+79023730808"


def _skip_notify_phones_last10() -> set[str]:
    raw = (os.environ.get("VOICE_MAX_SKIP_NOTIFY_PHONES") or _DEFAULT_SKIP_NOTIFY_PHONES).strip()
    out: set[str] = set()
    for part in raw.split(","):
        norm = normalize_phone(part.strip())
        if not norm:
            continue
        digits = "".join(c for c in norm if c.isdigit())
        if len(digits) >= 10:
            out.add(digits[-10:])
    return out


def should_skip_voice_booking_max_notify(phone_named_by_client: Optional[str]) -> bool:
    """Не слать лид в MAX, если клиент назвал тестовый номер (не номер линии звонка)."""
    named = (phone_named_by_client or "").strip()
    if not named:
        return False
    norm = normalize_phone(named)
    if not norm:
        return False
    digits = "".join(c for c in norm if c.isdigit())
    if len(digits) < 10:
        return False
    return digits[-10:] in _skip_notify_phones_last10()


def _compose_car_label(brand: Optional[str], model: Optional[str]) -> str:
    parts = [str(brand or "").strip(), str(model or "").strip()]
    return " ".join(p for p in parts if p).strip()


def _compose_booking_need_text(
    *,
    car_norm_label: str,
    car_raw_label: str,
    mileage_norm: Optional[str],
    mileage_raw: Optional[str],
    slot_str: str,
    work_wishes: str,
) -> str:
    mileage_norm_label = (mileage_norm or "").strip()
    mileage_raw_label = (mileage_raw or "").strip()
    return (
        f"Запись на ТО: авто norm: {car_norm_label or 'не указано'}, "
        f"авто raw: {car_raw_label or 'не указано'}, "
        f"пробег norm: {mileage_norm_label or 'не указано'}, "
        f"пробег raw: {mileage_raw_label or 'не указано'}, "
        f"дата {slot_str}, работы: {work_wishes}"
    )


async def notify_voice_service_booking_to_max(
    *,
    fio: str,
    phone: str,
    phone_named_by_client: Optional[str] = None,
    car_brand: str,
    car_model: str,
    car_brand_raw: Optional[str] = None,
    car_model_raw: Optional[str] = None,
    car_brand_norm: Optional[str] = None,
    car_model_norm: Optional[str] = None,
    car_year: str,
    mileage_norm: Optional[str] = None,
    mileage_raw: Optional[str] = None,
    work_wishes: str,
    slot_start: datetime,
    booking_id: Optional[str] = None,
    mechanic_name: Optional[str] = None,
    to_label: Optional[str] = None,
    price_rub: Optional[float] = None,
    price_note: Optional[str] = None,
) -> bool:
    """Отправить лид записи на ТО в MAX_CHAT_SERVICE (как у MAX/TG-ботов)."""
    if should_skip_voice_booking_max_notify(phone_named_by_client):
        logger.info(
            "Запись на ТО: уведомление в MAX пропущено (клиент назвал тестовый номер %s)",
            phone_named_by_client,
        )
        return True

    token = (os.environ.get("MAX_BOT_TOKEN") or "").strip()
    if not token:
        logger.warning("MAX_BOT_TOKEN не задан — уведомление о записи на ТО в MAX пропущено")
        return False

    slot_str = slot_start.strftime("%d.%m в %H:%M")
    car_raw_label = _compose_car_label(car_brand_raw, car_model_raw) or _compose_car_label(
        car_brand,
        car_model,
    )
    car_norm_label = _compose_car_label(car_brand_norm, car_model_norm)
    mileage_clean = mileage_label_for_storage_and_notify(
        mileage_norm=mileage_norm,
        mileage_raw=mileage_raw,
    )
    need_text = _compose_booking_need_text(
        car_norm_label=car_norm_label,
        car_raw_label=car_raw_label,
        mileage_norm=mileage_clean or (mileage_norm or ""),
        mileage_raw=mileage_clean or "",
        slot_str=slot_str,
        work_wishes=work_wishes,
    )
    if booking_id:
        need_text += f" (1C: {booking_id})"
    mechanic_label = (mechanic_name or "").strip()
    if not mechanic_label:
        mechanic_label = "не указан"
    need_text += f", Слесарь: {mechanic_label}"
    note = (price_note or "").strip()
    if not note:
        if price_rub is not None:
            try:
                amount = int(round(float(price_rub)))
            except (TypeError, ValueError):
                amount = 0
            if amount > 0:
                label = (to_label or "").strip()
                if label and label.upper() != "ТО":
                    note = f"стоимость {label}: {amount} руб"
                else:
                    note = f"стоимость ТО: {amount} руб"
            else:
                note = "цена не называлась"
        else:
            note = "цена не называлась"
    need_text += f", {note}"

    lead_msg = "📞 Голосовой бот\n" + _router.format_lead_message(
        fio=fio,
        phone=phone,
        need_text=need_text,
        need=ClientNeed.SERVICE,
        timestamp=datetime.now().strftime("%d.%m.%Y %H:%M"),
        has_photo=False,
        car_brand=(car_brand_norm or "").strip() or None,
        car_model=(car_model_norm or "").strip() or None,
        car_year=car_year or None,
        car_mileage=mileage_clean or None,
        work_wishes=work_wishes or None,
    )

    base = (os.environ.get("MAX_API_BASE") or "https://platform-api.max.ru").rstrip("/")
    try:
        client = MaxApiClient(token=token, base_url=base)
    except ValueError:
        logger.warning("MAX_BOT_TOKEN пуст — уведомление о записи на ТО в MAX пропущено")
        return False

    try:
        import aiohttp

        async with aiohttp.ClientSession() as http:
            ok = await notify_max_department(client, http, ClientNeed.SERVICE, lead_msg)
            if ok:
                logger.info("Запись на ТО отправлена в MAX_CHAT_SERVICE")
            return ok
    except Exception:
        logger.exception("Не удалось отправить запись на ТО в MAX")
        return False
