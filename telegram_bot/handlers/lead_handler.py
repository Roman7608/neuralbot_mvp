"""
Обработчик заявок от клиентов.
"""

import asyncio
import logging
from datetime import datetime, timedelta, date as date_type
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext

from telegram_bot.states import BotStates
from telegram_bot.services.need_detector import NeedDetector, ClientNeed
from telegram_bot.services.group_router import GroupRouter
from telegram_bot.services.ics_alfa_service import ICSAlfaService
from telegram_bot.utils.phone_normalizer import normalize_phone, is_valid_phone
from telegram_bot.utils.time_utils import is_working_hours, get_response_message_template, format_datetime
from telegram_bot.utils.date_parser import parse_date
from telegram_bot.services.working_hours_schedule import (
    get_working_hours_message,
    is_working_day,
)
from telegram_bot.services.service_booking_service import (
    find_nearest_slot,
    find_nearest_slot_1c,
    get_labor_and_cost,
    create_1c_booking,
    cancel_1c_booking,
    parse_time_preference,
    parse_later_hours,
    parse_explicit_time_hour,
    SLOT_DURATION_MINUTES,
    DEFAULT_LABOR_MINUTES,
)
from database import TelegramLeadsDB

logger = logging.getLogger(__name__)

router = Router()

need_detector = NeedDetector()
group_router = GroupRouter()
ics_service = ICSAlfaService()

# Марки, которые относятся к дилерству Викинги (новые авто)
OUR_BRAND_KEYWORDS = [
    "chery", "черри", "чери",
    "tiggo", "тигго",
    "tenet", "тенет",
]

# Марки, которые считаем «чужими» и ведём в отдел автомобилей с пробегом при запросе покупки
FOREIGN_BRAND_KEYWORDS = [
    "ford", "форд",
    "kia", "киа",
    "hyundai", "хендай", "хёндэ", "хендэ",
    "toyota", "тойота",
    "bmw",
    "mercedes", "мерседес",
    "audi", "ауди",
    "volkswagen", "vw", "фольксваген",
    "skoda", "шкода",
    "renault", "рено",
    "nissan", "ниссан",
    "mazda", "мазда",
    "honda", "хонда",
    "geely", "джили",
    "lifan", "лифан",
    "subaru", "сузуки", "suzuki",
]


