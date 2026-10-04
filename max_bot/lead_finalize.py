"""
Завершение заявки после ФИО+телефон: ответ в MAX, лид в чаты MAX по отделу, БД, 1С.
В Telegram лиды MAX-бота не отправляются.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

import aiohttp

from database import TelegramLeadsDB
from max_bot.keyboards import main_menu_attachments
from max_bot.notify_max_channels import max_chat_id_for_need, notify_max_department
from telegram_bot.handlers.lead_handler import group_router, ics_service
from telegram_bot.services.need_detector import ClientNeed
from telegram_bot.services.working_hours_schedule import dealer_local_now, is_dealer_quick_response_window
from telegram_bot.utils.phone_normalizer import normalize_phone
from telegram_bot.utils.time_utils import format_datetime, get_response_message_template

if TYPE_CHECKING:
    from max_bot.api_client import MaxApiClient

logger = logging.getLogger(__name__)


async def finalize_lead_max(
    client: "MaxApiClient",
    http: aiohttp.ClientSession,
    *,
    chat_id: Optional[int],
    user_id: int,
    data: dict[str, Any],
    phone: str,
    phone_alt: Optional[str] = None,
    custom_response: Optional[str] = None,
) -> dict[str, Any]:
    """
    Отправляет ответ в MAX, лид в чаты MAX (notify_max_department), save_lead(source=max), ICS.
    Возвращает обновлённые поля для session data.
    """
    fio = data.get("fio")
    phone_n = normalize_phone(phone or "") if phone else None
    if phone_n:
        phone = phone_n
    elif phone and len(phone) > 80:
        logger.warning("MAX лид: телефон не нормализован (длина %s), проверьте vCard / ввод", len(phone))
    brand = data.get("brand") or "chery_tenet"
    need_value = data.get("need")
    need_text = data.get("need_text", "") or ""
    car_brand = data.get("car_brand")
    car_model = data.get("car_model")
    car_year = data.get("car_year")
    car_mileage = data.get("car_mileage")
    work_wishes = data.get("work_wishes")

    need = ClientNeed(need_value) if need_value else ClientNeed.SECRETARY
    department = group_router.get_department_name(need)
    now = dealer_local_now()
    is_working = is_dealer_quick_response_window(now)
    response_template = get_response_message_template(is_working, department)
    override = custom_response or data.get("custom_response")
    response_text = override if override else f"{fio}, {response_template}"

    await client.send_message(
        http,
        chat_id=chat_id,
        user_id=None if chat_id is not None else user_id,
        text=response_text,
        attachments=main_menu_attachments(user_id),
    )

    group_id = group_router.get_group_id(need)
    lead_message = group_router.format_lead_message(
        fio=fio or "",
        phone=phone,
        need_text=need_text,
        need=need,
        timestamp=format_datetime(now),
        has_photo=False,
        phone_alt=phone_alt,
        car_brand=car_brand,
        car_model=car_model,
        car_year=car_year,
        car_mileage=car_mileage,
        work_wishes=work_wishes,
    )
    lead_message = "📱 MAX\n" + lead_message

    max_cid = max_chat_id_for_need(need)
    ok_max = await notify_max_department(client, http, need, lead_message)
    if max_cid is not None and ok_max:
        logger.info(
            "MAX лид отправлен в чат MAX chat_id=%s need=%s department=%s",
            max_cid,
            need.value,
            department,
        )
    elif max_cid is not None and not ok_max:
        logger.error("MAX лид не доставлен в чат %s (ошибка send_message)", max_cid)

    lead_id = None
    try:
        lead_id = TelegramLeadsDB.save_lead(
            telegram_user_id=user_id,
            telegram_username=None,
            client_fio=fio or "",
            client_phone=phone,
            need_type=need.value,
            phone_alt=phone_alt,
            need_text=need_text,
            department=department,
            group_id=group_id,
            working_hours=is_working,
            response_message=response_template,
            car_brand=car_brand,
            car_model=car_model,
            car_year=car_year,
            car_mileage=car_mileage,
            work_wishes=work_wishes,
            source="max",
        )
        logger.info("MAX лид сохранён: ID=%s", lead_id)
    except Exception as e:
        logger.error("Ошибка сохранения лида MAX: %s", e)

    try:
        ics_created = await ics_service.create_notification(
            fio=fio or "",
            phone=phone,
            need=need.value,
            department=department,
            need_text=need_text,
            telegram_user_id=user_id,
            car_brand=car_brand,
            car_model=car_model,
            car_year=car_year,
            car_mileage=car_mileage,
            work_wishes=work_wishes,
        )
        client_1c = await ics_service.find_client(fio or "", phone)
        ics_client_found = client_1c is not None
        ics_client_id = client_1c.get("id") if client_1c else None
        if lead_id:
            TelegramLeadsDB.update_ics_status(
                lead_id=lead_id,
                ics_notification_created=ics_created,
                ics_client_found=ics_client_found,
                ics_client_id=ics_client_id,
            )
        if client_1c:
            await ics_service.save_telegram_user_id(client_1c.get("id"), user_id)
    except Exception as e:
        logger.error("ICS для MAX-лида: %s", e)

    await client.send_message(
        http,
        chat_id=chat_id,
        user_id=None if chat_id is not None else user_id,
        text="Могу ли я ещё чем-нибудь Вам помочь?",
        attachments=main_menu_attachments(user_id),
    )

    return {
        "phone": phone,
        "phone_alt": phone_alt,
        "phone_typed": None,
        "last_lead_id": lead_id,
        "last_lead_group_id": group_id,
        "last_need": need.value,
        "more_need_attempts": 0,
        "custom_response": None,
    }
