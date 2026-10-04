"""
Система стейтов для отслеживания состояния диалога с клиентом.

Реализует state machine согласно ИнструкцияRAG.txt:
- Отслеживание прогресса диалога
- Извлечение имени клиента
- Выявление потребностей
- Подтверждение потребностей
- Счетчики попыток
"""

import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class ConversationState(Enum):
    """Состояния диалога."""
    INITIAL = "initial"  # Начало разговора, приветствие
    # v2 + нерабочее время: после «Поняла…» — запрос имени для перезвона, затем подтверждение контакта
    AFTER_HOURS_NAME = "after_hours_name"
    ASKING_NAME = "asking_name"  # Запрос имени
    NAME_EXTRACTED = "name_extracted"  # Имя извлечено, ожидание потребности
    NEED_IDENTIFIED = "need_identified"  # Потребность выявлена, ожидание подтверждения
    NEED_CONFIRMED = "need_confirmed"  # Потребность подтверждена
    SERVICE_DATA_COLLECTION = "service_data_collection"  # Сбор данных для записи на сервис
    SERVICE_SLOT_SELECTION = "service_slot_selection"  # Выбор времени для записи
    SERVICE_BOOKED = "service_booked"  # Запись создана
    NEOPREDELENNOST = "neopredelennost"  # Неопределенность, перевод на администратора
    TRANSFERRING = "transferring"  # Перевод на отдел
    ENDED = "ended"  # Разговор завершен


class ClientNeed(Enum):
    """Потребности клиента."""
    USED_CARS = "used_cars"  # Б/у автомобили
    NEW_CARS_CHERY_TENET = "new_cars_chery_tenet"  # Новые Чери/Тенет (единый ОП новых авто)
    SERVICE = "service"  # Сервисное обслуживание
    SERVICE_COST = "service_cost"  # Узнать стоимость работ
    PARTS = "parts"  # Запчасти
    BODY_REPAIR = "body_repair"  # Кузовной ремонт
    ACCOUNTING = "accounting"  # Бухгалтерия
    OTHER = "other"  # Иные потребности


