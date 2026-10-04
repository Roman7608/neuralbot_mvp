"""
Конфигурация для Telegram-бота проекта Vikingi.
"""

import os
from pathlib import Path
from datetime import time

# Telegram Bot Token (из .env или значение по умолчанию)
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "8640408582:AAH5BhINkb3apvJPFfLKIAlv8IUpHBgCYBA")

# Публичный ник бота (без @); для клиентов: https://t.me/<ник>. Переопределение: TELEGRAM_BOT_USERNAME в .env
TELEGRAM_BOT_USERNAME: str = (os.environ.get("TELEGRAM_BOT_USERNAME") or "Vikingi_tlt_bot").lstrip("@")
TELEGRAM_BOT_T_ME_LINK: str = f"https://t.me/{TELEGRAM_BOT_USERNAME}"

# ID групп отделов (ОП новых, ОП с пробегом, Слесарный, Кузовной, Запчасти, Прочие)
# Vikingi_tlt — бот администратор в этих каналах
GROUP_CHERY_TENET: int = -1003890817210   # ОП Чери (Vikingi_tlt_Tenet)
GROUP_USED_CARS: int = -1003890144238     # ОП с пробегом (Vikingi_tlt_probeg)
GROUP_SERVICE: int = -1003692620760       # Слесарный цех (Vikingi_tlt_STO)
GROUP_BODY_REPAIR: int = -1003720904727   # Кузовной цех (Vikingi_tlt_kuzov)
GROUP_SPARES: int = -1003825758886        # Запчасти (Vikingi_tlt_spares)
GROUP_CONSULTANT: int = -1003831423823    # Прочие (Vikingi_tlt_Other)
GROUP_SECRETARY: int = -1003831423823     # Прочие (потребность не определена)

# Время работы дилерского центра
DEALER_CENTER_WORK_START: time = time(9, 0)  # Начало работы: 9:00
DEALER_CENTER_WORK_END: time = time(21, 0)  # Окончание работы: 21:00
DEALER_CENTER_WORK_END_MINUS: int = 30  # Минус 30 минут от окончания работы для определения диапазона

# Пути к файлам
WORKING_HOURS_PATH: Path = Path("working_hours.json")  # Рабочее расписание (по дням)
TELEGRAM_LEADS_PATH: Path = Path("telegram_leads.xlsx")  # Для статистики
TELEGRAM_FEEDBACK_PATH: Path = Path("telegram_feedback.xlsx")
TELEGRAM_PHOTOS_DIR: Path = Path("telegram_photos")

# Напоминания
REMINDER_HOURS_BEFORE: int = 24  # За 24 часа до записи
REMINDER_TIME_START: int = 8  # Начало окна отправки (8:00)
REMINDER_TIME_END: int = 21  # Конец окна отправки (21:00)
REMINDER_WINDOW_MINUTES: int = 15  # Окно отправки (15 минут)

# Оценка (время после визита для отправки опроса, в часах)
FEEDBACK_DELAY_HOURS: int = 2

# Интеграция с 1С Альфа 6.0
ICS_ALFA_CONNECTION_STRING: str = "..."
ICS_ALFA_USE_COM: bool = os.environ.get("ICS_ALFA_USE_COM", "false").lower() == "true"
ICS_ALFA_HTTP_URL: str = os.environ.get("ICS_ALFA_HTTP_URL", "http://192.168.0.11/Alfa6Test/hs")
ICS_ALFA_API_KEY: str = os.environ.get("ICS_ALFA_API_KEY", "")
# HTTP Basic auth для пользователя 1С (HTTP-сервис 1С Альфа возвращает 401 без логина/пароля).
ICS_ALFA_HTTP_USER: str = os.environ.get("ICS_ALFA_HTTP_USER", "").strip()
ICS_ALFA_HTTP_PASSWORD: str = os.environ.get("ICS_ALFA_HTTP_PASSWORD", "")
# post_id / acceptor_id в запросах 1С: код поста и код мастера-приёмщика из ответа /service/slots.
ICS_ALFA_SERVICE_POST_ID: str = os.environ.get("ICS_ALFA_SERVICE_POST_ID", "").strip()

# Временное использование Excel (на первом этапе)
USE_EXCEL_TEMP: bool = True  # Использовать slot.xlsx временно
SLOT_XLSX_PATH: Path = Path("slot.xlsx")  # Путь к slot.xlsx (только на первом этапе)

# Telegram-канал для новостей
TELEGRAM_CHANNEL_ID: str = ""  # Username канала (например, "@vikingi_auto") или ID (например, -1001234567890)
TELEGRAM_CHANNEL_TYPE: str = "channel"  # "channel" (публичный канал) или "group" (группа)

# Автоматическая публикация новостей
AUTO_PUBLISH_NEWS: bool = False  # Включить автоматическую публикацию
NEWS_PUBLISH_TIME: time = time(9, 0)  # Время публикации новостей (9:00)
