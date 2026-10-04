"""
Обработчик команды /start и начала диалога.
При старте сразу показываем общее меню с кнопкой «ОП Tenet & Chery» (единый отдел новых авто).
"""

import logging
from pathlib import Path

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext

from telegram_bot.states import BotStates
from telegram_bot.handlers.lead_handler import make_main_keyboard, schedule_generic_inactivity, cancel_generic_inactivity

logger = logging.getLogger(__name__)

router = Router()

# Путь к файлу Политики (корень проекта Vikingi: handlers -> telegram_bot -> Vikingi)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
POLICY_FILE = PROJECT_ROOT / "Analytic" / "Политика обработки персональных данных Викинги (18.11.22).docx"

GREETING = (
    "Здравствуйте! Я - бот автоцентра \"Викинги\" на Заставной. Чем могу Вам помочь? "
    "Продолжая общение в боте, вы подтверждаете согласие на обработку персональных данных "
    "в соответствии с Федеральным законом № 152-ФЗ и Политикой обработки персональных данных. "
    "При желании вы можете ознакомиться с Политикой, нажав кнопку ниже."
)

# Инлайн-кнопка под приветствием для ознакомления с Политикой
POLICY_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Ознакомиться с Политикой предприятия", callback_data="show_policy")],
])


def make_greeting_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для приветствия: кнопка «Ознакомиться с Политикой»."""
    return POLICY_KEYBOARD


@router.message(F.text.startswith("/start"))
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик /start. Сразу показываем приветствие и общее меню."""
    await state.clear()
    await state.set_state(BotStates.WAITING_NEED)
    await message.answer(GREETING, reply_markup=make_main_keyboard())
    await message.answer("Нажмите, чтобы ознакомиться с Политикой предприятия:", reply_markup=make_greeting_keyboard())
    await schedule_generic_inactivity(message, state)
    logger.info(f"Пользователь {message.from_user.id} начал диалог")


@router.message(F.text.in_(["старт", "Старт", "start", "Start"]))
async def cmd_start_button(message: Message, state: FSMContext):
    """Обработка нажатия кнопки «Старт» — как /start."""
    await state.clear()
    await state.set_state(BotStates.WAITING_NEED)
    await message.answer(GREETING, reply_markup=make_main_keyboard())
    await message.answer("Нажмите, чтобы ознакомиться с Политикой предприятия:", reply_markup=make_greeting_keyboard())
    await schedule_generic_inactivity(message, state)
    logger.info(f"Пользователь {message.from_user.id} начал диалог через кнопку Старт")


@router.callback_query(F.data == "show_policy")
async def send_policy_file(callback: CallbackQuery, state: FSMContext):
    """Отправка файла Политики по нажатию кнопки."""
    cancel_generic_inactivity(callback.from_user.id)
    await callback.answer()
    if not POLICY_FILE.exists():
        await callback.message.answer("Файл Политики временно недоступен. Попробуйте позже.")
        logger.warning(f"Файл Политики не найден: {POLICY_FILE}")
        return
    try:
        document = FSInputFile(POLICY_FILE, filename="Политика_обработки_ПД.docx")
        await callback.message.answer_document(document, caption="Политика в отношении обработки персональных данных")
        await schedule_generic_inactivity(callback.message, state)
        logger.info(f"Пользователь {callback.from_user.id} запросил Политику")
    except Exception as e:
        await callback.message.answer("Не удалось отправить файл. Попробуйте позже.")
        logger.exception(f"Ошибка отправки Политики: {e}")


