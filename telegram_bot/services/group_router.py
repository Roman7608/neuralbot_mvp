"""
Маршрутизация сообщений в группы отделов.
"""

import logging
from typing import Optional

from telegram_bot.services.need_detector import ClientNeed
from telegram_bot_config import (
    GROUP_CHERY_TENET,
    GROUP_USED_CARS,
    GROUP_SERVICE,
    GROUP_BODY_REPAIR,
    GROUP_SPARES,
    GROUP_SECRETARY,
)

logger = logging.getLogger(__name__)


class GroupRouter:
    """Маршрутизирует заявки в соответствующие группы."""
    
    NEED_TO_GROUP = {
        ClientNeed.CHERY_TENET: GROUP_CHERY_TENET,
        ClientNeed.USED_CARS: GROUP_USED_CARS,
        ClientNeed.SERVICE: GROUP_SERVICE,
        ClientNeed.BODY_REPAIR: GROUP_BODY_REPAIR,
        ClientNeed.SPARES: GROUP_SPARES,
        ClientNeed.SECRETARY: GROUP_SECRETARY,
    }
    
    NEED_TO_NAME = {
        ClientNeed.CHERY_TENET: "ОП Chery/Tenet",
        ClientNeed.USED_CARS: "Отдел продаж автомобилей с пробегом",
        ClientNeed.SERVICE: "Приемка слесарного цеха",
        ClientNeed.BODY_REPAIR: "Приемка кузовного цеха",
        ClientNeed.SPARES: "Отдел запчастей",
        ClientNeed.SECRETARY: "Прочие",
    }
    
    def get_group_id(self, need: ClientNeed) -> int:
        """
        Возвращает ID группы для потребности.
        
        :param need: Потребность клиента
        :return: ID группы Telegram
        """
        return self.NEED_TO_GROUP.get(need, GROUP_SECRETARY)
    
    def get_department_name(self, need: ClientNeed) -> str:
        """
        Возвращает название отдела для потребности.
        
        :param need: Потребность клиента
        :return: Название отдела
        """
        return self.NEED_TO_NAME.get(need, "Прочие")
    
    def format_lead_message(
        self,
        fio: str,
        phone: str,
        need_text: str,
        need: ClientNeed,
        timestamp: str,
        has_photo: bool = False,
        phone_alt: Optional[str] = None,
        car_brand: Optional[str] = None,
        car_model: Optional[str] = None,
        car_year: Optional[str] = None,
        car_mileage: Optional[str] = None,
        work_wishes: Optional[str] = None,
    ) -> str:
        """
        Форматирует сообщение для отправки в группу.
        Для слесарного/кузовного цеха добавляет марку, модель, год, пробег, пожелания по работам.
        """
        message = "🆕 НОВАЯ ЗАЯВКА\n\n"
        message += f"👤 Клиент: {fio}\n"
        message += f"📞 Телефон: {phone}\n"
        if phone_alt:
            message += f"📞 Телефон доп.: {phone_alt}\n"
        message += f"📝 Потребность: {need_text}\n"
        if car_brand or car_model:
            message += f"🚗 Марка: {car_brand or '—'}\n"
            message += f"🚗 Модель: {car_model or '—'}\n"
        if car_year:
            message += f"📅 Год выпуска: {car_year}\n"
        if car_mileage:
            message += f"📏 Пробег (км): {car_mileage}\n"
        if work_wishes:
            message += f"🔧 Пожелания по работам: {work_wishes}\n"
        message += f"🕐 Время: {timestamp}\n"
        if has_photo:
            message += "\n📷 ФОТО ОТ КЛИЕНТА"
        return message

    def format_lead_update_message(
        self,
        lead_id: int,
        fio: str,
        phone: str,
        additional_wishes: str,
        timestamp: str,
    ) -> str:
        """Форматирует сообщение об уточнении по заявке (доп. пожелания в ту же группу)."""
        return (
            f"📝 УТОЧНЕНИЕ ПО ЗАЯВКЕ №{lead_id}\n\n"
            f"👤 Клиент: {fio}\n"
            f"📞 Телефон: {phone}\n"
            f"🔧 Доп. пожелания: {additional_wishes}\n"
            f"🕐 Время: {timestamp}\n"
        )
