"""
Сервис для публикации новостей в Telegram-канал/группу.
"""

import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

from aiogram import Bot
from aiogram.types import InputFile, FSInputFile

from telegram_bot_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TELEGRAM_CHANNEL_TYPE

logger = logging.getLogger(__name__)


class ChannelPublisher:
    """Публикует новости в Telegram-канал или группу."""
    
    def __init__(self):
        """Инициализация сервиса."""
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.channel_id = TELEGRAM_CHANNEL_ID
        self.channel_type = TELEGRAM_CHANNEL_TYPE
    
    async def publish_text(
        self,
        text: str,
        disable_notification: bool = False,
    ) -> Optional[int]:
        """
        Публикует текстовый пост в канал/группу.
        
        :param text: Текст поста
        :param disable_notification: Отключить уведомления для подписчиков
        :return: ID сообщения или None при ошибке
        """
        if not self.channel_id:
            logger.warning("TELEGRAM_CHANNEL_ID не настроен, публикация пропущена")
            return None
        
        try:
            message = await self.bot.send_message(
                chat_id=self.channel_id,
                text=text,
                disable_notification=disable_notification,
            )
            logger.info(f"Новость опубликована в канал: {message.message_id}")
            return message.message_id
        except Exception as e:
            logger.error(f"Ошибка публикации новости: {e}")
            return None
    
    async def publish_photo(
        self,
        photo_path: Path,
        caption: str = "",
        disable_notification: bool = False,
    ) -> Optional[int]:
        """
        Публикует пост с фото в канал/группу.
        
        :param photo_path: Путь к файлу фото
        :param caption: Подпись к фото
        :param disable_notification: Отключить уведомления
        :return: ID сообщения или None при ошибке
        """
        if not self.channel_id:
            logger.warning("TELEGRAM_CHANNEL_ID не настроен, публикация пропущена")
            return None
        
        if not photo_path.exists():
            logger.error(f"Файл фото не найден: {photo_path}")
            return None
        
        try:
            photo_file = FSInputFile(str(photo_path))
            message = await self.bot.send_photo(
                chat_id=self.channel_id,
                photo=photo_file,
                caption=caption,
                disable_notification=disable_notification,
            )
            logger.info(f"Фото опубликовано в канал: {message.message_id}")
            return message.message_id
        except Exception as e:
            logger.error(f"Ошибка публикации фото: {e}")
            return None
    
    async def publish_document(
        self,
        document_path: Path,
        caption: str = "",
        disable_notification: bool = False,
    ) -> Optional[int]:
        """
        Публикует пост с документом в канал/группу.
        
        :param document_path: Путь к файлу документа
        :param caption: Подпись к документу
        :param disable_notification: Отключить уведомления
        :return: ID сообщения или None при ошибке
        """
        if not self.channel_id:
            logger.warning("TELEGRAM_CHANNEL_ID не настроен, публикация пропущена")
            return None
        
        if not document_path.exists():
            logger.error(f"Файл документа не найден: {document_path}")
            return None
        
        try:
            doc_file = FSInputFile(str(document_path))
            message = await self.bot.send_document(
                chat_id=self.channel_id,
                document=doc_file,
                caption=caption,
                disable_notification=disable_notification,
            )
            logger.info(f"Документ опубликован в канал: {message.message_id}")
            return message.message_id
        except Exception as e:
            logger.error(f"Ошибка публикации документа: {e}")
            return None
    
    async def publish_news_with_buttons(
        self,
        text: str,
        buttons: list,
        disable_notification: bool = False,
    ) -> Optional[int]:
        """
        Публикует пост с кнопками в канал/группу.
        
        :param text: Текст поста
        :param buttons: Список кнопок [{"text": "Текст", "url": "https://..."}]
        :param disable_notification: Отключить уведомления
        :return: ID сообщения или None при ошибке
        """
        if not self.channel_id:
            logger.warning("TELEGRAM_CHANNEL_ID не настроен, публикация пропущена")
            return None
        
        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            keyboard = []
            for button in buttons:
                keyboard.append([
                    InlineKeyboardButton(
                        text=button["text"],
                        url=button.get("url", ""),
                        callback_data=button.get("callback_data", ""),
                    )
                ])
            
            reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
            
            message = await self.bot.send_message(
                chat_id=self.channel_id,
                text=text,
                reply_markup=reply_markup,
                disable_notification=disable_notification,
            )
            logger.info(f"Пост с кнопками опубликован в канал: {message.message_id}")
            return message.message_id
        except Exception as e:
            logger.error(f"Ошибка публикации поста с кнопками: {e}")
            return None
    
    async def edit_message(
        self,
        message_id: int,
        text: str,
    ) -> bool:
        """
        Редактирует сообщение в канале/группе.
        
        :param message_id: ID сообщения для редактирования
        :param text: Новый текст
        :return: True если успешно, False иначе
        """
        if not self.channel_id:
            return False
        
        try:
            await self.bot.edit_message_text(
                chat_id=self.channel_id,
                message_id=message_id,
                text=text,
            )
            logger.info(f"Сообщение {message_id} отредактировано")
            return True
        except Exception as e:
            logger.error(f"Ошибка редактирования сообщения: {e}")
            return False
    
    async def delete_message(
        self,
        message_id: int,
    ) -> bool:
        """
        Удаляет сообщение из канала/группы.
        
        :param message_id: ID сообщения для удаления
        :return: True если успешно, False иначе
        """
        if not self.channel_id:
            return False
        
        try:
            await self.bot.delete_message(
                chat_id=self.channel_id,
                message_id=message_id,
            )
            logger.info(f"Сообщение {message_id} удалено")
            return True
        except Exception as e:
            logger.error(f"Ошибка удаления сообщения: {e}")
            return False
    
    async def close(self):
        """Закрывает сессию бота."""
        await self.bot.session.close()
