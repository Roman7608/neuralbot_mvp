"""
Утилиты для работы со временем.
"""

from datetime import datetime

from telegram_bot.services.working_hours_schedule import (
    get_response_contact_phrase,
    is_working_hours as _schedule_is_working_hours,
)


def is_working_hours(current_time: datetime | None = None) -> bool:
    """
    Проверяет, попадает ли текущее время в рабочие часы
    (начало дня … конец дня минус 15 минут) по расписанию working_hours.json.
    """
    return _schedule_is_working_hours(current_time)


def get_response_message_template(is_working: bool, department: str, current_time: datetime | None = None) -> str:
    """
    Шаблон «запрос передан … / когда перезвонят».
    is_working не влияет на текст — см. get_response_contact_phrase (локальное время сервера).
    """
    return get_response_contact_phrase(department, current_time)


def format_datetime(dt: datetime) -> str:
    """
    Форматирует datetime в строку.
    
    :param dt: Дата и время
    :return: Отформатированная строка
    """
    return dt.strftime("%Y-%m-%d %H:%M:%S")
