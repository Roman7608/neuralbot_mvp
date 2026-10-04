"""
Логика заявок MAX по тем же веткам, что telegram_bot/handlers/lead_handler.py (WAITING_NEED и др.).
Телефон: ввод вручную и кнопка request_contact (inline), как «Передать номер» в Telegram.
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

import aiohttp

from database import TelegramLeadsDB
from max_bot.config import max_service_booking_via_alfa_enabled
from max_bot.keyboards import (
    POLICY_PAYLOAD,
    back_to_menu_attachments,
    main_menu_attachments,
    payload_to_menu_label,
    phone_after_typed_attachments,
    phone_prompt_attachments,
)
from max_bot.lead_finalize import finalize_lead_max
from max_bot.notify_max_channels import notify_max_department
from max_bot.lead_session import clear_session, get_session, session_data, set_state, update_data
from telegram_bot.handlers import lead_handler as lh
from telegram_bot.services.need_detector import ClientNeed
from telegram_bot.services.service_booking_service import (
    create_1c_booking,
    cancel_1c_booking,
    find_nearest_slot,
    find_nearest_slot_1c,
    get_labor_and_cost,
    parse_explicit_time_hour,
    parse_later_hours,
    parse_time_preference,
)
from telegram_bot.services.working_hours_schedule import get_working_hours_message, is_working_day
from telegram_bot.utils.date_parser import parse_date
from telegram_bot.utils.phone_normalizer import normalize_phone
from telegram_bot.services.working_hours_schedule import dealer_local_now, is_dealer_quick_response_window
from telegram_bot.utils.time_utils import format_datetime, get_response_message_template

if TYPE_CHECKING:
    from max_bot.api_client import MaxApiClient

logger = logging.getLogger(__name__)

# Состояния = имена как у BotStates
ST_WAITING_NEED = "WAITING_NEED"
ST_WAITING_NEED_CHOICE = "WAITING_NEED_CHOICE"
ST_WAITING_SERVICE_CHOICE = "WAITING_SERVICE_CHOICE"
ST_WAITING_SERVICE_FIO = "WAITING_SERVICE_FIO"
ST_WAITING_SERVICE_PHONE = "WAITING_SERVICE_PHONE"
ST_WAITING_SERVICE_CAR = "WAITING_SERVICE_CAR"
ST_WAITING_SERVICE_YEAR_MILEAGE = "WAITING_SERVICE_YEAR_MILEAGE"
ST_WAITING_SERVICE_WORK = "WAITING_SERVICE_WORK"
ST_WAITING_SERVICE_DATE = "WAITING_SERVICE_DATE"
ST_WAITING_SERVICE_SLOT_CONFIRM = "WAITING_SERVICE_SLOT_CONFIRM"
ST_WAITING_SERVICE_FINAL_CONFIRM = "WAITING_SERVICE_FINAL_CONFIRM"
ST_WAITING_FIO = "WAITING_FIO"
ST_WAITING_PHONE = "WAITING_PHONE"
ST_WAITING_CAR_BRAND_MODEL = "WAITING_CAR_BRAND_MODEL"
ST_WAITING_CAR_YEAR = "WAITING_CAR_YEAR"
ST_WAITING_CAR_MILEAGE = "WAITING_CAR_MILEAGE"
ST_WAITING_CAR_WISHES = "WAITING_CAR_WISHES"
ST_WAITING_NEW_USED = "WAITING_NEW_USED"
ST_WAITING_MORE_NEED = "WAITING_MORE_NEED"

SERVICE_BOOKING_STATES = frozenset(
    {
        ST_WAITING_SERVICE_CHOICE,
        ST_WAITING_SERVICE_FIO,
        ST_WAITING_SERVICE_PHONE,
        ST_WAITING_SERVICE_CAR,
        ST_WAITING_SERVICE_YEAR_MILEAGE,
        ST_WAITING_SERVICE_WORK,
        ST_WAITING_SERVICE_DATE,
        ST_WAITING_SERVICE_SLOT_CONFIRM,
        ST_WAITING_SERVICE_FINAL_CONFIRM,
    }
)


def payload_to_menu_text(payload: str) -> Optional[str]:
    """callback payload -> текст как у кнопки ReplyKeyboard в TG."""
    return payload_to_menu_label(payload)


def _clear_service_booking_progress(user_id: int) -> None:
    d = session_data(user_id)
    for k in (
        "service_fio",
        "service_phone",
        "service_car_brand",
        "service_car_model",
        "service_car_year_mileage",
        "service_work_wishes",
        "service_preferred_date",
        "service_proposed_slot_start",
        "service_proposed_slot_end",
        "service_1c_booking_id",
        "service_time_preference",
        "service_confirmed_slot_start",
    ):
        d.pop(k, None)


async def _service_department_without_booking(
    client: "MaxApiClient",
    http: aiohttp.ClientSession,
    chat_id: Optional[int],
    user_id: int,
    *,
    need_text: str,
) -> None:
    """Слесарный цех без онлайн-записи на ТО (до интеграции 1С Альфа)."""
    update_data(user_id, need=ClientNeed.SERVICE.value, need_text=need_text)
    data = session_data(user_id)
    if data.get("fio") and data.get("phone"):
        extra = await finalize_lead_max(
            client,
            http,
            chat_id=chat_id,
            user_id=user_id,
            data=data,
            phone=data["phone"],
            phone_alt=data.get("phone_alt"),
        )
        update_data(user_id, **extra)
        set_state(user_id, ST_WAITING_MORE_NEED)
    else:
        set_state(user_id, ST_WAITING_FIO)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
            back_to_menu_attachments(),
        )


async def _send(
    client: "MaxApiClient",
    http: aiohttp.ClientSession,
    chat_id: Optional[int],
    user_id: int,
    text: str,
    attachments: Optional[list] = None,
) -> None:
    await client.send_message(
        http,
        chat_id=chat_id,
        user_id=user_id,
        text=text,
        attachments=attachments,
    )


def _reset_to_need(user_id: int) -> None:
    set_state(user_id, ST_WAITING_NEED)
    d = session_data(user_id)
    for k in (
        "need",
        "need_text",
        "need_attempts",
        "service_fio",
        "service_phone",
        "service_car_brand",
        "service_car_model",
        "service_car_year_mileage",
        "service_work_wishes",
        "service_preferred_date",
        "service_proposed_slot_start",
        "service_1c_booking_id",
        "car_brand",
        "car_model",
        "car_year",
        "car_mileage",
        "work_wishes",
    ):
        d.pop(k, None)


async def _process_phone_contact(
    client: "MaxApiClient",
    http: aiohttp.ClientSession,
    chat_id: Optional[int],
    user_id: int,
    contact_phone: str,
) -> bool:
    """Как handle_phone_contact в Telegram: основной номер с кнопки, phone_alt — ранее введённый вручную."""
    raw = contact_phone.strip()
    phone_contact = normalize_phone(raw) or raw
    if phone_contact and not str(phone_contact).startswith("+"):
        phone_contact = f"+{str(phone_contact).lstrip('+')}"
    data = session_data(user_id)
    phone_typed = data.get("phone_typed")
    phone_alt = None
    if phone_typed:
        n1 = normalize_phone(phone_typed)
        n2 = normalize_phone(str(phone_contact))
        if n1 and n2 and n1 != n2:
            phone_alt = phone_typed
    extra = await finalize_lead_max(
        client,
        http,
        chat_id=chat_id,
        user_id=user_id,
        data=data,
        phone=str(phone_contact),
        phone_alt=phone_alt,
    )
    update_data(user_id, **extra)
    set_state(user_id, ST_WAITING_MORE_NEED)
    return True


async def _route_after_need_determined(
    client: "MaxApiClient",
    http: aiohttp.ClientSession,
    chat_id: Optional[int],
    user_id: int,
    *,
    need: ClientNeed,
    need_text: str,
    routing_text: str,
) -> bool:
    """Общая ветка после того, как потребность определена (кнопка, детектор или второй шаг)."""
    update_data(user_id, need=need.value, need_text=need_text, need_attempts=2)
    data = session_data(user_id)
    if need == ClientNeed.SERVICE:
        if not max_service_booking_via_alfa_enabled():
            await _service_department_without_booking(client, http, chat_id, user_id, need_text=need_text)
            return True
        has_fp = data.get("fio") and data.get("phone")
        if lh._is_service_book_button(routing_text.strip()):
            if has_fp:
                update_data(user_id, service_fio=data.get("fio"), service_phone=data.get("phone"))
                set_state(user_id, ST_WAITING_SERVICE_CAR)
                await _send(
                    client,
                    http,
                    chat_id,
                    user_id,
                    "Назовите марку и модель Вашего автомобиля.",
                    back_to_menu_attachments(),
                )
            else:
                set_state(user_id, ST_WAITING_SERVICE_FIO)
                await _send(
                    client,
                    http,
                    chat_id,
                    user_id,
                    "Назовите ваши фамилию, имя и отчество.",
                    main_menu_attachments(user_id),
                )
        else:
            set_state(user_id, ST_WAITING_SERVICE_CHOICE)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Я могу записать Вас на ТО либо передать Ваш контакт ассистенту Слесарного цеха.",
                main_menu_attachments(user_id),
            )
        return True

    if need == ClientNeed.BODY_REPAIR:
        update_data(user_id, car_brand=None, car_model=None, car_year=None, car_mileage=None, work_wishes=None)
        set_state(user_id, ST_WAITING_CAR_BRAND_MODEL)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Укажите, пожалуйста, марку и модель автомобиля (например: Toyota Camry).",
            main_menu_attachments(user_id),
        )
        return True

    data = session_data(user_id)
    if data.get("fio") and data.get("phone"):
        extra = await finalize_lead_max(
            client,
            http,
            chat_id=chat_id,
            user_id=user_id,
            data=data,
            phone=data["phone"],
            phone_alt=data.get("phone_alt"),
        )
        update_data(user_id, **extra)
        set_state(user_id, ST_WAITING_MORE_NEED)
    else:
        set_state(user_id, ST_WAITING_FIO)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
            back_to_menu_attachments(),
        )
    return True


async def handle_max_lead_message(
    client: "MaxApiClient",
    http: aiohttp.ClientSession,
    *,
    chat_id: Optional[int],
    user_id: int,
    text_in: Optional[str],
    callback_payload: Optional[str],
    contact_phone: Optional[str] = None,
) -> bool:
    """
    Обрабатывает ввод пользователя MAX в рамках сценария лидов.
    Возвращает True, если сообщение обработано (dispatcher не шлёт эхо).
    """
    if user_id is None:
        return False

    text = (text_in or "").strip()
    if callback_payload:
        # Политика: сначала проверка payload, иначе mapped = текст кнопки и уходит в need_detector как фраза пользователя.
        if callback_payload == POLICY_PAYLOAD:
            return False
        if callback_payload == "start":
            clear_session(user_id)
            return False
        mapped = payload_to_menu_text(callback_payload)
        if mapped:
            text = mapped
        else:
            return False

    # После первого действия (текст, контакт, любая кнопка кроме обработанных выше) — убрать политику из главного меню
    if contact_phone or text.strip() or callback_payload:
        update_data(user_id, policy_menu_dismissed=True)

    sess = get_session(user_id)
    state = sess["state"]
    data = sess["data"]

    if (
        not max_service_booking_via_alfa_enabled()
        and state in SERVICE_BOOKING_STATES
        and contact_phone
    ):
        _clear_service_booking_progress(user_id)
        raw = contact_phone.strip()
        pn = normalize_phone(raw) or raw
        if pn and not str(pn).startswith("+"):
            pn = f"+{str(pn).lstrip('+')}"
        if pn:
            update_data(user_id, phone=pn, phone_typed=pn)
        need_text = str(data.get("need_text") or text or "слесарный цех")
        await _service_department_without_booking(client, http, chat_id, user_id, need_text=need_text)
        return True

    if contact_phone:
        if state == ST_WAITING_PHONE:
            return await _process_phone_contact(client, http, chat_id, user_id, contact_phone)
        if state == ST_WAITING_SERVICE_PHONE:
            pn = normalize_phone(contact_phone) or contact_phone
            update_data(user_id, service_phone=pn)
            set_state(user_id, ST_WAITING_SERVICE_CAR)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Назовите марку и модель Вашего автомобиля.",
                back_to_menu_attachments(),
            )
            return True
        if not text:
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Сейчас номер телефона не запрашивается. Выберите раздел в меню ниже.",
                back_to_menu_attachments(),
            )
            return True

    if not max_service_booking_via_alfa_enabled() and state in SERVICE_BOOKING_STATES:
        need_text = str(data.get("need_text") or text or "слесарный цех")
        _clear_service_booking_progress(user_id)
        await _service_department_without_booking(client, http, chat_id, user_id, need_text=need_text)
        return True

    if not text:
        return False

    # Глобально: команда старт/меню сбрасывает сессию
    tl = text.lower()
    if tl in ("/start", "/старт", "старт", "start", "начать", "меню") or (
        tl.startswith("/start") or tl.startswith("/старт")
    ):
        clear_session(user_id)
        return False

    if tl in ("закрыть сессию", "закрыть диалог"):
        clear_session(user_id)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Сессия закрыта. Данные сценария сброшены. "
            "Чтобы начать снова — выберите раздел в меню или напишите /start.",
            main_menu_attachments(user_id),
        )
        return True

    try:
        if state == ST_WAITING_NEED:
            return await _handle_waiting_need(client, http, chat_id, user_id, text)
        if state == ST_WAITING_NEED_CHOICE:
            return await _handle_need_choice(client, http, chat_id, user_id, text)
        if state == ST_WAITING_SERVICE_CHOICE:
            return await _handle_service_choice(client, http, chat_id, user_id, text)
        if state == ST_WAITING_SERVICE_FIO:
            return await _handle_service_fio(client, http, chat_id, user_id, text)
        if state == ST_WAITING_SERVICE_PHONE:
            return await _handle_service_phone(client, http, chat_id, user_id, text)
        if state == ST_WAITING_SERVICE_CAR:
            return await _handle_service_car(client, http, chat_id, user_id, text)
        if state == ST_WAITING_SERVICE_YEAR_MILEAGE:
            return await _handle_service_year_mileage(client, http, chat_id, user_id, text)
        if state == ST_WAITING_SERVICE_WORK:
            return await _handle_service_work(client, http, chat_id, user_id, text)
        if state == ST_WAITING_SERVICE_DATE:
            return await _handle_service_date(client, http, chat_id, user_id, text)
        if state == ST_WAITING_SERVICE_SLOT_CONFIRM:
            return await _handle_service_slot_confirm(client, http, chat_id, user_id, text)
        if state == ST_WAITING_SERVICE_FINAL_CONFIRM:
            return await _handle_service_final_confirm(client, http, chat_id, user_id, text)
        if state == ST_WAITING_CAR_BRAND_MODEL:
            return await _handle_car_brand_model(client, http, chat_id, user_id, text)
        if state == ST_WAITING_CAR_YEAR:
            return await _handle_car_year(client, http, chat_id, user_id, text)
        if state == ST_WAITING_CAR_MILEAGE:
            return await _handle_car_mileage(client, http, chat_id, user_id, text)
        if state == ST_WAITING_CAR_WISHES:
            return await _handle_car_wishes(client, http, chat_id, user_id, text)
        if state == ST_WAITING_FIO:
            return await _handle_fio(client, http, chat_id, user_id, text)
        if state == ST_WAITING_PHONE:
            return await _handle_phone(client, http, chat_id, user_id, text)
        if state == ST_WAITING_NEW_USED:
            return await _handle_new_used(client, http, chat_id, user_id, text)
        if state == ST_WAITING_MORE_NEED:
            return await _handle_more_need(client, http, chat_id, user_id, text)
    except Exception:
        logger.exception("MAX lead_flow user_id=%s state=%s", user_id, state)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Произошла ошибка. Напишите /start и попробуйте снова.",
            back_to_menu_attachments(),
        )
        clear_session(user_id)
        return True

    return False


async def _handle_waiting_need(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    need_text = text
    text_lower = need_text.lower()
    brand = session_data(user_id).get("brand") or "chery_tenet"

    if any(kw in text_lower for kw in lh.WORKING_HOURS_KEYWORDS):
        base = date_type.today()
        if "завтра" in text_lower:
            d = base + timedelta(days=1)
        elif "послезавтра" in text_lower or "после завтра" in text_lower:
            d = base + timedelta(days=2)
        elif "сегодня" in text_lower:
            d = base
        else:
            parsed = parse_date(need_text, base)
            d = parsed if parsed else base
        msg = get_working_hours_message(d)
        await _send(client, http, chat_id, user_id, msg, main_menu_attachments(user_id))
        return True

    if any(kw in text_lower for kw in lh.DEALER_INFO_KEYWORDS):
        await _send(client, http, chat_id, user_id, lh.DEALER_INFO_REPLY, main_menu_attachments(user_id))
        return True

    if text_lower in ("оп tenet & chery", "оп jetour"):
        update_data(
            user_id, brand="chery_tenet", need=ClientNeed.CHERY_TENET.value, need_text=need_text
        )
        data = session_data(user_id)
        if data.get("fio") and data.get("phone"):
            extra = await finalize_lead_max(
                client, http, chat_id=chat_id, user_id=user_id, data=data, phone=data["phone"], phone_alt=data.get("phone_alt")
            )
            update_data(user_id, **extra)
            set_state(user_id, ST_WAITING_MORE_NEED)
        else:
            set_state(user_id, ST_WAITING_FIO)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                back_to_menu_attachments(),
            )
        return True

    if lh._is_service_book_button(text_lower):
        if not max_service_booking_via_alfa_enabled():
            await _service_department_without_booking(client, http, chat_id, user_id, need_text=need_text)
            return True
        update_data(user_id, need=ClientNeed.SERVICE.value, need_text=need_text)
        data = session_data(user_id)
        brand = data.get("brand") or "chery_tenet"
        if data.get("fio") and data.get("phone"):
            update_data(user_id, service_fio=data.get("fio"), service_phone=data.get("phone"))
            set_state(user_id, ST_WAITING_SERVICE_CAR)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Назовите марку и модель Вашего автомобиля.",
                back_to_menu_attachments(),
            )
        else:
            set_state(user_id, ST_WAITING_SERVICE_FIO)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Назовите ваши фамилию, имя и отчество.",
                main_menu_attachments(user_id),
            )
        return True

    if lh._is_service_to_text(text_lower):
        await _send(client, http, chat_id, user_id, lh.SERVICE_TO_REDIRECT_MESSAGE, main_menu_attachments(user_id))
        return True

    if text_lower == "слесарный цех":
        if not max_service_booking_via_alfa_enabled():
            await _service_department_without_booking(client, http, chat_id, user_id, need_text=need_text)
            return True
        update_data(user_id, need=ClientNeed.SERVICE.value, need_text=need_text)
        set_state(user_id, ST_WAITING_SERVICE_CHOICE)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Я могу записать Вас на ТО либо передать Ваш контакт ассистенту Слесарного цеха.",
            main_menu_attachments(user_id),
        )
        return True

    if text_lower == "кузовной цех":
        update_data(
            user_id,
            need=ClientNeed.BODY_REPAIR.value,
            need_text=need_text,
            car_brand=None,
            car_model=None,
            car_year=None,
            car_mileage=None,
            work_wishes=None,
        )
        set_state(user_id, ST_WAITING_CAR_BRAND_MODEL)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Укажите, пожалуйста, марку и модель автомобиля (например: Toyota Camry).",
            main_menu_attachments(user_id),
        )
        return True

    if text_lower == "автомобили с пробегом":
        update_data(user_id, need=ClientNeed.USED_CARS.value, need_text=need_text)
        data = session_data(user_id)
        if data.get("fio") and data.get("phone"):
            extra = await finalize_lead_max(
                client, http, chat_id=chat_id, user_id=user_id, data=data, phone=data["phone"], phone_alt=data.get("phone_alt")
            )
            update_data(user_id, **extra)
            set_state(user_id, ST_WAITING_MORE_NEED)
        else:
            set_state(user_id, ST_WAITING_FIO)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                back_to_menu_attachments(),
            )
        return True

    if text_lower == "запчасти":
        update_data(user_id, need=ClientNeed.SPARES.value, need_text=need_text)
        data = session_data(user_id)
        if data.get("fio") and data.get("phone"):
            extra = await finalize_lead_max(
                client, http, chat_id=chat_id, user_id=user_id, data=data, phone=data["phone"], phone_alt=data.get("phone_alt")
            )
            update_data(user_id, **extra)
            set_state(user_id, ST_WAITING_MORE_NEED)
        else:
            set_state(user_id, ST_WAITING_FIO)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                back_to_menu_attachments(),
            )
        return True

    if text_lower == "прочие":
        update_data(user_id, need=ClientNeed.SECRETARY.value, need_text=need_text)
        data = session_data(user_id)
        if data.get("fio") and data.get("phone"):
            extra = await finalize_lead_max(
                client, http, chat_id=chat_id, user_id=user_id, data=data, phone=data["phone"], phone_alt=data.get("phone_alt")
            )
            update_data(user_id, **extra)
            set_state(user_id, ST_WAITING_MORE_NEED)
        else:
            set_state(user_id, ST_WAITING_FIO)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                back_to_menu_attachments(),
            )
        return True

    if any(kw in text_lower for kw in lh.DEPARTMENT_INQUIRY_KEYWORDS):
        await _send(
            client,
            http,
            chat_id,
            user_id,
            lh.need_detector.get_choice_message(brand=brand),  # type: ignore[union-attr]
            main_menu_attachments(user_id),
        )
        return True

    nd_need = lh.need_detector.detect(need_text)
    if nd_need is not None:
        need = nd_need
        if need == ClientNeed.CHERY_TENET:
            if any(b in text_lower for b in lh.FOREIGN_BRAND_KEYWORDS):
                need = ClientNeed.USED_CARS
                await _send(
                    client,
                    http,
                    chat_id,
                    user_id,
                    "Я могу перевести Вас на отдел автомобили с пробегом. "
                    "Там Вам постараются помочь с Вашим запросом.",
                    main_menu_attachments(user_id),
                )
        return await _route_after_need_determined(
            client, http, chat_id, user_id, need=need, need_text=need_text, routing_text=text
        )

    update_data(user_id, need_text=need_text, need_attempts=1)
    set_state(user_id, ST_WAITING_NEED_CHOICE)
    await _send(
        client,
        http,
        chat_id,
        user_id,
        lh.need_detector.get_choice_message(brand=brand),  # type: ignore[union-attr]
        main_menu_attachments(user_id),
    )
    return True


async def _handle_need_choice(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    choice = text.strip().lower()
    brand = session_data(user_id).get("brand") or "chery_tenet"

    if lh._is_service_to_text(choice):
        await _send(client, http, chat_id, user_id, lh.SERVICE_TO_REDIRECT_MESSAGE, main_menu_attachments(user_id))
        return True

    has_foreign = any(b in choice for b in lh.FOREIGN_BRAND_KEYWORDS)
    has_used_markers = any(m in choice for m in ["пробег", "б/у", "бэу", "бу"])
    if has_foreign and not has_used_markers:
        need = ClientNeed.USED_CARS
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Я могу перевести Вас на отдел автомобили с пробегом. "
            "Там Вам постараются помочь с Вашим запросом.",
            main_menu_attachments(user_id),
        )
    else:
        if (
            "chery" in choice
            or "тенет" in choice
            or "чери" in choice
            or "jetour" in choice
            or "джетур" in choice
            or "джитур" in choice
            or ("нов" in choice and ("авто" in choice or "машин" in choice or "продаж" in choice))
        ):
            need = ClientNeed.CHERY_TENET
            update_data(user_id, brand="chery_tenet")
        elif any(
            m in choice
            for m in [
                "пробег",
                "б/у",
                "бэу",
                "бу",
                "подержан",
                "с пробегом",
                "отдел с пробегом",
                "подержаные",
                "подержанные",
                "не новые",
                "неновые",
            ]
        ):
            need = ClientNeed.USED_CARS
        elif (
            any(m in choice for m in ["слесар", "сто", "сервис", "приемка слесар", "техобслуж"])
            or lh._is_service_book_button(choice)
        ):
            need = ClientNeed.SERVICE
        elif any(
            m in choice
            for m in ["кузов", "приемка кузов", "кузовной", "покраск", "покрасить", "бампер", "крыло", "капот", "вмятин", "царапин", "дтп"]
        ):
            need = ClientNeed.BODY_REPAIR
        elif "запчаст" in choice or "детал" in choice or "запасных частей" in choice or "запасных" in choice:
            need = ClientNeed.SPARES
        elif "прочи" in choice or "секретар" in choice or "друго" in choice:
            need = ClientNeed.SECRETARY
        else:
            need = ClientNeed.SECRETARY

    data = session_data(user_id)
    need_text = data.get("need_text", text)
    return await _route_after_need_determined(
        client, http, chat_id, user_id, need=need, need_text=need_text, routing_text=text
    )


async def _handle_service_choice(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    text_raw = text.strip()
    t = text_raw.lower()
    data = session_data(user_id)
    brand = data.get("brand") or "chery_tenet"

    if any(kw in t for kw in lh.BODY_REPAIR_REDIRECT_KEYWORDS):
        update_data(user_id, need=ClientNeed.BODY_REPAIR.value, need_text=data.get("need_text") or text_raw)
        update_data(user_id, car_brand=None, car_model=None, car_year=None, car_mileage=None, work_wishes=None)
        set_state(user_id, ST_WAITING_CAR_BRAND_MODEL)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Укажите, пожалуйста, марку и модель автомобиля (например: Toyota Camry).",
            main_menu_attachments(user_id),
        )
        return True

    wants_book = any(kw in t for kw in lh.SERVICE_BOOK_KEYWORDS)
    wants_pass = any(kw in t for kw in lh.SERVICE_PASS_CONTACT_KEYWORDS)
    has_fp = data.get("fio") and data.get("phone")

    if lh._is_service_book_button(text_raw):
        if has_fp:
            update_data(user_id, service_fio=data.get("fio"), service_phone=data.get("phone"))
            set_state(user_id, ST_WAITING_SERVICE_CAR)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Назовите марку и модель Вашего автомобиля.",
                back_to_menu_attachments(),
            )
        else:
            set_state(user_id, ST_WAITING_SERVICE_FIO)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Назовите ваши фамилию, имя и отчество.",
                back_to_menu_attachments(),
            )
        return True
    if wants_book or lh._is_service_to_text(text_raw):
        await _send(client, http, chat_id, user_id, lh.SERVICE_TO_REDIRECT_MESSAGE, main_menu_attachments(user_id))
        return True
    if wants_pass:
        if has_fp:
            extra = await finalize_lead_max(
                client, http, chat_id=chat_id, user_id=user_id, data=data, phone=data["phone"], phone_alt=data.get("phone_alt")
            )
            update_data(user_id, **extra)
            set_state(user_id, ST_WAITING_MORE_NEED)
        else:
            set_state(user_id, ST_WAITING_FIO)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                main_menu_attachments(user_id),
            )
        return True

    await _send(
        client,
        http,
        chat_id,
        user_id,
        "Я могу записать Вас на ТО либо передать Ваш контакт ассистенту Слесарного цеха. "
        "Напишите, что Вам удобнее: записаться на ТО (или нажмите в меню «Записаться на ТО») "
        "или передать контакт диспетчеру.",
        main_menu_attachments(user_id),
    )
    return True


async def _handle_service_fio(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    data = session_data(user_id)
    if data.get("fio") and data.get("phone"):
        update_data(user_id, service_fio=data.get("fio"), service_phone=data.get("phone"))
        set_state(user_id, ST_WAITING_SERVICE_CAR)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Назовите марку и модель Вашего автомобиля.",
            back_to_menu_attachments(),
        )
        return True
    t = text.strip()
    if not t:
        await _send(client, http, chat_id, user_id, "Пожалуйста, назовите ваши фамилию, имя и отчество.", main_menu_attachments(user_id))
        return True
    update_data(user_id, service_fio=t)
    if data.get("phone"):
        update_data(user_id, service_phone=data.get("phone"))
        set_state(user_id, ST_WAITING_SERVICE_CAR)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Назовите марку и модель Вашего автомобиля.",
            back_to_menu_attachments(),
        )
    else:
        set_state(user_id, ST_WAITING_SERVICE_PHONE)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            'Назовите номер телефона для связи в формате +7 XXX XXX XX XX или нажмите «Передать номер телефона».',
            phone_prompt_attachments(),
        )
    return True


async def _handle_service_phone(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    raw = text.strip()
    phone = normalize_phone(raw)
    if not phone:
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Пожалуйста, укажите корректный номер (+7…) или нажмите «Передать номер телефона».",
            phone_prompt_attachments(),
        )
        return True
    update_data(user_id, service_phone=phone)
    set_state(user_id, ST_WAITING_SERVICE_CAR)
    await _send(
        client,
        http,
        chat_id,
        user_id,
        "Назовите марку и модель Вашего автомобиля.",
        back_to_menu_attachments(),
    )
    return True


async def _handle_service_car(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    t = text.strip()
    if not t:
        await _send(client, http, chat_id, user_id, "Пожалуйста, назовите марку и модель автомобиля.", back_to_menu_attachments())
        return True
    parts = t.split(maxsplit=1)
    update_data(user_id, service_car_brand=parts[0], service_car_model=parts[1] if len(parts) > 1 else "")
    set_state(user_id, ST_WAITING_SERVICE_YEAR_MILEAGE)
    await _send(client, http, chat_id, user_id, "Назовите год выпуска и пробег автомобиля.", back_to_menu_attachments())
    return True


async def _handle_service_year_mileage(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    t = text.strip()
    if not t:
        await _send(client, http, chat_id, user_id, "Пожалуйста, назовите год выпуска и пробег.", back_to_menu_attachments())
        return True
    update_data(user_id, service_car_year_mileage=t)
    set_state(user_id, ST_WAITING_SERVICE_WORK)
    await _send(
        client,
        http,
        chat_id,
        user_id,
        "Я могу записать Вас на ТО или простые механические операции. "
        "Какие работы Вам нужно произвести на автомобиле?",
        back_to_menu_attachments(),
    )
    return True


async def _handle_service_work(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    t = text.strip()
    if not t:
        await _send(client, http, chat_id, user_id, "Пожалуйста, опишите необходимые работы.", back_to_menu_attachments())
        return True
    if lh._is_service_cost_question(t):
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Ориентировочная трудоёмкость: 2 часа. Стоимость уточняется при записи.",
            back_to_menu_attachments(),
        )
        return True
    update_data(user_id, service_work_wishes=t)
    set_state(user_id, ST_WAITING_SERVICE_DATE)
    await _send(
        client,
        http,
        chat_id,
        user_id,
        "В какой день Вы хотели бы сдать автомобиль на обслуживание?",
        back_to_menu_attachments(),
    )
    return True


async def _handle_service_date(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    t = text.strip()
    if not t:
        await _send(client, http, chat_id, user_id, "Пожалуйста, назовите желаемую дату.", back_to_menu_attachments())
        return True
    if lh._is_service_cost_question(t):
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Ориентировочная трудоёмкость: 2 часа. Стоимость уточняется при записи.",
            back_to_menu_attachments(),
        )
        return True
    target = parse_date(t, date_type.today())
    if not target:
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Не удалось понять дату. Напишите, например: завтра, послезавтра, 15 февраля или 15.02.",
            back_to_menu_attachments(),
        )
        return True
    if not is_working_day(target):
        await _send(
            client,
            http,
            chat_id,
            user_id,
            f"{target.strftime('%d.%m.%Y')} — выходной день. Назовите другую дату.",
            back_to_menu_attachments(),
        )
        return True
    time_pref = parse_time_preference(t)
    explicit_hour = parse_explicit_time_hour(t)
    after_dt = None
    if explicit_hour:
        hour, _ = explicit_hour
        after_dt = datetime.combine(target, datetime.min.time()).replace(hour=hour, minute=0, second=0)
        time_pref = None
    update_data(user_id, service_preferred_date=target.isoformat(), service_time_preference=time_pref)
    data = session_data(user_id)
    work_wishes = data.get("service_work_wishes", "")
    labor_min, _ = get_labor_and_cost(work_wishes)
    slot_duration = labor_min + 30
    slot_info, in_priority = await find_nearest_slot_1c(
        preferred_date=target,
        slot_duration_min=slot_duration,
        days_priority=3,
        time_window=time_pref,
        after_datetime=after_dt,
    )
    if not slot_info:
        slot_info, in_priority = find_nearest_slot(
            preferred_date=target,
            slot_duration_min=slot_duration,
            days_priority=3,
            time_window=time_pref,
            after_datetime=after_dt,
        )
    if not slot_info:
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "К сожалению, на указанную дату и ближайшие дни свободных мест нет. Назовите другой день.",
            back_to_menu_attachments(),
        )
        return True
    slot_str = slot_info.start.strftime("%d.%m в %H:%M")
    update_data(
        user_id,
        service_proposed_slot_start=slot_info.start.isoformat(),
        service_proposed_slot_end=slot_info.end.isoformat(),
        service_proposed_post_id=slot_info.post_id or "",
        service_proposed_acceptor_id=slot_info.acceptor_id or "",
        service_proposed_duration_min=slot_duration,
        service_1c_booking_id=None,
    )
    msg = f"Могу предложить Вам {slot_str}. Вам подходит?" if in_priority else (
        "В ближайшие 3 дня свободных мест нет. Ближайший свободный слот: "
        f"{slot_str}. Вам подходит?"
    )
    set_state(user_id, ST_WAITING_SERVICE_SLOT_CONFIRM)
    await _send(client, http, chat_id, user_id, msg, back_to_menu_attachments())
    return True


async def _handle_service_slot_confirm(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    """Как handle_service_slot_confirm в telegram_bot/handlers/lead_handler.py."""
    from datetime import datetime as dt_class

    text_lower = text.strip().lower()
    data = session_data(user_id)

    if lh._is_service_cost_question(text_lower):
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Ориентировочная трудоёмкость: 2 часа. Стоимость уточняется при записи.",
            back_to_menu_attachments(),
        )
        return True

    confirm_words = ["да", "подходит", "давайте", "ок", "окей", "согласен", "хорошо", "давай"]
    reject_later_words = ["позже", "попозже", "позже хочу", "на позже"]
    reject_preference_words = ["после обеда", "послеобед", "утром", "вечером", "до обеда", "в обед"]
    reject_other_day_words = ["другой день", "передумал"]

    wants_different_time = (
        any(w in text_lower for w in reject_later_words)
        or any(w in text_lower for w in reject_preference_words)
        or any(w in text_lower for w in ["нет", "не подходит", "другое время"])
        or parse_explicit_time_hour(text_lower) is not None
    )
    if wants_different_time:
        slot_start_str = data.get("service_proposed_slot_start")
        preferred_date_str = data.get("service_preferred_date")
        if slot_start_str and preferred_date_str:
            last_proposed = dt_class.fromisoformat(slot_start_str)
            preferred_date = (
                dt_class.fromisoformat(preferred_date_str).date() if preferred_date_str else last_proposed.date()
            )
            work_wishes = data.get("service_work_wishes", "")
            labor_min, _ = get_labor_and_cost(work_wishes)
            slot_duration = labor_min + 30
            time_pref = data.get("service_time_preference")
            after_dt = None
            explicit = parse_explicit_time_hour(text_lower)
            if explicit:
                hour, _ = explicit
                after_dt = datetime.combine(preferred_date, datetime.min.time()).replace(
                    hour=hour, minute=0, second=0
                )
                time_pref = None
            elif any(w in text_lower for w in reject_preference_words):
                time_pref = parse_time_preference(text_lower) or time_pref
                after_dt = None
            elif any(w in text_lower for w in reject_later_words):
                hours = parse_later_hours(text_lower)
                add_hours = hours if hours is not None else 2
                after_dt = last_proposed + timedelta(hours=add_hours)
                time_pref = None
            elif any(w in text_lower for w in ["нет", "не подходит", "другое время"]):
                if any(w in text_lower for w in reject_preference_words):
                    time_pref = parse_time_preference(text_lower) or time_pref
                else:
                    after_dt = last_proposed + timedelta(hours=2)
            else:
                after_dt = last_proposed + timedelta(hours=2)
            slot_info, in_priority = await find_nearest_slot_1c(
                preferred_date=preferred_date,
                slot_duration_min=slot_duration,
                days_priority=7,
                time_window=time_pref,
                after_datetime=after_dt,
            )
            if not slot_info:
                slot_info, in_priority = find_nearest_slot(
                    preferred_date=preferred_date,
                    slot_duration_min=slot_duration,
                    days_priority=7,
                    time_window=time_pref,
                    after_datetime=after_dt,
                )
            if slot_info:
                slot_str = slot_info.start.strftime("%d.%m в %H:%M")
                update_data(
                    user_id,
                    service_proposed_slot_start=slot_info.start.isoformat(),
                    service_proposed_slot_end=slot_info.end.isoformat(),
                    service_proposed_post_id=slot_info.post_id or "",
                    service_proposed_acceptor_id=slot_info.acceptor_id or "",
                    service_proposed_duration_min=slot_duration,
                    service_time_preference=time_pref,
                )
                msg = f"Могу предложить Вам {slot_str}. Вам подходит?"
                if in_priority is False:
                    msg = "В указанное время мест нет. " + msg
                await _send(client, http, chat_id, user_id, msg, back_to_menu_attachments())
                return True
            set_state(user_id, ST_WAITING_SERVICE_DATE)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "В этот день в указанное время свободных мест нет. Назовите другой день, я постараюсь подобрать для Вас время.",
                back_to_menu_attachments(),
            )
            return True
        set_state(user_id, ST_WAITING_SERVICE_DATE)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Назовите другой день, я постараюсь подобрать для Вас время.",
            back_to_menu_attachments(),
        )
        return True

    if any(w in text_lower for w in reject_other_day_words):
        set_state(user_id, ST_WAITING_SERVICE_DATE)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Назовите другой день, я постараюсь подобрать для Вас время.",
            back_to_menu_attachments(),
        )
        return True

    if any(w in text_lower for w in confirm_words):
        slot_start_str = data.get("service_proposed_slot_start")
        slot_start = dt_class.fromisoformat(slot_start_str) if slot_start_str else None
        if not slot_start:
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Произошла ошибка. Назовите другой день, я постараюсь подобрать время.",
                back_to_menu_attachments(),
            )
            set_state(user_id, ST_WAITING_SERVICE_DATE)
            return True

        booking_id = await create_1c_booking(
            fio=data.get("service_fio", ""),
            phone=data.get("service_phone", ""),
            car_brand=data.get("service_car_brand", ""),
            car_model=data.get("service_car_model", ""),
            car_year=data.get("service_car_year_mileage", ""),
            car_mileage="",
            work_wishes=data.get("service_work_wishes", ""),
            slot_start=slot_start,
            post_id=str(data.get("service_proposed_post_id") or ""),
            acceptor_id=str(data.get("service_proposed_acceptor_id") or ""),
            duration_min=data.get("service_proposed_duration_min"),
        )
        slot_str = slot_start.strftime("%d.%m в %H:%M")
        fio = data.get("service_fio", "")
        update_data(
            user_id,
            service_1c_booking_id=booking_id,
            service_confirmed_slot_start=slot_start_str,
        )
        need_text = (
            f"Запись на ТО: {data.get('service_car_brand', '')} {data.get('service_car_model', '')}, "
            f"дата {slot_str}, работы: {data.get('service_work_wishes', '')}"
        )
        department = lh.group_router.get_department_name(ClientNeed.SERVICE)
        group_id = lh.group_router.get_group_id(ClientNeed.SERVICE)
        lead_msg = "📱 MAX\n" + lh.group_router.format_lead_message(
            fio=fio,
            phone=data.get("service_phone", ""),
            need_text=need_text,
            need=ClientNeed.SERVICE,
            timestamp=format_datetime(datetime.now()),
            has_photo=False,
            car_brand=data.get("service_car_brand"),
            car_model=data.get("service_car_model"),
            car_year=data.get("service_car_year_mileage"),
            car_mileage=None,
            work_wishes=data.get("service_work_wishes"),
        )
        await notify_max_department(client, http, ClientNeed.SERVICE, lead_msg)
        lead_id = None
        try:
            lead_id = TelegramLeadsDB.save_lead(
                telegram_user_id=user_id,
                telegram_username=None,
                client_fio=fio,
                client_phone=data.get("service_phone", ""),
                need_type=ClientNeed.SERVICE.value,
                need_text=need_text,
                department=department,
                group_id=group_id,
                working_hours=is_dealer_quick_response_window(dealer_local_now()),
                response_message=get_response_message_template(True, department),
                car_brand=data.get("service_car_brand"),
                car_model=data.get("service_car_model"),
                car_year=data.get("service_car_year_mileage"),
                car_mileage=None,
                work_wishes=data.get("service_work_wishes"),
                source="max",
            )
        except Exception as e:
            logger.error("save_lead service: %s", e)

        await _send(client, http, chat_id, user_id, f"{fio}, Вы записаны на {slot_str}.", main_menu_attachments(user_id))
        await _send(client, http, chat_id, user_id, "Могу ли я ещё чем-нибудь Вам помочь?", main_menu_attachments(user_id))
        update_data(
            user_id,
            fio=fio,
            phone=data.get("service_phone"),
            last_lead_id=lead_id,
            last_lead_group_id=group_id,
            last_need=ClientNeed.SERVICE.value,
            more_need_attempts=0,
        )
        set_state(user_id, ST_WAITING_MORE_NEED)
        return True

    await _send(
        client,
        http,
        chat_id,
        user_id,
        "Напишите «да» или «подходит», если время устраивает, либо «позже» / «после обеда» / «другой день» для выбора другого времени.",
        back_to_menu_attachments(),
    )
    return True


async def _handle_service_final_confirm(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    """Упрощённо: дублируем финальный лид если нужно — в TG чаще есть отдельный шаг; для MAX слит с confirm."""
    set_state(user_id, ST_WAITING_MORE_NEED)
    await _send(client, http, chat_id, user_id, "Могу ли я ещё чем-нибудь Вам помочь?", main_menu_attachments(user_id))
    return True


async def _handle_car_brand_model(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    t = text.strip()
    if not t:
        await _send(client, http, chat_id, user_id, "Пожалуйста, укажите марку и модель автомобиля.", back_to_menu_attachments())
        return True
    parts = t.split(maxsplit=1)
    update_data(user_id, car_brand=parts[0], car_model=parts[1] if len(parts) > 1 else "")
    set_state(user_id, ST_WAITING_CAR_YEAR)
    await _send(client, http, chat_id, user_id, "Год выпуска автомобиля?", back_to_menu_attachments())
    return True


async def _handle_car_year(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    t = text.strip()
    if not t:
        await _send(client, http, chat_id, user_id, "Пожалуйста, укажите год выпуска.", back_to_menu_attachments())
        return True
    update_data(user_id, car_year=t)
    set_state(user_id, ST_WAITING_CAR_MILEAGE)
    await _send(client, http, chat_id, user_id, "Приблизительный пробег на сегодня (км)?", back_to_menu_attachments())
    return True


async def _handle_car_mileage(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    t = text.strip()
    if not t:
        await _send(client, http, chat_id, user_id, "Пожалуйста, укажите приблизительный пробег (км).", back_to_menu_attachments())
        return True
    update_data(user_id, car_mileage=t)
    set_state(user_id, ST_WAITING_CAR_WISHES)
    await _send(
        client,
        http,
        chat_id,
        user_id,
        "Опишите, пожалуйста, пожелания по работам "
        "(например: ТО, шиномонтаж, окраска бампера).",
        back_to_menu_attachments(),
    )
    return True


async def _handle_car_wishes(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    update_data(user_id, work_wishes=text.strip() or None)
    data = session_data(user_id)
    if data.get("fio") and data.get("phone"):
        extra = await finalize_lead_max(
            client, http, chat_id=chat_id, user_id=user_id, data=data, phone=data["phone"], phone_alt=data.get("phone_alt")
        )
        update_data(user_id, **extra)
        set_state(user_id, ST_WAITING_MORE_NEED)
    else:
        set_state(user_id, ST_WAITING_FIO)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
            back_to_menu_attachments(),
        )
    return True


async def _handle_fio(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    data = session_data(user_id)
    if data.get("fio") and data.get("phone"):
        extra = await finalize_lead_max(
            client, http, chat_id=chat_id, user_id=user_id, data=data, phone=data["phone"], phone_alt=data.get("phone_alt")
        )
        update_data(user_id, **extra)
        set_state(user_id, ST_WAITING_MORE_NEED)
        return True
    t = text.strip()
    brand = data.get("brand") or "chery_tenet"
    if not t:
        await _send(client, http, chat_id, user_id, "Пожалуйста, укажите Ваше ФИО.", main_menu_attachments(user_id))
        return True
    lower = t.lower()
    if any(w in lower for w in ["купить", "авто", "автомоб", "машин", "запис", "ремонт", "пробег", "запчаст"]):
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Похоже, Вы продолжаете описывать запрос. Пожалуйста, укажите фамилию, имя и отчество полностью.",
            main_menu_attachments(user_id),
        )
        return True
    update_data(user_id, fio=t)
    set_state(user_id, ST_WAITING_PHONE)
    await _send(
        client,
        http,
        chat_id,
        user_id,
        f'Я записал Ваши данные, Вы {t}. Подтвердите номер кнопкой «Передать номер телефона» или введите вручную +7 XXX XXX XX XX.',
        phone_prompt_attachments(),
    )
    return True


async def _handle_phone(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    raw = text.strip()
    tl = raw.lower()
    data = session_data(user_id)
    phone_typed = data.get("phone_typed")

    if tl in ("готово", "да", "продолжить", "ок", "окей") and phone_typed:
        extra = await finalize_lead_max(
            client, http, chat_id=chat_id, user_id=user_id, data=data, phone=phone_typed, phone_alt=None
        )
        update_data(user_id, **extra)
        set_state(user_id, ST_WAITING_MORE_NEED)
        return True

    phone = normalize_phone(raw)
    if phone:
        update_data(user_id, phone_typed=phone)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Номер записан. Нажмите «Передать номер телефона» для другого номера или «Готово» для продолжения.",
            phone_after_typed_attachments(),
        )
        return True

    attempts = int(data.get("invalid_phone_attempts", 0)) + 1
    update_data(user_id, invalid_phone_attempts=attempts)
    if attempts >= 3 and not phone_typed:
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Без корректного номера не смогу передать запрос. Напишите /start для нового диалога.",
            main_menu_attachments(user_id),
        )
        clear_session(user_id)
        return True
    intent_keywords = ["купить", "машин", "авто", "хочу", "запис", "ремонт", "сервис", "запчасти"]
    looks_like_intent = any(kw in tl for kw in intent_keywords) and not any(c.isdigit() for c in raw)
    if looks_like_intent:
        msg = (
            "Сначала подтвердите номер телефона — он нужен для передачи заявки менеджеру. "
            "Введите номер в формате +7 XXX XXX XX XX или нажмите «Передать номер телефона»."
        )
    else:
        msg = "Номер введён некорректно. Введите +7 XXX XXX XX XX или нажмите «Передать номер телефона»."
    await _send(client, http, chat_id, user_id, msg, phone_prompt_attachments())
    return True


async def _handle_new_used(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    """Как handle_new_used_choice в Telegram (новый / пробег / чужая марка)."""
    text_raw = text.strip()
    t = text_raw.lower()
    data = session_data(user_id)
    brand = data.get("brand") or "chery_tenet"
    need_candidate_value = data.get("need_candidate") or ClientNeed.CHERY_TENET.value

    if any(b in t for b in lh.FOREIGN_BRAND_KEYWORDS):
        final_need = ClientNeed.USED_CARS
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Я могу перевести Вас на отдел автомобили с пробегом. "
            "Там Вам постараются помочь с Вашим запросом.",
            main_menu_attachments(user_id),
        )
    elif "пробег" in t or "б/у" in t or "бу" in t or "бэу" in t:
        final_need = ClientNeed.USED_CARS
    elif "нов" in t:
        final_need = ClientNeed.CHERY_TENET
    else:
        final_need = ClientNeed(need_candidate_value)

    update_data(user_id, need=final_need.value, need_text=text_raw)

    if data.get("fio") and data.get("phone"):
        d2 = session_data(user_id)
        extra = await finalize_lead_max(
            client, http, chat_id=chat_id, user_id=user_id, data=d2, phone=d2["phone"], phone_alt=d2.get("phone_alt")
        )
        update_data(user_id, **extra)
        set_state(user_id, ST_WAITING_MORE_NEED)
    else:
        set_state(user_id, ST_WAITING_FIO)
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
            back_to_menu_attachments(),
        )
    return True


async def _handle_more_need(
    client: "MaxApiClient", http: aiohttp.ClientSession, chat_id: Optional[int], user_id: int, text: str
) -> bool:
    """Как handle_more_need в Telegram (часы работы, детектор, уточнение лида, новый/пробег, секретарь)."""
    t = text.strip().lower()
    msg_full = text.strip()

    if any(kw in t for kw in lh.MORE_HELP_NO_KEYWORDS):
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Спасибо за обращение. До свидания. Для нового диалога напишите /start или «меню».",
            main_menu_attachments(user_id),
        )
        clear_session(user_id)
        return True

    if any(kw in t for kw in lh.WORKING_HOURS_KEYWORDS):
        base = date_type.today()
        if "завтра" in t:
            d = base + timedelta(days=1)
        elif "послезавтра" in t or "после завтра" in t:
            d = base + timedelta(days=2)
        elif "сегодня" in t:
            d = base
        else:
            parsed = parse_date(msg_full, base)
            d = parsed if parsed else base
        msg_wh = get_working_hours_message(d)
        data_wh = session_data(user_id)
        brand = data_wh.get("brand") or "chery_tenet"
        await _send(client, http, chat_id, user_id, msg_wh, main_menu_attachments(user_id))
        await _send(
            client, http, chat_id, user_id, "Могу ли я ещё чем-нибудь Вам помочь?", main_menu_attachments(user_id)
        )
        return True

    tl_check = t.strip().lower()
    if tl_check in ("оп tenet & chery", "оп jetour"):
        update_data(
            user_id, brand="chery_tenet", need=ClientNeed.CHERY_TENET.value, need_text=text
        )
        data_op = session_data(user_id)
        if data_op.get("fio") and data_op.get("phone"):
            extra = await finalize_lead_max(
                client,
                http,
                chat_id=chat_id,
                user_id=user_id,
                data=data_op,
                phone=data_op["phone"],
                phone_alt=data_op.get("phone_alt"),
            )
            update_data(user_id, **extra)
            set_state(user_id, ST_WAITING_MORE_NEED)
        else:
            set_state(user_id, ST_WAITING_FIO)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                back_to_menu_attachments(),
            )
        return True

    data = session_data(user_id)
    last_need = data.get("last_need")
    last_lead_id = data.get("last_lead_id")
    last_lead_group_id = data.get("last_lead_group_id")
    fio = data.get("fio")
    phone = data.get("phone")
    attempts = int(data.get("more_need_attempts", 0)) + 1
    update_data(user_id, more_need_attempts=attempts)

    need = lh.need_detector.detect(msg_full)
    brand = data.get("brand") or "chery_tenet"

    if need is not None and need == ClientNeed.CHERY_TENET:
        if any(b in t for b in lh.FOREIGN_BRAND_KEYWORDS):
            need = ClientNeed.USED_CARS
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Я могу перевести Вас на отдел автомобили с пробегом. "
                "Там Вам постараются помочь с Вашим запросом.",
                main_menu_attachments(user_id),
            )

    if need:
        update_data(user_id, more_need_attempts=0)
        if need.value == last_need and last_lead_id and last_lead_group_id:
            purchase_needs = (ClientNeed.CHERY_TENET, ClientNeed.USED_CARS)
            need_text_lower = (data.get("need_text") or "").lower()
            msg_lower = t.strip().lower()
            is_repeated_purchase = (
                need in purchase_needs
                and any(kw in msg_lower for kw in ["купить", "машин", "авто", "хочу"])
                and any(kw in need_text_lower for kw in ["купить", "машин", "авто", "хочу"])
            )
            if not is_repeated_purchase:
                TelegramLeadsDB.update_lead_append(lead_id=last_lead_id, work_wishes_append=msg_full)
                update_msg = lh.group_router.format_lead_update_message(
                    lead_id=last_lead_id,
                    fio=fio,
                    phone=phone or "",
                    additional_wishes=msg_full,
                    timestamp=format_datetime(datetime.now()),
                )
                if last_need:
                    await notify_max_department(
                        client, http, ClientNeed(last_need), "📱 MAX\n" + update_msg
                    )
            else:
                dept_name = lh.group_router.get_department_name(need)
                await _send(
                    client,
                    http,
                    chat_id,
                    user_id,
                    f"Ваш запрос на покупку автомобиля уже передан в {dept_name}. "
                    "С Вами свяжутся. Могу ли помочь чем-то ещё?",
                    main_menu_attachments(user_id),
                )
                return True
            await _send(
                client, http, chat_id, user_id, "Могу ли я ещё чем-нибудь Вам помочь?", main_menu_attachments(user_id)
            )
            return True

        update_data(user_id, need=need.value, need_text=msg_full)

        new_markers = ["новый", "новый автомобиль", "новая машина"]
        used_markers = lh.need_detector.KEYWORDS.get(ClientNeed.USED_CARS, [])
        nu_lower = msg_full.strip().lower()
        has_new = any(m in nu_lower for m in new_markers)
        has_used = any(m in nu_lower for m in used_markers)
        if need == ClientNeed.CHERY_TENET and not has_new and not has_used:
            update_data(user_id, need_candidate=need.value)
            set_state(user_id, ST_WAITING_NEW_USED)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Подскажите: вы рассматриваете новый автомобиль или с пробегом? "
                "Напишите «новый автомобиль» или «автомобиль с пробегом».",
                back_to_menu_attachments(),
            )
            return True

        if need == ClientNeed.SERVICE:
            if not max_service_booking_via_alfa_enabled():
                await _service_department_without_booking(client, http, chat_id, user_id, need_text=msg_full)
                return True
            has_fio_phone = data.get("fio") and data.get("phone")
            if lh._is_service_book_button(msg_full.strip()):
                if has_fio_phone:
                    update_data(user_id, service_fio=data.get("fio"), service_phone=data.get("phone"))
                    set_state(user_id, ST_WAITING_SERVICE_CAR)
                    await _send(
                        client,
                        http,
                        chat_id,
                        user_id,
                        "Назовите марку и модель Вашего автомобиля.",
                        back_to_menu_attachments(),
                    )
                else:
                    set_state(user_id, ST_WAITING_SERVICE_FIO)
                    await _send(
                        client,
                        http,
                        chat_id,
                        user_id,
                        "Назовите ваши фамилию, имя и отчество.",
                        main_menu_attachments(user_id),
                    )
                return True
            set_state(user_id, ST_WAITING_SERVICE_CHOICE)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Я могу записать Вас на ТО либо передать Ваш контакт ассистенту Слесарного цеха.",
                main_menu_attachments(user_id),
            )
            return True
        if need == ClientNeed.BODY_REPAIR:
            update_data(
                user_id,
                car_brand=None,
                car_model=None,
                car_year=None,
                car_mileage=None,
                work_wishes=None,
            )
            set_state(user_id, ST_WAITING_CAR_BRAND_MODEL)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Укажите, пожалуйста, марку и модель автомобиля (например: Toyota Camry).",
                main_menu_attachments(user_id),
            )
            return True
        if data.get("fio") and data.get("phone"):
            extra = await finalize_lead_max(
                client,
                http,
                chat_id=chat_id,
                user_id=user_id,
                data=data,
                phone=data["phone"],
                phone_alt=data.get("phone_alt"),
            )
            update_data(user_id, **extra)
            set_state(user_id, ST_WAITING_MORE_NEED)
        else:
            set_state(user_id, ST_WAITING_FIO)
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                back_to_menu_attachments(),
            )
        return True

    if attempts >= 2:
        if not data.get("phone"):
            await _send(
                client,
                http,
                chat_id,
                user_id,
                "Для передачи Вашего запроса менеджеру нужен номер телефона. "
                "Если захотите оставить заявку, откройте меню и начните диалог заново.",
                main_menu_attachments(user_id),
            )
            clear_session(user_id)
            return True
        department = lh.group_router.get_department_name(ClientNeed.SECRETARY)
        group_id = lh.group_router.get_group_id(ClientNeed.SECRETARY)
        is_working = is_dealer_quick_response_window(dealer_local_now())
        response_template = get_response_message_template(is_working, department)
        await _send(client, http, chat_id, user_id, response_template, main_menu_attachments(user_id))
        await _send(
            client,
            http,
            chat_id,
            user_id,
            "Спасибо за обращение. До свидания. Для нового диалога напишите /start.",
            main_menu_attachments(user_id),
        )
        try:
            lead_msg = "📱 MAX\n" + lh.group_router.format_lead_message(
                fio=data.get("fio", ""),
                phone=data.get("phone", ""),
                need_text=msg_full or "",
                need=ClientNeed.SECRETARY,
                timestamp=format_datetime(datetime.now()),
                has_photo=False,
                phone_alt=data.get("phone_alt"),
            )
            await notify_max_department(client, http, ClientNeed.SECRETARY, lead_msg)
            TelegramLeadsDB.save_lead(
                telegram_user_id=user_id,
                telegram_username=None,
                client_fio=data.get("fio", ""),
                client_phone=data.get("phone", ""),
                need_type=ClientNeed.SECRETARY.value,
                phone_alt=data.get("phone_alt"),
                need_text=msg_full,
                department=department,
                group_id=group_id,
                working_hours=is_working,
                response_message=response_template,
                source="max",
            )
        except Exception as e:
            logger.error("more_need secretary lead: %s", e)
        clear_session(user_id)
        return True

    await _send(
        client,
        http,
        chat_id,
        user_id,
        "Уточните, пожалуйста: Вас интересует новый автомобиль (Chery/Tenet), "
        "автомобиль с пробегом, слесарный или кузовной цех, запчасти или что-то другое (Прочие)?",
        main_menu_attachments(user_id),
    )
    return True