def make_main_keyboard(brand: str | None = None) -> ReplyKeyboardMarkup:
    """
    Основные кнопки выбора отдела, которые должны почти всегда быть снизу экрана.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Записаться на ТО")],
            [KeyboardButton(text="ОП Tenet & Chery")],
            [KeyboardButton(text="Автомобили с пробегом")],
            [KeyboardButton(text="Слесарный цех")],
            [KeyboardButton(text="Кузовной цех")],
            [KeyboardButton(text="Запчасти")],
            [KeyboardButton(text="Прочие")],
            [KeyboardButton(text="Старт")],
        ],
        resize_keyboard=True,
    )


# Фраза-ответ на вопрос «какие автомобили/марки продают в Викинги»
DEALER_INFO_REPLY = (
    "Компания Викинги является официальным дилером марок Chery и Tenet. "
    "Мы предлагаем автомобили этих марок с официальной гарантией и профессиональным сервисным обслуживанием. "
    "Если Вас интересуют автомобили других марок, могу соединить Вас с Отделом продажи автомобилей с пробегом, там Вам обязательно помогут."
)

# Ключевые слова для вопроса о часах работы
WORKING_HOURS_KEYWORDS = [
    "часы работы", "когда работаете", "работаете", "режим работы",
    "открыты", "до скольки", "во сколько", "работает дилер",
]

# Вопрос о доступных отделах — показываем меню выбора
DEPARTMENT_INQUIRY_KEYWORDS = [
    "какие отделы", "с какими отделами", "какие отдел", "соедин", "соединить",
    "почему спрашиваешь", "почему спрашиваешь марку", "для чего", "зачем марку",
    "что ты можешь", "чем помочь", "чем можете помочь",
]

# Ключевые слова для вопроса об ассортименте
DEALER_INFO_KEYWORDS = [
    "какие автомобили", "какие марки", "какие машины", "что продают", "что продаёте",
    "какие авто", "дилер каких", "какие марки продают", "автомобили продают",
    "викинги продают", "у викинги", "в викинги",
]

# «Нет» / отказ от дополнительной помощи
MORE_HELP_NO_KEYWORDS = ["нет", "не надо", "не нужно", "спасибо всё", "спасибо все", "всё", "все", "достаточно", "хватит", "пока нет"]

# Таймаут неактивности (сек): контрольный вопрос через 30 сек, закрытие ещё через 30 сек
INACTIVITY_TIMEOUT_SEC = 30

# Единый таймер бездействия для всех этапов: user_id -> asyncio.Task
_generic_inactivity_tasks: dict[int, asyncio.Task] = {}


@router.message(BotStates.WAITING_NEED)
async def handle_need(message: Message, state: FSMContext):
    """
    Обработка потребности клиента (первая попытка).
    """
    # Пользователь ответил — снимаем таймер бездействия
    cancel_generic_inactivity(message.from_user.id)

    need_text = message.text
    text_lower = (need_text or "").strip().lower()

    # Явное нажатие «ОП Tenet & Chery» (или устаревшая подпись «ОП Jetour») — единый ОП новых авто
    if text_lower in ("оп tenet & chery", "оп jetour"):
        await state.update_data(
            brand="chery_tenet", need=ClientNeed.CHERY_TENET.value, need_text=need_text
        )
        data = await state.get_data()
        if data.get("fio") and data.get("phone"):
            await process_phone(message, state, data.get("phone"), phone_alt=data.get("phone_alt"))
        else:
            await state.set_state(BotStates.WAITING_FIO)
            await message.answer(
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                reply_markup=ReplyKeyboardRemove(),
            )
            await schedule_generic_inactivity(message, state)
        return

    # Вопрос о часах работы — отвечаем по расписанию
    if any(kw in text_lower for kw in WORKING_HOURS_KEYWORDS):
        base = date_type.today()
        if "завтра" in text_lower:
            d = base + timedelta(days=1)
        elif "послезавтра" in text_lower or "после завтра" in text_lower:
            d = base + timedelta(days=2)
        elif "сегодня" in text_lower:
            d = base
        else:
            parsed = parse_date(need_text or "", base)
            d = parsed if parsed else base
        msg = get_working_hours_message(d)
        data = await state.get_data()
        await message.answer(msg, reply_markup=make_main_keyboard(data.get("brand") or "chery_tenet"))
        return

    # Вопрос «какие автомобили/марки продают» — даём информационный ответ, остаёмся в WAITING_NEED
    if any(kw in text_lower for kw in DEALER_INFO_KEYWORDS):
        data = await state.get_data()
        brand = data.get("brand") or "chery_tenet"
        await message.answer(DEALER_INFO_REPLY, reply_markup=make_main_keyboard(brand))
        return

    # Только нажатие кнопки «Записаться на ТО» — запуск цикла записи на ТО
    if _is_service_book_button(text_lower):
        data = await state.get_data()
        brand = data.get("brand") or "chery_tenet"
        await state.update_data(need=ClientNeed.SERVICE.value, need_text=need_text)
        has_fio_phone = data.get("fio") and data.get("phone")
        if has_fio_phone:
            await state.update_data(service_fio=data.get("fio"), service_phone=data.get("phone"))
            await state.set_state(BotStates.WAITING_SERVICE_CAR)
            await message.answer(
                "Назовите марку и модель Вашего автомобиля.",
                reply_markup=ReplyKeyboardRemove(),
            )
        else:
            await state.set_state(BotStates.WAITING_SERVICE_FIO)
            await message.answer(
                "Назовите ваши фамилию, имя и отчество.",
                reply_markup=make_main_keyboard(brand),
            )
        await schedule_generic_inactivity(message, state)
        return

    # Текст про ТО (не кнопка) — подсказка нажать кнопку меню или слесарного цеха
    if _is_service_to_text(text_lower):
        data = await state.get_data()
        await message.answer(
            SERVICE_TO_REDIRECT_MESSAGE,
            reply_markup=make_main_keyboard(data.get("brand") or "chery_tenet"),
        )
        return

    # Прямое нажатие кнопок отделов — сразу переход в соответствующий сценарий
    if text_lower == "слесарный цех":
        data = await state.get_data()
        brand = data.get("brand") or "chery_tenet"
        await state.update_data(need=ClientNeed.SERVICE.value, need_text=need_text)
        await state.set_state(BotStates.WAITING_SERVICE_CHOICE)
        await message.answer(
            "Я могу записать Вас на ТО либо передать Ваш контакт ассистенту Слесарного цеха.",
            reply_markup=make_main_keyboard(brand),
        )
        await schedule_generic_inactivity(message, state)
        return
    if text_lower == "кузовной цех":
        data = await state.get_data()
        brand = data.get("brand") or "chery_tenet"
        await state.update_data(
            need=ClientNeed.BODY_REPAIR.value,
            need_text=need_text,
            car_brand=None,
            car_model=None,
            car_year=None,
            car_mileage=None,
            work_wishes=None,
        )
        await state.set_state(BotStates.WAITING_CAR_BRAND_MODEL)
        await message.answer(
            "Укажите, пожалуйста, марку и модель автомобиля (например: Toyota Camry).",
            reply_markup=make_main_keyboard(brand),
        )
        await schedule_generic_inactivity(message, state)
        return
    if text_lower == "автомобили с пробегом":
        data = await state.get_data()
        await state.update_data(need=ClientNeed.USED_CARS.value, need_text=need_text)
        if data.get("fio") and data.get("phone"):
            await process_phone(message, state, data.get("phone"), phone_alt=data.get("phone_alt"))
        else:
            await state.set_state(BotStates.WAITING_FIO)
            await message.answer(
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                reply_markup=ReplyKeyboardRemove(),
            )
            await schedule_generic_inactivity(message, state)
        return
    if text_lower == "запчасти":
        data = await state.get_data()
        await state.update_data(need=ClientNeed.SPARES.value, need_text=need_text)
        if data.get("fio") and data.get("phone"):
            await process_phone(message, state, data.get("phone"), phone_alt=data.get("phone_alt"))
        else:
            await state.set_state(BotStates.WAITING_FIO)
            await message.answer(
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                reply_markup=ReplyKeyboardRemove(),
            )
            await schedule_generic_inactivity(message, state)
        return
    if text_lower == "прочие":
        data = await state.get_data()
        await state.update_data(need=ClientNeed.SECRETARY.value, need_text=need_text)
        if data.get("fio") and data.get("phone"):
            await process_phone(message, state, data.get("phone"), phone_alt=data.get("phone_alt"))
        else:
            await state.set_state(BotStates.WAITING_FIO)
            await message.answer(
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                reply_markup=ReplyKeyboardRemove(),
            )
            await schedule_generic_inactivity(message, state)
        return

    # При любом упоминании (не кнопка) — показываем меню выбора, не предполагаем намерение
    data = await state.get_data()
    await state.update_data(need_text=need_text, need_attempts=1)
    await state.set_state(BotStates.WAITING_NEED_CHOICE)
    await message.answer(
        need_detector.get_choice_message(brand=data.get("brand") or "chery_tenet"),
        reply_markup=make_main_keyboard(data.get("brand") or "chery_tenet"),
    )
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_NEED_CHOICE)
async def handle_need_choice(message: Message, state: FSMContext):
    """
    Обработка выбора отдела (если потребность не определена с первого раза).
    """
    cancel_generic_inactivity(message.from_user.id)
    choice = (message.text or "").strip().lower()
    need = None

    data = await state.get_data()
    brand = data.get("brand") or "chery_tenet"

    # Текст про ТО (не кнопка) — подсказка нажать кнопку
    if _is_service_to_text(choice):
        await message.answer(
            SERVICE_TO_REDIRECT_MESSAGE,
            reply_markup=make_main_keyboard(brand),
        )
        return

    # Если клиент явно пишет про автомобиль чужой марки в свободной форме
    has_foreign_brand = any(b in choice for b in FOREIGN_BRAND_KEYWORDS)
    has_used_markers = any(m in choice for m in ["пробег", "б/у", "бэу", "бу"])
    if has_foreign_brand and not has_used_markers:
        need = ClientNeed.USED_CARS
        await message.answer(
            "Я могу перевести Вас на отдел автомобили с пробегом. "
            "Там Вам постараются помочь с Вашим запросом.",
            reply_markup=make_main_keyboard(brand),
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
        elif any(m in choice for m in ["пробег", "б/у", "бэу", "бу", "подержан", "с пробегом", "отдел с пробегом", "подержаные", "подержанные", "не новые", "неновые"]):
            need = ClientNeed.USED_CARS
        elif any(m in choice for m in ["слесар", "сто", "сервис", "приемка слесар", "техобслуж"]) or _is_service_book_button(choice):
            need = ClientNeed.SERVICE
        elif any(m in choice for m in ["кузов", "приемка кузов", "кузовной", "покраск", "покрасить", "бампер", "крыло", "капот", "вмятин", "царапин", "дтп"]):
            need = ClientNeed.BODY_REPAIR
        elif "запчаст" in choice or "детал" in choice or "запасных частей" in choice or "запасных" in choice:
            need = ClientNeed.SPARES
        elif "прочи" in choice or "секретар" in choice or "друго" in choice:
            need = ClientNeed.SECRETARY
        else:
            need = ClientNeed.SECRETARY
    need_text = data.get("need_text", message.text)

    await state.update_data(need=need.value, need_text=need_text, need_attempts=2)
    if need == ClientNeed.SERVICE:
        has_fio_phone = data.get("fio") and data.get("phone")
        if _is_service_book_button((message.text or "").strip()):
            if has_fio_phone:
                await state.update_data(service_fio=data.get("fio"), service_phone=data.get("phone"))
                await state.set_state(BotStates.WAITING_SERVICE_CAR)
                await message.answer(
                    "Назовите марку и модель Вашего автомобиля.",
                    reply_markup=ReplyKeyboardRemove(),
                )
            else:
                await state.set_state(BotStates.WAITING_SERVICE_FIO)
                await message.answer(
                    "Назовите ваши фамилию, имя и отчество.",
                    reply_markup=make_main_keyboard(brand),
                )
            await schedule_generic_inactivity(message, state)
        else:
            await state.set_state(BotStates.WAITING_SERVICE_CHOICE)
            await message.answer(
                "Я могу записать Вас на ТО либо передать Ваш контакт ассистенту Слесарного цеха.",
                reply_markup=make_main_keyboard(brand),
            )
            await schedule_generic_inactivity(message, state)
    elif need == ClientNeed.BODY_REPAIR:
        await state.set_state(BotStates.WAITING_CAR_BRAND_MODEL)
        await message.answer(
            "Укажите, пожалуйста, марку и модель автомобиля (например: Toyota Camry).",
            reply_markup=ReplyKeyboardRemove(),
        )
        await schedule_generic_inactivity(message, state)
    else:
        if data.get("fio") and data.get("phone"):
            await process_phone(message, state, data.get("phone"), phone_alt=data.get("phone_alt"))
        else:
            await state.set_state(BotStates.WAITING_FIO)
            await message.answer(
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                reply_markup=ReplyKeyboardRemove(),
            )
            await schedule_generic_inactivity(message, state)


OTHER_BRAND_RESPONSE = "Я передал Ваши данные в Отдел продаж, Вам перезвонят в ближайшие 15 минут."

# «Записать» vs «передать контакт» для слесарного цеха
SERVICE_BOOK_KEYWORDS = ["запис", "на то", " на то", "то ", "да", "хочу записаться", "запишите", "запиши"]
# Текст кнопки «Записаться на ТО» — только нажатие кнопки запускает сценарий записи
SERVICE_BOOK_BUTTON_TEXT = "записаться на то"
# Ключевые слова текста про ТО (не кнопка) — показываем просьбу нажать кнопку меню или слесарного цеха
SERVICE_TO_TEXT_KEYWORDS = ["пройти то", "записаться на то", "техническое обслуживание", "техобслуживан"]
SERVICE_TO_REDIRECT_MESSAGE = (
    'Если Вы хотите записаться на ТО, нажмите кнопку "Записаться на ТО" для автоматической записи '
    'или "Слесарный цех", чтобы с Вами созвонился диспетчер слесарного цеха.'
)


def _is_service_book_button(text: str) -> bool:
    """Только точное совпадение с текстом кнопки «Записаться на ТО» (нажатие кнопки)."""
    t = (text or "").strip().lower()
    return t == SERVICE_BOOK_BUTTON_TEXT


def _is_service_to_text(text: str) -> bool:
    """Пользователь написал про ТО текстом (не нажал кнопку) — нужна подсказка про кнопки."""
    t = (text or "").strip().lower()
    if _is_service_book_button(text):
        return False
    return any(kw in t for kw in SERVICE_TO_TEXT_KEYWORDS)
SERVICE_PASS_CONTACT_KEYWORDS = ["передай", "передать", "контакт", "свяжитесь", "перезвонит", "позвонит"]
SERVICE_COST_KEYWORDS = ["сколько стоит", "какая цена", "какая стоимость", "во сколько", "цена", "стоимость"]


def _is_service_cost_question(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(kw in t for kw in SERVICE_COST_KEYWORDS)


@router.message(BotStates.WAITING_BRAND_CHOICE)
async def handle_brand_choice(message: Message, state: FSMContext):
    """
    «Хочу купить новый автомобиль» — выбор марки: Chery, Tenet или другая.
    """
    cancel_generic_inactivity(message.from_user.id)
    text_raw = (message.text or "").strip()
    text = text_raw.lower()
    data = await state.get_data()
    brand = data.get("brand") or "chery_tenet"
    need_text = data.get("need_text", text_raw)

    need = None
    custom_response = None

    if "другая" in text and "марк" in text:
        need = ClientNeed.USED_CARS
        custom_response = OTHER_BRAND_RESPONSE
    elif any(b in text for b in FOREIGN_BRAND_KEYWORDS):
        need = ClientNeed.USED_CARS
        custom_response = OTHER_BRAND_RESPONSE
    elif "chery" in text or "чери" in text or "черри" in text:
        need = ClientNeed.CHERY_TENET
        brand = "chery_tenet"
    elif "tenet" in text or "тенет" in text:
        need = ClientNeed.CHERY_TENET
        brand = "chery_tenet"
    elif "jetour" in text or "джетур" in text or "джитур" in text:
        need = ClientNeed.CHERY_TENET
        brand = "chery_tenet"
    else:
        need = ClientNeed.CHERY_TENET
        brand = "chery_tenet"

    await state.update_data(
        need=need.value,
        need_text=need_text,
        brand=brand,
        custom_response=custom_response if custom_response else None,
    )

    if data.get("fio") and data.get("phone"):
        await process_phone(
            message,
            state,
            data.get("phone"),
            phone_alt=data.get("phone_alt"),
            custom_response=custom_response,
        )
        return

    await state.set_state(BotStates.WAITING_FIO)
    await message.answer(
        "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
        reply_markup=ReplyKeyboardRemove(),
    )
    await schedule_generic_inactivity(message, state)


BODY_REPAIR_REDIRECT_KEYWORDS = [
    "кузов", "кузовной", "покраска", "покрасить", "бампер", "крыло", "капот",
    "вмятина", "вмятины", "царапина", "царапину", "окрасить",
    "отрихтовать", "заменить",
]


@router.message(BotStates.WAITING_SERVICE_CHOICE)
async def handle_service_choice(message: Message, state: FSMContext):
    """
    «Я могу записать Вас на ТО либо передать Ваш контакт ассистенту Слесарного цеха».
    «Записать» -> WAITING_SERVICE_FIO, «передать контакт» -> WAITING_FIO.
    Если клиент уточняет кузовной цех — перенаправляем в BODY_REPAIR.
    """
    cancel_generic_inactivity(message.from_user.id)
    text_raw = (message.text or "").strip()
    text = text_raw.lower()
    data = await state.get_data()
    brand = data.get("brand") or "chery_tenet"

    if any(kw in text for kw in BODY_REPAIR_REDIRECT_KEYWORDS):
        await state.update_data(need=ClientNeed.BODY_REPAIR.value, need_text=data.get("need_text") or text_raw)
        await state.update_data(car_brand=None, car_model=None, car_year=None, car_mileage=None, work_wishes=None)
        await state.set_state(BotStates.WAITING_CAR_BRAND_MODEL)
        await message.answer(
            "Укажите, пожалуйста, марку и модель автомобиля (например: Toyota Camry).",
            reply_markup=make_main_keyboard(brand),
        )
        await schedule_generic_inactivity(message, state)
        return

    wants_book = any(kw in text for kw in SERVICE_BOOK_KEYWORDS)
    wants_pass = any(kw in text for kw in SERVICE_PASS_CONTACT_KEYWORDS)
    has_fio_phone = data.get("fio") and data.get("phone")

    # Только нажатие кнопки «Записаться на ТО» запускает цикл записи
    if _is_service_book_button(text_raw):
        if has_fio_phone:
            await state.update_data(service_fio=data.get("fio"), service_phone=data.get("phone"))
            await state.set_state(BotStates.WAITING_SERVICE_CAR)
            await message.answer(
                "Назовите марку и модель Вашего автомобиля.",
                reply_markup=ReplyKeyboardRemove(),
            )
        else:
            await state.set_state(BotStates.WAITING_SERVICE_FIO)
            await message.answer(
                "Назовите ваши фамилию, имя и отчество.",
                reply_markup=ReplyKeyboardRemove(),
            )
        await schedule_generic_inactivity(message, state)
    # Текст про ТО (не кнопка) — подсказка нажать кнопку
    elif wants_book or _is_service_to_text(text_raw):
        await message.answer(
            SERVICE_TO_REDIRECT_MESSAGE,
            reply_markup=make_main_keyboard(brand),
        )
    elif wants_pass:
        if has_fio_phone:
            await process_phone(message, state, data.get("phone"), phone_alt=data.get("phone_alt"))
        else:
            await state.set_state(BotStates.WAITING_FIO)
            await message.answer(
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                reply_markup=make_main_keyboard(brand),
            )
            await schedule_generic_inactivity(message, state)
    else:
        await message.answer(
            "Я могу записать Вас на ТО либо передать Ваш контакт ассистенту Слесарного цеха.",
            reply_markup=make_main_keyboard(brand),
        )


@router.message(BotStates.WAITING_SERVICE_FIO)
async def handle_service_fio(message: Message, state: FSMContext):
    """ФИО для записи на ТО."""
    cancel_generic_inactivity(message.from_user.id)
    data = await state.get_data()
    if data.get("fio") and data.get("phone"):
        await state.update_data(service_fio=data.get("fio"), service_phone=data.get("phone"))
        await state.set_state(BotStates.WAITING_SERVICE_CAR)
        await message.answer(
            "Назовите марку и модель Вашего автомобиля.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await schedule_generic_inactivity(message, state)
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, назовите ваши фамилию, имя и отчество.")
        return
    await state.update_data(service_fio=text)
    if data.get("phone"):
        await state.update_data(service_phone=data.get("phone"))
        await state.set_state(BotStates.WAITING_SERVICE_CAR)
        await message.answer(
            "Назовите марку и модель Вашего автомобиля.",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await state.set_state(BotStates.WAITING_SERVICE_PHONE)
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Передать номер телефона", request_contact=True)],
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await message.answer(
            "Назовите номер телефона для связи или нажмите кнопку «Передать номер телефона».",
            reply_markup=keyboard,
        )
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_SERVICE_PHONE, F.contact)
async def handle_service_phone_contact(message: Message, state: FSMContext):
    """Телефон через кнопку для записи на ТО."""
    cancel_generic_inactivity(message.from_user.id)
    contact = message.contact
    phone = contact.phone_number
    if phone and not phone.startswith("+"):
        phone = f"+{phone}"
    await state.update_data(service_phone=phone)
    await state.set_state(BotStates.WAITING_SERVICE_CAR)
    await message.answer(
        "Назовите марку и модель Вашего автомобиля.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_SERVICE_PHONE)
async def handle_service_phone_text(message: Message, state: FSMContext):
    """Телефон текстом для записи на ТО."""
    cancel_generic_inactivity(message.from_user.id)
    raw = (message.text or "").strip()
    phone = normalize_phone(raw)
    if not phone:
        attempts = int((await state.get_data()).get("service_invalid_phone_attempts", 0)) + 1
        await state.update_data(service_invalid_phone_attempts=attempts)
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Передать номер телефона", request_contact=True)],
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        if attempts == 1:
            msg = "Пожалуйста, укажите корректный номер телефона."
        else:
            msg = 'Нажмите кнопку «Передать номер телефона» для подтверждения номера телефона.'
        await message.answer(msg, reply_markup=keyboard)
        return
    await state.update_data(service_phone=phone, service_invalid_phone_attempts=0)
    await state.set_state(BotStates.WAITING_SERVICE_CAR)
    await message.answer(
        "Назовите марку и модель Вашего автомобиля.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_SERVICE_CAR)
async def handle_service_car(message: Message, state: FSMContext):
    """Марка и модель авто для записи на ТО."""
    cancel_generic_inactivity(message.from_user.id)
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, назовите марку и модель автомобиля.")
        return
    parts = text.split(maxsplit=1)
    car_brand = parts[0] if parts else text
    car_model = parts[1] if len(parts) > 1 else ""
    await state.update_data(service_car_brand=car_brand, service_car_model=car_model)
    await state.set_state(BotStates.WAITING_SERVICE_YEAR_MILEAGE)
    await message.answer("Назовите год выпуска и пробег автомобиля.")
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_SERVICE_YEAR_MILEAGE)
async def handle_service_year_mileage(message: Message, state: FSMContext):
    """Год и пробег для записи на ТО."""
    cancel_generic_inactivity(message.from_user.id)
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, назовите год выпуска и пробег.")
        return
    await state.update_data(service_car_year_mileage=text)
    await state.set_state(BotStates.WAITING_SERVICE_WORK)
    await message.answer(
        "Я могу записать Вас на ТО или простые механические операции. "
        "Какие работы Вам нужно произвести на автомобиле?"
    )
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_SERVICE_WORK)
async def handle_service_work(message: Message, state: FSMContext):
    """Описание работ. Проверяем вопрос о стоимости."""
    cancel_generic_inactivity(message.from_user.id)
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, опишите необходимые работы.")
        return

    if _is_service_cost_question(text):
        labor_min, cost = get_labor_and_cost("")
        cost_msg = f"Ориентировочная трудоёмкость: {DEFAULT_LABOR_MINUTES} минут. Стоимость уточняется при записи." if cost == 0 else f"Ориентировочно: {cost:.0f} руб."
        await message.answer(cost_msg)
        return

    await state.update_data(service_work_wishes=text)
    await state.set_state(BotStates.WAITING_SERVICE_DATE)
    await message.answer("В какой день Вы хотели бы сдать автомобиль на обслуживание?")
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_SERVICE_DATE)
async def handle_service_date(message: Message, state: FSMContext):
    """Дата для записи. Парсим, ищем слот, предлагаем."""
    cancel_generic_inactivity(message.from_user.id)
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, назовите желаемую дату.")
        return

    if _is_service_cost_question(text):
        cost_msg = "Ориентировочная трудоёмкость: 2 часа. Стоимость уточняется при записи."
        await message.answer(cost_msg)
        return

    target = parse_date(text, date_type.today())
    if not target:
        await message.answer(
            "Не удалось понять дату. Напишите, например: завтра, послезавтра, 15 февраля или 15.02."
        )
        return

    if not is_working_day(target):
        await message.answer(
            f"{target.strftime('%d.%m.%Y')} — выходной день. Назовите другую дату."
        )
        return

    time_pref = parse_time_preference(text)
    explicit_hour = parse_explicit_time_hour(text)
    after_dt = None
    if explicit_hour:
        hour, _ = explicit_hour
        after_dt = datetime.combine(target, datetime.min.time()).replace(hour=hour, minute=0, second=0)
        time_pref = None
    await state.update_data(service_preferred_date=target.isoformat(), service_time_preference=time_pref)

    data = await state.get_data()
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
        await message.answer(
            "К сожалению, на указанную дату и ближайшие дни свободных мест нет. "
            "Назовите другой день, я постараюсь подобрать для Вас время."
        )
        return

    slot_str = slot_info.start.strftime("%d.%m в %H:%M")
    await state.update_data(
        service_proposed_slot_start=slot_info.start.isoformat(),
        service_proposed_slot_end=slot_info.end.isoformat(),
        service_proposed_post_id=slot_info.post_id or "",
        service_proposed_acceptor_id=slot_info.acceptor_id or "",
        service_proposed_duration_min=slot_duration,
        service_1c_booking_id=None,
    )

    if in_priority:
        msg = f"Могу предложить Вам {slot_str}. Вам подходит?"
    else:
        msg = (
            "В ближайшие 3 дня свободных мест нет. "
            f"Ближайший свободный слот: {slot_str}. Вам подходит?"
        )
    await state.set_state(BotStates.WAITING_SERVICE_SLOT_CONFIRM)
    await message.answer(msg)
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_SERVICE_SLOT_CONFIRM)
async def handle_service_slot_confirm(message: Message, state: FSMContext):
    """Подтверждение предложенного слота."""
    cancel_generic_inactivity(message.from_user.id)
    text = (message.text or "").strip().lower()
    data = await state.get_data()

    if _is_service_cost_question(text):
        await message.answer("Ориентировочная трудоёмкость: 2 часа. Стоимость уточняется при записи.")
        return

    confirm_words = ["да", "подходит", "давайте", "ок", "окей", "согласен", "хорошо", "давай"]
    reject_later_words = ["позже", "попозже", "позже хочу", "на позже"]
    reject_preference_words = ["после обеда", "послеобед", "утром", "вечером", "до обеда", "в обед"]
    reject_other_day_words = ["другой день", "передумал"]

    # Сначала проверяем запрос другого времени (иначе «после обеда» срабатывает как «да» из-за «обед-да»)
    wants_different_time = (
        any(w in text for w in reject_later_words)
        or any(w in text for w in reject_preference_words)
        or any(w in text for w in ["нет", "не подходит", "другое время"])
        or parse_explicit_time_hour(text) is not None
    )
    if wants_different_time:
        slot_start_str = data.get("service_proposed_slot_start")
        preferred_date_str = data.get("service_preferred_date")
        if slot_start_str and preferred_date_str:
            from datetime import datetime as dt_class
            last_proposed = dt_class.fromisoformat(slot_start_str)
            preferred_date = dt_class.fromisoformat(preferred_date_str).date() if preferred_date_str else last_proposed.date()
            work_wishes = data.get("service_work_wishes", "")
            labor_min, _ = get_labor_and_cost(work_wishes)
            slot_duration = labor_min + 30
            time_pref = data.get("service_time_preference")
            after_dt = None
            explicit = parse_explicit_time_hour(text)
            if explicit:
                hour, _ = explicit
                after_dt = datetime.combine(preferred_date, datetime.min.time()).replace(hour=hour, minute=0, second=0)
                time_pref = None
            elif any(w in text for w in reject_preference_words):
                time_pref = parse_time_preference(text) or time_pref
                after_dt = None
            elif any(w in text for w in reject_later_words):
                hours = parse_later_hours(text)
                add_hours = hours if hours is not None else 2
                after_dt = last_proposed + timedelta(hours=add_hours)
                time_pref = None
            elif any(w in text for w in ["нет", "не подходит", "другое время"]):
                if any(w in text for w in reject_preference_words):
                    time_pref = parse_time_preference(text) or time_pref
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
                await state.update_data(
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
                await message.answer(msg)
                await schedule_generic_inactivity(message, state)
                return
            await state.set_state(BotStates.WAITING_SERVICE_DATE)
            await message.answer(
                "В этот день в указанное время свободных мест нет. Назовите другой день, я постараюсь подобрать для Вас время."
            )
            await schedule_generic_inactivity(message, state)
            return
        await state.set_state(BotStates.WAITING_SERVICE_DATE)
        await message.answer("Назовите другой день, я постараюсь подобрать для Вас время.")
        await schedule_generic_inactivity(message, state)
        return

    if any(w in text for w in reject_other_day_words):
        await state.set_state(BotStates.WAITING_SERVICE_DATE)
        await message.answer("Назовите другой день, я постараюсь подобрать для Вас время.")
        await schedule_generic_inactivity(message, state)
        return

    if any(w in text for w in confirm_words):
        from datetime import datetime as dt_class
        slot_start_str = data.get("service_proposed_slot_start")
        slot_start = dt_class.fromisoformat(slot_start_str) if slot_start_str else None
        if not slot_start:
            await message.answer("Произошла ошибка. Назовите другой день, я постараюсь подобрать время.")
            await state.set_state(BotStates.WAITING_SERVICE_DATE)
            return

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
        slot_str = slot_start.strftime("%d.%m в %H-%M")
        fio = data.get("service_fio", "")
        await state.update_data(
            service_1c_booking_id=booking_id,
            service_confirmed_slot_start=slot_start_str,
        )
        department = group_router.get_department_name(ClientNeed.SERVICE)
        group_id = group_router.get_group_id(ClientNeed.SERVICE)
        need_text = (
            f"Запись на ТО: {data.get('service_car_brand', '')} {data.get('service_car_model', '')}, "
            f"дата {slot_start_str}, работы: {data.get('service_work_wishes', '')}"
        )
        lead_msg = group_router.format_lead_message(
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
        try:
            await message.bot.send_message(chat_id=group_id, text=lead_msg)
            logger.info(f"Лид записи на ТО отправлен в группу {group_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки лида в группу: {e}")
        lead_id = None
        try:
            lead_id = TelegramLeadsDB.save_lead(
                telegram_user_id=message.from_user.id,
                telegram_username=message.from_user.username,
                client_fio=fio,
                client_phone=data.get("service_phone", ""),
                need_type=ClientNeed.SERVICE.value,
                need_text=need_text,
                department=department,
                group_id=group_id,
                working_hours=is_working_hours(datetime.now()),
                response_message=get_response_message_template(True, department),
                car_brand=data.get("service_car_brand"),
                car_model=data.get("service_car_model"),
                car_year=data.get("service_car_year_mileage"),
                car_mileage=None,
                work_wishes=data.get("service_work_wishes"),
            )
            logger.info(f"Лид записи на ТО сохранён: ID={lead_id}")
        except Exception as e:
            logger.error(f"Ошибка сохранения лида: {e}")
        await message.answer(f"{fio}, Вы записаны на {slot_str}.")
        await message.answer(
            "Могу ли я ещё чем-нибудь Вам помочь?",
            reply_markup=make_main_keyboard(data.get("brand") or "chery_tenet"),
        )
        await state.update_data(
            fio=fio,
            phone=data.get("service_phone"),
            last_lead_id=lead_id,
            last_lead_group_id=group_id,
            last_need=ClientNeed.SERVICE.value,
            more_need_attempts=0,
        )
        await state.set_state(BotStates.WAITING_MORE_NEED)
        await schedule_generic_inactivity(message, state)
        return

    if any(w in text for w in reject_other_day_words):
        await state.set_state(BotStates.WAITING_SERVICE_DATE)
        await message.answer("Назовите другой день, я постараюсь подобрать для Вас время.")
        await schedule_generic_inactivity(message, state)
        return

    # Не подтверждение и не отказ — подсказываем
    await message.answer(
        "Напишите «да» или «подходит», если время устраивает, либо «позже» / «после обеда» / «другой день» для выбора другого времени."
    )


@router.message(BotStates.WAITING_SERVICE_FINAL_CONFIRM)
async def handle_service_final_confirm(message: Message, state: FSMContext):
    """
    После «Вы записаны на...»: без возражений -> лид в группу.
    Если передумал -> аннулируем 1С, предлагаем другое время.
    """
    cancel_generic_inactivity(message.from_user.id)
    cancel_generic_inactivity(message.from_user.id)
    text = (message.text or "").strip().lower()
    data = await state.get_data()
    brand = data.get("brand") or "chery_tenet"

    reject_words = ["передумал", "нет", "другой день", "другое время", "отмена", "отменить"]
    if any(w in text for w in reject_words):
        booking_id = data.get("service_1c_booking_id")
        if booking_id:
            await cancel_1c_booking(booking_id)
            await state.update_data(service_1c_booking_id=None)
        await state.set_state(BotStates.WAITING_SERVICE_DATE)
        await message.answer("Назовите другой день, я постараюсь подобрать для Вас время.")
        return

    if _is_service_cost_question(text):
        await message.answer("Стоимость уточняется при приёме автомобиля.")
        return

    if data.get("service_lead_sent"):
        await message.answer("Могу ли я ещё чем-нибудь Вам помочь?")
        return

    # Нет возражений — считаем подтверждённым, отправляем лид в группу
    fio = data.get("service_fio", "")
    phone = data.get("service_phone", "")
    need_text = (
        f"Запись на ТО: {data.get('service_car_brand', '')} {data.get('service_car_model', '')}, "
        f"дата {data.get('service_confirmed_slot_start', '')}, работы: {data.get('service_work_wishes', '')}"
    )
    department = group_router.get_department_name(ClientNeed.SERVICE)
    group_id = group_router.get_group_id(ClientNeed.SERVICE)
    lead_msg = group_router.format_lead_message(
        fio=fio,
        phone=phone,
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
    try:
        await message.bot.send_message(chat_id=group_id, text=lead_msg)
        logger.info(f"Лид записи на ТО отправлен в группу {group_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки лида в группу: {e}")

    lead_id = None
    try:
        is_working = is_working_hours(datetime.now())
        response_template = get_response_message_template(is_working, department)
        lead_id = TelegramLeadsDB.save_lead(
            telegram_user_id=message.from_user.id,
            telegram_username=message.from_user.username,
            client_fio=fio,
            client_phone=phone,
            need_type=ClientNeed.SERVICE.value,
            need_text=need_text,
            department=department,
            group_id=group_id,
            working_hours=is_working,
            response_message=response_template,
            car_brand=data.get("service_car_brand"),
            car_model=data.get("service_car_model"),
            car_year=data.get("service_car_year_mileage"),
            car_mileage=None,
            work_wishes=data.get("service_work_wishes"),
        )
        logger.info(f"Лид записи на ТО сохранён: ID={lead_id}")
    except Exception as e:
        logger.error(f"Ошибка сохранения лида: {e}")

    await message.answer("Могу ли я ещё чем-нибудь Вам помочь?")
    await state.update_data(
        fio=fio,
        phone=phone,
        last_lead_id=lead_id,
        last_lead_group_id=group_id,
        last_need=ClientNeed.SERVICE.value,
        more_need_attempts=0,
        service_lead_sent=True,
    )
    await state.set_state(BotStates.WAITING_MORE_NEED)
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_NEW_USED)
async def handle_new_used_choice(message: Message, state: FSMContext):
    """
    Уточнение: новый автомобиль или с пробегом.
    Используется и при первом запросе, и при повторном («Могу ли ещё помочь?»).
    """
    cancel_generic_inactivity(message.from_user.id)
    text_raw = (message.text or "").strip()
    text = text_raw.lower()
    data = await state.get_data()
    brand = data.get("brand") or "chery_tenet"
    need_candidate_value = data.get("need_candidate") or ClientNeed.CHERY_TENET.value

    # Если клиент на этом шаге называет чужую марку (Форд, Киа и т.п.) —
    # считаем, что его нужно перевести в отдел автомобилей с пробегом.
    if any(b in text for b in FOREIGN_BRAND_KEYWORDS):
        final_need = ClientNeed.USED_CARS
        await message.answer(
            "Я могу перевести Вас на отдел автомобили с пробегом. "
            "Там Вам постараются помочь с Вашим запросом.",
            reply_markup=make_main_keyboard(brand),
        )
        await schedule_generic_inactivity(message, state)
    else:
        # Определяем финальную потребность: новый или с пробегом
        if "пробег" in text or "б/у" in text or "бу" in text or "бэу" in text:
            final_need = ClientNeed.USED_CARS
        elif "нов" in text:
            final_need = ClientNeed.CHERY_TENET
        else:
            # Непонятно -> оставляем кандидат (новый авто выбранной марки)
            final_need = ClientNeed(need_candidate_value)

    await state.update_data(need=final_need.value)

    # Если уже есть ФИО и телефон — это повторный запрос: создаём новый лид сразу
    if data.get("fio") and data.get("phone"):
        await process_phone(
            message,
            state,
            data.get("phone"),
            phone_alt=data.get("phone_alt"),
        )
        return

    # Иначе — это первый запрос: продолжаем обычный сценарий (спросить ФИО)
    await state.set_state(BotStates.WAITING_FIO)
    await message.answer(
        "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
        reply_markup=ReplyKeyboardRemove(),
    )
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_CAR_BRAND_MODEL)
async def handle_car_brand_model(message: Message, state: FSMContext):
    """Марка и модель автомобиля (одной строкой; в БД разделяем по первому пробелу)."""
    cancel_generic_inactivity(message.from_user.id)
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, укажите марку и модель автомобиля.")
        return
    parts = text.split(maxsplit=1)
    car_brand = parts[0] if parts else text
    car_model = parts[1] if len(parts) > 1 else ""
    await state.update_data(car_brand=car_brand, car_model=car_model)
    await state.set_state(BotStates.WAITING_CAR_YEAR)
    await message.answer("Год выпуска автомобиля?")
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_CAR_YEAR)
async def handle_car_year(message: Message, state: FSMContext):
    """Год выпуска."""
    cancel_generic_inactivity(message.from_user.id)
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, укажите год выпуска.")
        return
    await state.update_data(car_year=text)
    await state.set_state(BotStates.WAITING_CAR_MILEAGE)
    await message.answer("Приблизительный пробег на сегодня (км)?")
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_CAR_MILEAGE)
async def handle_car_mileage(message: Message, state: FSMContext):
    """Пробег."""
    cancel_generic_inactivity(message.from_user.id)
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, укажите приблизительный пробег (км).")
        return
    await state.update_data(car_mileage=text)
    await state.set_state(BotStates.WAITING_CAR_WISHES)
    await message.answer(
        "Опишите, пожалуйста, пожелания по работам "
        "(например: ТО, шиномонтаж, окраска бампера, запись на осмотр по направлению от страховой)."
    )
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_CAR_WISHES)
async def handle_car_wishes(message: Message, state: FSMContext):
    """Пожелания по работам. Если в сессии уже есть ФИО и телефон — не спрашиваем повторно."""
    cancel_generic_inactivity(message.from_user.id)
    text = (message.text or "").strip()
    await state.update_data(work_wishes=text or None)
    data = await state.get_data()
    if data.get("fio") and data.get("phone"):
        await process_phone(
            message, state,
            data.get("phone"),
            phone_alt=data.get("phone_alt"),
        )
        return
    await state.set_state(BotStates.WAITING_FIO)
    await message.answer("Сообщите мне ваши фамилию, имя и отчество, пожалуйста?")
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_FIO)
async def handle_fio(message: Message, state: FSMContext):
    """
    Обработка ФИО клиента.
    """
    cancel_generic_inactivity(message.from_user.id)
    data = await state.get_data()
    if data.get("fio") and data.get("phone"):
        await process_phone(message, state, data.get("phone"), phone_alt=data.get("phone_alt"))
        return
    text = (message.text or "").strip()
    brand = data.get("brand") or "chery_tenet"

    if not text:
        await message.answer("Пожалуйста, укажите Ваше ФИО.", reply_markup=make_main_keyboard(brand))
        return

    # Защита от ситуации, когда вместо ФИО клиент снова пишет «хочу купить автомобиль» и т.п.
    lower = text.lower()
    intent_words = ["купить", "авто", "автомоб", "машин", "запис", "ремонт", "пробег", "запчаст"]
    if any(w in lower for w in intent_words):
        await message.answer(
            "Похоже, Вы продолжаете описывать запрос. "
            "Пожалуйста, укажите Ваши фамилию, имя и отчество полностью.",
            reply_markup=make_main_keyboard(brand),
        )
        return

    fio = text
    await state.update_data(fio=fio)
    await state.set_state(BotStates.WAITING_PHONE)
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Передать номер телефона", request_contact=True)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    
    await message.answer(
        f"Я записал Ваши данные, Вы {fio}. Подтвердите номер телефона нажатием на кнопку \"Передать номер телефона\" или введите вручную.",
        reply_markup=keyboard,
    )
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_PHONE, F.contact)
async def handle_phone_contact(message: Message, state: FSMContext):
    """
    Обработка телефона через кнопку. Если клиент ранее ввёл другой номер вручную — передаём оба в лид.
    """
    cancel_generic_inactivity(message.from_user.id)
    contact = message.contact
    phone_contact = contact.phone_number
    if phone_contact and not phone_contact.startswith("+"):
        phone_contact = f"+{phone_contact}"
    data = await state.get_data()
    phone_typed = data.get("phone_typed")
    phone_alt = None
    if phone_typed:
        n1 = normalize_phone(phone_typed)
        n2 = normalize_phone(phone_contact)
        if n1 and n2 and n1 != n2:
            phone_alt = phone_typed  # основной — с кнопки, доп. — введённый
    await process_phone(message, state, phone_contact, phone_alt=phone_alt)


@router.message(BotStates.WAITING_PHONE)
async def handle_phone_text(message: Message, state: FSMContext):
    """
    Обработка телефона: ввод вручную или «Готово» для продолжения.
    Если клиент ввёл номер, предлагаем также нажать кнопку (другой номер) — тогда в лид пойдут оба.
    """
    cancel_generic_inactivity(message.from_user.id)
    raw_text = (message.text or "").strip()
    text = raw_text.lower()
    data = await state.get_data()
    phone_typed = data.get("phone_typed")
    brand = data.get("brand") or "chery_tenet"

    if text in ("готово", "да", "продолжить", "ок", "окей") and phone_typed:
        await process_phone(message, state, phone_typed, phone_alt=None)
        return

    normalized_phone = normalize_phone(raw_text)
    if normalized_phone:
        await state.update_data(phone_typed=normalized_phone)
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Передать номер телефона", request_contact=True)],
                [KeyboardButton(text="Готово")],
                [KeyboardButton(text="Старт")],
            ],
            resize_keyboard=True,
        )
        await message.answer(
            "Номер записан. Можете нажать «Передать номер телефона» для другого номера для связи "
            "или отправить «Готово» для продолжения.",
            reply_markup=keyboard,
        )
        return
    # Некорректный ввод: считаем попытки
    attempts = int(data.get("invalid_phone_attempts", 0)) + 1
    await state.update_data(invalid_phone_attempts=attempts)
    if attempts >= 3 and not phone_typed:
        # Три неудачные попытки, номера нет — мягко выходим из сессии
        await message.answer(
            "Без корректного номера телефона я не смогу передать Ваш запрос менеджеру.\n"
            "Если захотите начать диалог заново, нажмите «Старт».",
            reply_markup=make_main_keyboard(brand),
        )
        await state.clear()
        return

    # Остаёмся в WAITING_PHONE — не просим кнопку, если клиент вводит вручную
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Передать номер телефона", request_contact=True)],
            [KeyboardButton(text="Старт")],
        ],
        resize_keyboard=True,
    )
    intent_keywords = ["купить", "машин", "авто", "хочу", "запис", "ремонт", "сервис", "запчасти"]
    looks_like_intent = any(kw in text for kw in intent_keywords) and not any(c.isdigit() for c in raw_text)
    if looks_like_intent:
        msg = (
            "Сначала подтвердите номер телефона — он нужен для передачи заявки менеджеру. "
            "Введите номер в формате +7 XXX XXX XX XX или нажмите «Передать номер телефона»."
        )
    else:
        msg = (
            "Номер введён некорректно. Введите номер в формате +7 XXX XXX XX XX "
            "или нажмите «Передать номер телефона»."
        )
    await message.answer(msg, reply_markup=keyboard)
    await schedule_generic_inactivity(message, state)


@router.message(BotStates.WAITING_PHONE_BUTTON, F.contact)
async def handle_phone_button(message: Message, state: FSMContext):
    """
    Обработка телефона после запроса нажатия кнопки.
    """
    cancel_generic_inactivity(message.from_user.id)
    contact = message.contact
    phone = contact.phone_number
    if phone and not phone.startswith("+"):
        phone = f"+{phone}"
    await process_phone(message, state, phone)


@router.message(BotStates.WAITING_PHONE_BUTTON)
async def handle_phone_button_text(message: Message, state: FSMContext):
    """
    Поведение, если вместо передачи контакта пользователь продолжает писать текст.
    После нескольких неудачных попыток — вежливо выходим из сессии.
    """
    cancel_generic_inactivity(message.from_user.id)
    cancel_generic_inactivity(message.from_user.id)
    text_raw = (message.text or "").strip()
    text = text_raw.lower()
    data = await state.get_data()
    brand = data.get("brand") or "chery_tenet"
    phone_typed = data.get("phone_typed")

    # Разрешаем слово «Готово», если уже есть ранее введённый номер
    if text in ("готово", "да", "продолжить", "ок", "окей") and phone_typed:
        await process_phone(message, state, phone_typed, phone_alt=None)
        return

    attempts = int(data.get("invalid_phone_button_attempts", 0)) + 1
    await state.update_data(invalid_phone_button_attempts=attempts)

    if attempts >= 3 and not phone_typed:
        await message.answer(
            "Без корректного номера телефона я не смогу передать Ваш запрос менеджеру.\n"
            "Если захотите начать диалог заново, нажмите «Старт».",
            reply_markup=make_main_keyboard(brand),
        )
        await state.clear()
        return

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Передать номер телефона", request_contact=True)],
            [KeyboardButton(text="Старт")],
        ],
        resize_keyboard=True,
    )
    await message.answer(
        "Прошу Вас нажать кнопку согласия с передачей номера.",
        reply_markup=keyboard,
    )


@router.message(BotStates.WAITING_MORE_NEED)
async def handle_more_need(message: Message, state: FSMContext):
    """
    «Могу ли ещё помочь?» — отказ, новая потребность (та же группа → обновить лид; другая → новый лид),
    или не удалось определить за 2 попытки → лид в Прочие и прощание.
    """
    user_id = message.from_user.id
    cancel_generic_inactivity(user_id)
    text = (message.text or "").strip().lower()

    if any(kw in text for kw in MORE_HELP_NO_KEYWORDS):
        keyboard_start = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Старт")]],
            resize_keyboard=True,
            one_time_keyboard=False,
        )
        await message.answer(
            "Спасибо за обращение. До свидания. "
            "Если захотите начать диалог заново, нажмите кнопку «Старт» внизу экрана.",
            reply_markup=keyboard_start,
        )
        await state.clear()
        logger.info(f"Сессия закрыта: пользователь отказался от дополнительной помощи")
        return

    # Вопрос о часах работы
    if any(kw in text for kw in WORKING_HOURS_KEYWORDS):
        base = date_type.today()
        if "завтра" in text:
            d = base + timedelta(days=1)
        elif "послезавтра" in text or "после завтра" in text:
            d = base + timedelta(days=2)
        elif "сегодня" in text:
            d = base
        else:
            parsed = parse_date((message.text or "").strip(), base)
            d = parsed if parsed else base
        msg = get_working_hours_message(d)
        data = await state.get_data()
        await message.answer(msg, reply_markup=make_main_keyboard(data.get("brand") or "chery_tenet"))
        await message.answer("Могу ли я ещё чем-нибудь Вам помочь?")
        await schedule_generic_inactivity(message, state)
        return

    # «ОП Tenet & Chery» или устаревшая клавиатура «ОП Jetour» — единый ОП новых авто
    text_lower_check = text.strip().lower()
    if text_lower_check in ("оп tenet & chery", "оп jetour"):
        await state.update_data(
            brand="chery_tenet", need=ClientNeed.CHERY_TENET.value, need_text=message.text
        )
        data = await state.get_data()
        if data.get("fio") and data.get("phone"):
            await process_phone(
                message, state, data.get("phone"), phone_alt=data.get("phone_alt")
            )
        else:
            await state.set_state(BotStates.WAITING_FIO)
            await message.answer(
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                reply_markup=ReplyKeyboardRemove(),
            )
        return

    data = await state.get_data()
    last_need = data.get("last_need")
    last_lead_id = data.get("last_lead_id")
    last_lead_group_id = data.get("last_lead_group_id")
    fio = data.get("fio")
    phone = data.get("phone")  # не храним phone в state после process_phone — нужно сохранять
    # Сохраняем phone в state в process_phone для цикла «ещё помочь»
    attempts = data.get("more_need_attempts", 0) + 1
    await state.update_data(more_need_attempts=attempts)

    need = need_detector.detect(message.text)
    brand = data.get("brand") or "chery_tenet"

    # Покупка авто НЕ наших марок при повторном запросе → отдел автомобилей с пробегом
    if need == ClientNeed.CHERY_TENET:
        has_foreign_brand = any(b in text for b in FOREIGN_BRAND_KEYWORDS)
        if has_foreign_brand:
            need = ClientNeed.USED_CARS
            await message.answer(
                "Я могу перевести Вас на отдел автомобили с пробегом. "
                "Там Вам постараются помочь с Вашим запросом.",
                reply_markup=make_main_keyboard(brand),
            )

    if need:
        await state.update_data(more_need_attempts=0)
        if need.value == last_need and last_lead_id and last_lead_group_id:
            # Не добавляем повторную фразу о покупке — она не несёт новой информации
            purchase_needs = (ClientNeed.CHERY_TENET, ClientNeed.USED_CARS)
            need_text_lower = (data.get("need_text") or "").lower()
            msg_lower = text.strip().lower()
            is_repeated_purchase = (
                need in purchase_needs
                and any(kw in msg_lower for kw in ["купить", "машин", "авто", "хочу"])
                and any(kw in need_text_lower for kw in ["купить", "машин", "авто", "хочу"])
            )
            if not is_repeated_purchase:
                TelegramLeadsDB.update_lead_append(lead_id=last_lead_id, work_wishes_append=message.text)
                update_msg = group_router.format_lead_update_message(
                    lead_id=last_lead_id,
                    fio=fio,
                    phone=phone or "",
                    additional_wishes=message.text,
                    timestamp=format_datetime(datetime.now()),
                )
                try:
                    await message.bot.send_message(chat_id=last_lead_group_id, text=update_msg)
                    logger.info(f"Уточнение по лиду {last_lead_id} отправлено в группу {last_lead_group_id}")
                except Exception as e:
                    logger.error(f"Ошибка отправки уточнения в группу: {e}")
            else:
                dept_name = group_router.get_department_name(need)
                await message.answer(
                    f"Ваш запрос на покупку автомобиля уже передан в {dept_name}. "
                    "С Вами свяжутся. Могу ли помочь чем-то ещё?",
                    reply_markup=make_main_keyboard(brand),
                )
                await schedule_generic_inactivity(message, state)
                return
            await message.answer("Могу ли я ещё чем-нибудь Вам помочь?")
            await schedule_generic_inactivity(message, state)
            return

        await state.update_data(need=need.value, need_text=message.text)

        # Покупка авто без уточнения «новый/с пробегом» → спрашиваем отдельно
        new_markers = ["новый", "новый автомобиль", "новая машина"]
        used_markers = need_detector.KEYWORDS.get(ClientNeed.USED_CARS, [])
        text_lower = (message.text or "").strip().lower()
        has_new = any(m in text_lower for m in new_markers)
        has_used = any(m in text_lower for m in used_markers)
        if need == ClientNeed.CHERY_TENET and not has_new and not has_used:
            await state.update_data(need_candidate=need.value)
            await state.set_state(BotStates.WAITING_NEW_USED)
            keyboard = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="Новый автомобиль")],
                    [KeyboardButton(text="Автомобиль с пробегом")],
                ],
                resize_keyboard=True,
                one_time_keyboard=True,
            )
            await message.answer(
                "Подскажите, пожалуйста, вы рассматриваете новый автомобиль или автомобиль с пробегом?",
                reply_markup=keyboard,
            )
            await schedule_generic_inactivity(message, state)
            return

        if need == ClientNeed.SERVICE:
            has_fio_phone = data.get("fio") and data.get("phone")
            if _is_service_book_button(text.strip()):
                if has_fio_phone:
                    await state.update_data(service_fio=data.get("fio"), service_phone=data.get("phone"))
                    await state.set_state(BotStates.WAITING_SERVICE_CAR)
                    await message.answer(
                        "Назовите марку и модель Вашего автомобиля.",
                        reply_markup=ReplyKeyboardRemove(),
                    )
                else:
                    await state.set_state(BotStates.WAITING_SERVICE_FIO)
                    await message.answer(
                        "Назовите ваши фамилию, имя и отчество.",
                        reply_markup=make_main_keyboard(brand),
                    )
                await schedule_generic_inactivity(message, state)
            else:
                await state.set_state(BotStates.WAITING_SERVICE_CHOICE)
                await message.answer(
                    "Я могу записать Вас на ТО либо передать Ваш контакт ассистенту Слесарного цеха.",
                    reply_markup=make_main_keyboard(brand),
                )
                await schedule_generic_inactivity(message, state)
            return
        if need == ClientNeed.BODY_REPAIR:
            await state.update_data(car_brand=None, car_model=None, car_year=None, car_mileage=None, work_wishes=None)
            await state.set_state(BotStates.WAITING_CAR_BRAND_MODEL)
            await message.answer(
                "Укажите, пожалуйста, марку и модель автомобиля (например: Toyota Camry).",
                reply_markup=make_main_keyboard(brand),
            )
            await schedule_generic_inactivity(message, state)
            return
        else:
            if data.get("fio") and data.get("phone"):
                await process_phone(
                    message, state,
                    data.get("phone"),
                    phone_alt=data.get("phone_alt"),
                )
                return
            await state.set_state(BotStates.WAITING_FIO)
            await message.answer(
                "Сообщите мне ваши фамилию, имя и отчество, пожалуйста?",
                reply_markup=make_main_keyboard(brand),
            )
            await schedule_generic_inactivity(message, state)
        return

    if attempts >= 2:
        keyboard_start = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Старт")]],
            resize_keyboard=True,
            one_time_keyboard=False,
        )
        # Не создаём лид без подтверждённого телефона
        if not data.get("phone"):
            await message.answer(
                "Для передачи Вашего запроса менеджеру нужен номер телефона. "
                "Если захотите оставить заявку, нажмите «Старт» и укажите контактные данные.",
                reply_markup=keyboard_start,
            )
            await state.clear()
            return
        department = group_router.get_department_name(ClientNeed.SECRETARY)
        group_id = group_router.get_group_id(ClientNeed.SECRETARY)
        is_working = is_working_hours(datetime.now())
        response_template = get_response_message_template(is_working, department)
        await message.answer(response_template)
        await message.answer(
            "Спасибо за обращение. До свидания. "
            "Если захотите начать диалог заново, нажмите кнопку «Старт» внизу экрана.",
            reply_markup=keyboard_start,
        )
        try:
            lead_msg = group_router.format_lead_message(
                fio=data.get("fio", ""),
                phone=data.get("phone", ""),
                need_text=message.text or "",
                need=ClientNeed.SECRETARY,
                timestamp=format_datetime(datetime.now()),
                has_photo=False,
                phone_alt=data.get("phone_alt"),
            )
            await message.bot.send_message(chat_id=group_id, text=lead_msg)
            lead_id = TelegramLeadsDB.save_lead(
                telegram_user_id=message.from_user.id,
                telegram_username=message.from_user.username,
                client_fio=data.get("fio", ""),
                client_phone=data.get("phone", ""),
                need_type=ClientNeed.SECRETARY.value,
                phone_alt=data.get("phone_alt"),
                need_text=message.text,
                department=department,
                group_id=group_id,
                working_hours=is_working,
                response_message=response_template,
            )
            logger.info(f"Лид в Прочие создан: ID={lead_id}")
        except Exception as e:
            logger.error(f"Ошибка создания лида в Прочие: {e}")
        await state.clear()
        return

    await message.answer(
        "Уточните, пожалуйста: Вас интересует новый автомобиль (Chery/Tenet), "
        "автомобиль с пробегом, слесарный или кузовной цех, запчасти или что-то другое (Прочие)?"
    )
    await schedule_generic_inactivity(message, state)


def cancel_generic_inactivity(user_id: int) -> None:
    """Отменить таймер неактивности для пользователя."""
    task = _generic_inactivity_tasks.pop(user_id, None)
    if task and not task.done():
        task.cancel()


async def _generic_inactivity_worker(
    sec: float, bot, chat_id: int, user_id: int, storage, key: tuple,
) -> None:
    """
    Единый таймер для любого этапа WAITING_*:
    - через 30 секунд молчания — контрольный вопрос;
    - ещё через 30 секунд молчания — прощание и закрытие сессии.
    """
    try:
        from aiogram.fsm.context import FSMContext

        while True:
            await asyncio.sleep(sec)
            ctx = FSMContext(storage=storage, key=key)
            state = await ctx.get_state()
            if not state or "WAITING_" not in (state or ""):
                _generic_inactivity_tasks.pop(user_id, None)
                return

            data = await ctx.get_data()
            phase = int(data.get("generic_inactivity_phase", 0))

            if phase == 0:
                await ctx.update_data(generic_inactivity_phase=1)
                await bot.send_message(
                    chat_id,
                    "Вы ещё на связи? Продолжим, пожалуйста.",
                )
            else:
                await ctx.clear()
                from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

                keyboard_start = ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="Старт")]],
                    resize_keyboard=True,
                    one_time_keyboard=True,
                )
                await bot.send_message(
                    chat_id,
                    "Спасибо за обращение. До свидания. "
                    "Если захотите начать диалог заново, нажмите кнопку «Старт» внизу экрана.",
                    reply_markup=keyboard_start,
                )
                _generic_inactivity_tasks.pop(user_id, None)
                logger.info(f"Сессия закрыта по таймауту неактивности: user_id={user_id}, state={state}")
                return
    except Exception as e:
        logger.error(f"Ошибка таймера неактивности: {e}")


async def schedule_generic_inactivity(message: Message, state: FSMContext) -> None:
    """
    Запланировать контрольный вопрос и закрытие сессии на любом этапе:
    через 30 сек — «Вы ещё на связи? Продолжим, пожалуйста.»; ещё через 30 сек — закрытие.
    """
    user_id = message.from_user.id
    cancel_generic_inactivity(user_id)
    await state.update_data(generic_inactivity_phase=0)
    bot, chat_id, storage, key = message.bot, message.chat.id, state.storage, state.key
    task = asyncio.create_task(
        _generic_inactivity_worker(INACTIVITY_TIMEOUT_SEC, bot, chat_id, user_id, storage, key),
    )
    _generic_inactivity_tasks[user_id] = task


async def process_phone(
    message: Message,
    state: FSMContext,
    phone: str,
    phone_alt: str | None = None,
    custom_response: str | None = None,
):
    """
    Обрабатывает телефон (и опционально второй номер), создаёт лид, отправляет «С Вами свяжутся…» и «Могу ли ещё помочь?».
    custom_response: при «другая марка» — «Я передал Ваши данные в Отдел продаж, Вам перезвонят в ближайшие 15 минут».
    Сессия не завершается — переходим в WAITING_MORE_NEED.
    """
    data = await state.get_data()
    fio = data.get("fio")
    brand = data.get("brand") or "chery_tenet"
    need_value = data.get("need")
    need_text = data.get("need_text", "")
    car_brand = data.get("car_brand")
    car_model = data.get("car_model")
    car_year = data.get("car_year")
    car_mileage = data.get("car_mileage")
    work_wishes = data.get("work_wishes")

    need = ClientNeed(need_value) if need_value else ClientNeed.SECRETARY
    department = group_router.get_department_name(need)
    current_time = datetime.now()
    is_working = is_working_hours(current_time)
    response_template = get_response_message_template(is_working, department)

    # custom_response может быть передан явно или сохранён в state (для «другая марка»)
    override = custom_response or data.get("custom_response")
    response_text = override if override else f"{fio}, {response_template}"
    await message.answer(
        response_text,
        reply_markup=make_main_keyboard(brand),
    )

    group_id = group_router.get_group_id(need)
    lead_message = group_router.format_lead_message(
        fio=fio,
        phone=phone,
        need_text=need_text,
        need=need,
        timestamp=format_datetime(current_time),
        has_photo=False,
        phone_alt=phone_alt,
        car_brand=car_brand,
        car_model=car_model,
        car_year=car_year,
        car_mileage=car_mileage,
        work_wishes=work_wishes,
    )

    try:
        await message.bot.send_message(chat_id=group_id, text=lead_message)
        logger.info(f"Заявка отправлена в группу {group_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки в группу: {e}")

    lead_id = None
    try:
        lead_id = TelegramLeadsDB.save_lead(
            telegram_user_id=message.from_user.id,
            telegram_username=message.from_user.username,
            client_fio=fio,
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
        )
        logger.info(f"Лид сохранен в БД: ID={lead_id}")
    except Exception as e:
        logger.error(f"Ошибка сохранения лида в БД: {e}")

    try:
        ics_created = await ics_service.create_notification(
            fio=fio,
            phone=phone,
            need=need.value,
            department=department,
            need_text=need_text,
            telegram_user_id=message.from_user.id,
            car_brand=car_brand,
            car_model=car_model,
            car_year=car_year,
            car_mileage=car_mileage,
            work_wishes=work_wishes,
        )
        client = await ics_service.find_client(fio, phone)
        ics_client_found = client is not None
        ics_client_id = client.get("id") if client else None
        if lead_id:
            TelegramLeadsDB.update_ics_status(
                lead_id=lead_id,
                ics_notification_created=ics_created,
                ics_client_found=ics_client_found,
                ics_client_id=ics_client_id,
            )
        if client:
            await ics_service.save_telegram_user_id(client.get("id"), message.from_user.id)
    except Exception as e:
        logger.error(f"Ошибка работы с 1С: {e}")

    await message.answer("Могу ли я ещё чем-нибудь Вам помочь?")
    await state.update_data(
        phone=phone,
        phone_alt=phone_alt,
        phone_typed=None,
        last_lead_id=lead_id,
        last_lead_group_id=group_id,
        last_need=need.value,
        more_need_attempts=0,
        custom_response=None,
    )
    await state.set_state(BotStates.WAITING_MORE_NEED)
    await schedule_generic_inactivity(message, state)
    logger.info(f"Заявка оформлена: ФИО={fio}, Потребность={need.value}, лид_id={lead_id}")