class ConversationStateMachine:
    """
    State machine для управления диалогом с клиентом.
    
    Отслеживает:
    - Состояние диалога
    - Имя клиента
    - Потребность клиента
    - Счетчики попыток
    - Данные для записи на сервис
    """
    
    def __init__(self, session_uuid: str):
        self.session_uuid = session_uuid
        self.state = ConversationState.INITIAL
        self.client_name: Optional[str] = None
        self.client_name_attempts = 0
        self.max_name_attempts = 2
        self.awaiting_new_brand_choice = False
        
        self.identified_need: Optional[ClientNeed] = None
        self.need_confirmation_attempts = 0
        self.max_need_attempts = 2
        self.need_confirmed = False
        # Голосовой сценарий: первая реплика после «Как Вас зовут» — только имя (кроме CRM-сайта).
        self.post_greeting_name_done: bool = False
        # Сколько раз проиграли меню отделов (12 legacy / 22 v2); повторного перечисления нет — дальше админ.
        self.department_prompts_shown: int = 0
        
        # Данные для записи на сервис
        self.service_data = {
            "fio": None,
            "fio_from_db": None,
            "phone": None,
            "phone_raw": None,
            "phone_from_db": None,
            "car_brand": None,
            "car_model": None,
            "car_year": None,
            "mileage": None,
            "desired_date": None,
            "work_list": None,
            "client_found_in_db": False,
            "car_confirmed": False,
            "car_attempts": 0,
            "mileage_work_confirmed": False,
            "mileage_work_attempts": 0,
            "date_time_confirmed": False,
            "date_time_attempts": 0,
            "until_week_end_slot": False,
            "proposed_date": None,
            "proposed_time": None,
            "proposed_post": None,
            "proposed_acceptor_id": None,
            "proposed_mechanic_name": None,
            "proposed_slot_start_iso": None,
            "service_1c_booking_id": None,
            "later_attempts": 0,
            "slot_unclear_attempts": 0,
            "to_v2": False,
            "to_v2_step": 0,
            "to_v2_substate": None,
            "gate5_played": False,
            "resume_after_price_step": 6,
            "price_block": {},
            "after_price_unclear": 0,
            "price_requested": False,
            "price_announced": False,
            "to_v2_step_empty_attempts": {},
        }
        self.menu14_booking_active = False
        self.menu14_unclear_attempts = 0
        
        logger.info(
            "Инициализирована state machine (session_uuid=%s, state=%s)",
            self.session_uuid,
            self.state.value,
        )
    
    def set_name(self, name: str) -> None:
        """Устанавливает имя клиента."""
        if name and name.strip():
            self.client_name = name.strip()
            self.client_name_attempts = 0  # Сброс счетчика при успехе
            logger.info(
                "Имя клиента установлено: %s (session_uuid=%s)",
                self.client_name,
                self.session_uuid,
            )
        else:
            self.client_name_attempts += 1
            logger.warning(
                "Попытка установить пустое имя (attempt=%d/%d, session_uuid=%s)",
                self.client_name_attempts,
                self.max_name_attempts,
                self.session_uuid,
            )
    
    def increment_name_attempt(self) -> None:
        """Увеличивает счетчик попыток извлечения имени."""
        self.client_name_attempts += 1
        logger.info(
            "Попытка извлечения имени: %d/%d (session_uuid=%s)",
            self.client_name_attempts,
            self.max_name_attempts,
            self.session_uuid,
        )
    
    def should_transfer_to_consultant_name(self) -> bool:
        """Проверяет, нужно ли переводить на администратора из-за неудачного извлечения имени."""
        return self.client_name_attempts >= self.max_name_attempts and self.client_name is None
    
    def set_identified_need(self, need: ClientNeed) -> None:
        """Устанавливает выявленную потребность."""
        self.identified_need = need
        self.need_confirmation_attempts = 0
        self.need_confirmed = False
        logger.info(
            "Потребность выявлена: %s (session_uuid=%s)",
            need.value,
            self.session_uuid,
        )
    
    def confirm_need(self) -> None:
        """Подтверждает потребность клиента."""
        self.need_confirmed = True
        self.need_confirmation_attempts = 0
        logger.info(
            "Потребность подтверждена: %s (session_uuid=%s)",
            self.identified_need.value if self.identified_need else None,
            self.session_uuid,
        )
    
    def reject_need(self) -> None:
        """Отклоняет потребность (клиент не согласен)."""
        self.need_confirmed = False
        self.identified_need = None
        self.need_confirmation_attempts = 0
        logger.info(
            "Потребность отклонена (session_uuid=%s)",
            self.session_uuid,
        )
    
    def increment_need_attempt(self) -> None:
        """Увеличивает счетчик попыток выявления потребности."""
        self.need_confirmation_attempts += 1
        logger.info(
            "Попытка выявления потребности: %d/%d (session_uuid=%s)",
            self.need_confirmation_attempts,
            self.max_need_attempts,
            self.session_uuid,
        )
    
    def should_transfer_to_consultant_need(self) -> bool:
        """Проверяет, нужно ли переводить на администратора из-за неудачного выявления потребности."""
        return (
            self.need_confirmation_attempts >= self.max_need_attempts
            and (self.identified_need is None or not self.need_confirmed)
        )
    
    def transition_to(self, new_state: ConversationState) -> None:
        """Переходит в новое состояние."""
        old_state = self.state
        self.state = new_state
        logger.info(
            "Переход состояния: %s -> %s (session_uuid=%s)",
            old_state.value,
            new_state.value,
            self.session_uuid,
        )
    
    def get_client_name_formatted(self) -> str:
        """Возвращает имя клиента в формате для обращения (ФИО или только имя)."""
        if not self.client_name:
            return "клиент"
        # Если имя содержит несколько слов (ФИО), используем все
        # Иначе используем как есть
        return self.client_name
    
    def update_service_data(self, **kwargs) -> None:
        """Обновляет данные для записи на сервис."""
        for key, value in kwargs.items():
            if key in self.service_data:
                self.service_data[key] = value
        logger.info(
            "Обновлены данные сервиса: %s (session_uuid=%s)",
            kwargs,
            self.session_uuid,
        )
    
    def reset_service_data(self) -> None:
        """Сбрасывает данные для записи на сервис."""
        self.service_data = {
            "fio": None,
            "phone": None,
            "phone_raw": None,
            "car_brand": None,
            "car_model": None,
            "car_year": None,
            "mileage": None,
            "desired_date": None,
            "work_list": None,
            "client_found_in_db": False,
            "car_confirmed": False,
            "until_week_end_slot": False,
            "proposed_date": None,
            "proposed_time": None,
            "proposed_post": None,
            "proposed_acceptor_id": None,
            "proposed_mechanic_name": None,
            "proposed_slot_start_iso": None,
            "service_1c_booking_id": None,
        }
        logger.info(
            "Данные сервиса сброшены (session_uuid=%s)",
            self.session_uuid,
        )
