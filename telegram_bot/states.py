"""
Состояния FSM для Telegram-бота.
"""

from aiogram.fsm.state import State, StatesGroup


class BotStates(StatesGroup):
    """Состояния диалога с клиентом."""
    WAITING_BRAND = State()  # Ожидание выбора марки (Chery/Tenet), если зашли без deep link
    WAITING_BRAND_CHOICE = State()  # «Хочу новый автомобиль» — уточнение: Chery или Tenet
    WAITING_NEED = State()  # Ожидание потребности
    WAITING_NEED_CHOICE = State()  # Ожидание выбора отдела (если потребность не определена)
    WAITING_NEW_USED = State()  # Уточнение: новый авто или с пробегом
    # Опрос по машине для слесарного/кузовного цеха
    WAITING_CAR_BRAND_MODEL = State()
    WAITING_CAR_YEAR = State()
    WAITING_CAR_MILEAGE = State()
    WAITING_CAR_WISHES = State()
    # Запись на ТО (слесарный цех): выбор «записать» или «передать контакт»
    WAITING_SERVICE_CHOICE = State()
    WAITING_SERVICE_FIO = State()
    WAITING_SERVICE_PHONE = State()
    WAITING_SERVICE_CAR = State()
    WAITING_SERVICE_YEAR_MILEAGE = State()
    WAITING_SERVICE_WORK = State()
    WAITING_SERVICE_DATE = State()
    WAITING_SERVICE_SLOT_CONFIRM = State()   # Ожидание подтверждения предложенного слота
    WAITING_SERVICE_FINAL_CONFIRM = State()  # После «Вы записаны на...», ожидание (без возражений → лид)
    WAITING_FIO = State()  # Ожидание ФИО
    WAITING_PHONE = State()  # Ожидание телефона
    WAITING_PHONE_BUTTON = State()  # Ожидание нажатия кнопки телефона (если номер не прошел валидацию)
    WAITING_MORE_NEED = State()  # «Могу ли ещё помочь?» — ожидание ответа или новой потребности
