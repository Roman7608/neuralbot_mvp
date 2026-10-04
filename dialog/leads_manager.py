"""
Менеджер для записи лидов.

Поддерживает два режима:
1. Excel файл (для обратной совместимости)
2. PostgreSQL (основной режим)
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from postgresql_config import POSTGRESQL_SESSION_TIMEZONE

import pandas as pd

# Попытка импорта модуля БД (может быть недоступен)
try:
    from database import TelegramLeadsDB
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("Модуль database недоступен, используется только Excel")

logger = logging.getLogger(__name__)


class LeadsManager:
    """Менеджер для записи лидов."""
    
    def __init__(self, leads_file_path: Path, use_database: bool = True):
        """
        Инициализирует менеджер лидов.
        
        :param leads_file_path: Путь к файлу для записи лидов (для обратной совместимости)
        :param use_database: Использовать PostgreSQL вместо Excel
        """
        self.leads_file_path = Path(leads_file_path)
        self.use_database = use_database and DB_AVAILABLE
        logger.info(
            "Инициализирован LeadsManager (file=%s, use_database=%s)",
            self.leads_file_path,
            self.use_database,
        )
    
    def save_lead(
        self,
        client_name: Optional[str] = None,
        phone: Optional[str] = None,
        need: Optional[str] = None,
        service_data: Optional[Dict] = None,
        outcome: Optional[str] = None,
        working_hours: bool = True,
        source: str = "phone",
        voice_contact_outcome: Optional[str] = None,
        car_brand: Optional[str] = None,
        car_model: Optional[str] = None,
        car_year: Optional[str] = None,
        car_mileage: Optional[str] = None,
        work_wishes: Optional[str] = None,
        cdr_uniqueid: Optional[str] = None,
        voice_bot_session_id: Optional[int] = None,
    ) -> bool:
        """
        Сохраняет лид в файл.
        
        :param client_name: Имя клиента
        :param phone: Телефон клиента
        :param need: Потребность клиента
        :param service_data: Данные для записи на сервис (если применимо)
        :param outcome: Результат разговора (переведен, записан, завершен и т.д.)
        :param working_hours: Рабочие часы обращения (для фильтра в админке «Лиды»)
        :param source: В БД: phone — голосовой бот (по умолчанию)
        :param voice_contact_outcome: для phone: см. VOICE_CONTACT_OUTCOME_CODES в postgresql_manager
        :return: True если успешно, False иначе
        """
        try:
            sd = service_data or {}
            service_1c_booking_id = sd.get("service_1c_booking_id")
            cb = car_brand if car_brand is not None else sd.get("car_brand")
            cm = car_model if car_model is not None else sd.get("car_model")
            cy = car_year if car_year is not None else sd.get("car_year")
            cmi = car_mileage if car_mileage is not None else (sd.get("mileage") or sd.get("car_mileage"))
            ww = work_wishes if work_wishes is not None else sd.get("work_list")
            outcome_with_booking = outcome or ""
            if service_1c_booking_id:
                outcome_with_booking = (
                    f"{outcome_with_booking} | 1C booking: {service_1c_booking_id}"
                    if outcome_with_booking
                    else f"1C booking: {service_1c_booking_id}"
                )
            from dialog.sto_to_price_inquiry import format_to_price_for_lead

            price_for_lead = format_to_price_for_lead(sd)
            outcome_with_booking = (
                f"{outcome_with_booking} | {price_for_lead}"
                if outcome_with_booking
                else price_for_lead
            )
            # Если используется БД, сохраняем туда
            if self.use_database:
                # Для голосового бота используем упрощенную версию
                # (Telegram-бот сохраняет через TelegramLeadsDB напрямую)
                lead_id = TelegramLeadsDB.save_lead(
                    telegram_user_id=0,  # Для голосового бота нет telegram_user_id
                    client_fio=client_name or "",
                    client_phone=phone or "",
                    need_type=need or "unknown",
                    need_text=outcome_with_booking,
                    department=need or "unknown",
                    group_id=0,  # Нет группы для голосового бота
                    working_hours=working_hours,
                    response_message=outcome_with_booking,
                    car_brand=cb,
                    car_model=cm,
                    car_year=cy,
                    car_mileage=cmi,
                    work_wishes=ww,
                    source=source,
                    voice_contact_outcome=voice_contact_outcome,
                    cdr_uniqueid=cdr_uniqueid,
                    voice_bot_session_id=voice_bot_session_id,
                )
                if lead_id:
                    logger.info(
                        "Лид сохранен в БД: ID=%s, имя=%s, телефон=%s",
                        lead_id,
                        client_name,
                        phone,
                    )
                    return True
            
            # Сохранение в Excel (для обратной совместимости или если БД недоступна)
            # Подготавливаем данные лида
            _vco_ok = frozenset({
                "bot_only", "no_operator", "transfer_started", "unknown",
                "hangup_before_transfer", "after_hours_partial", "session_complete",
            })
            vco = (voice_contact_outcome or "").strip()
            if vco and vco not in _vco_ok:
                vco = ""
            _outcome_labels = {
                "bot_only": "Только бот",
                "no_operator": "Не дозвонился до сотрудника",
                "transfer_started": "Перевод к сотруднику",
                "unknown": "Уточнить",
                "hangup_before_transfer": "Обрыв до перевода",
                "after_hours_partial": "Нерабочее время (короткий контакт)",
                "session_complete": "Сессия без перевода (данные по наличию)",
            }
            lead_data = {
                "Дата и время": datetime.now(ZoneInfo(POSTGRESQL_SESSION_TIMEZONE)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "Имя клиента": client_name or "",
                "Телефон": phone or "",
                "Потребность": need or "",
                "Результат": outcome_with_booking,
                "Дозвон до Викингов": _outcome_labels.get(vco, vco or ""),
            }
            
            # Добавляем данные сервиса, если есть
            if service_data:
                from dialog.sto_to_price_inquiry import format_to_price_for_lead

                price_for_lead = format_to_price_for_lead(service_data)
                lead_data.update({
                    "Марка": service_data.get("car_brand", ""),
                    "Модель": service_data.get("car_model", ""),
                    "Год": service_data.get("car_year", ""),
                    "Пробег": service_data.get("mileage", ""),
                    "Дата обслуживания": service_data.get("desired_date", ""),
                    "Список работ": service_data.get("work_list", ""),
                    "ID записи 1С": service_data.get("service_1c_booking_id", ""),
                    "Стоимость ТО": price_for_lead,
                })
            
            # Загружаем существующие лиды или создаем новый файл
            if self.leads_file_path.exists():
                try:
                    df = pd.read_excel(self.leads_file_path)
                except Exception as e:
                    logger.warning(
                        "Ошибка чтения файла лидов, создаю новый: %s",
                        e,
                    )
                    df = pd.DataFrame()
            else:
                df = pd.DataFrame()
            
            # Добавляем новый лид
            new_row = pd.DataFrame([lead_data])
            df = pd.concat([df, new_row], ignore_index=True)
            
            # Сохраняем в файл
            df.to_excel(self.leads_file_path, index=False)
            
            logger.info(
                "Лид сохранен в Excel: имя=%s, телефон=%s, потребность=%s",
                client_name,
                phone,
                need,
            )
            return True
        except Exception as e:
            logger.error(
                "Ошибка сохранения лида: %s",
                e,
                exc_info=True,
            )
            return False
