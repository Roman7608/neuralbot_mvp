"""
Обработчик команд для работы с каналом (для администраторов).
"""

import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

from telegram_bot.services.channel_publisher import ChannelPublisher
from telegram_bot_config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

router = Router()

# Список ID администраторов (можно вынести в конфиг)
ADMIN_IDS = []  # Заполнить ID администраторов


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    return user_id in ADMIN_IDS


@router.message(Command("publish"))
async def cmd_publish(message: Message):
    """
    Команда для публикации новости в канал.
    Использование: /publish Текст новости
    """
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав для выполнения этой команды.")
        return
    
    text = message.text.replace("/publish", "").strip()
    if not text:
        await message.answer("Использование: /publish Текст новости")
        return
    
    publisher = ChannelPublisher()
    try:
        message_id = await publisher.publish_text(text)
        if message_id:
            await message.answer(f"✅ Новость опубликована в канал (ID: {message_id})")
        else:
            await message.answer("❌ Ошибка публикации новости")
    finally:
        await publisher.close()


@router.message(Command("publish_photo"))
async def cmd_publish_photo(message: Message):
    """
    Команда для публикации фото в канал.
    Использование: /publish_photo [подпись]
    Отправьте фото вместе с командой.
    """
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав для выполнения этой команды.")
        return
    
    if not message.photo:
        await message.answer("Отправьте фото вместе с командой /publish_photo")
        return
    
    caption = message.caption or ""
    from aiogram import Bot
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    publisher = ChannelPublisher()
    try:
        # Скачиваем фото
        photo = message.photo[-1]  # Берем фото наибольшего размера
        file_info = await bot.get_file(photo.file_id)
        file_path = f"temp_photo_{photo.file_id}.jpg"
        await bot.download_file(file_info.file_path, file_path)
        
        from pathlib import Path
        message_id = await publisher.publish_photo(Path(file_path), caption)
        
        # Удаляем временный файл
        Path(file_path).unlink(missing_ok=True)
        
        if message_id:
            await message.answer(f"✅ Фото опубликовано в канал (ID: {message_id})")
        else:
            await message.answer("❌ Ошибка публикации фото")
    finally:
        await publisher.close()
        await bot.session.close()
