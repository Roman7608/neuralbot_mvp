"""
Дублирование лидов в MAX-чаты отделов (chat_id), по аналогии с Telegram-группами.
Задайте в .env необязательные переменные MAX_CHAT_* (числовой id чата, куда бот может писать).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

from telegram_bot.services.need_detector import ClientNeed

if TYPE_CHECKING:
    import aiohttp

    from max_bot.api_client import MaxApiClient

logger = logging.getLogger(__name__)

_NEED_TO_ENV = {
    ClientNeed.CHERY_TENET: "MAX_CHAT_CHERY_TENET",
    ClientNeed.USED_CARS: "MAX_CHAT_USED_CARS",
    ClientNeed.SERVICE: "MAX_CHAT_SERVICE",
    ClientNeed.BODY_REPAIR: "MAX_CHAT_BODY_REPAIR",
    ClientNeed.SPARES: "MAX_CHAT_SPARES",
    ClientNeed.SECRETARY: "MAX_CHAT_CONSULTANT",
}


def _parse_chat_id(env_name: str) -> Optional[int]:
    raw = (os.environ.get(env_name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("Некорректный %s=%r (ожидается целое)", env_name, raw)
        return None


def max_chat_id_for_need(need: ClientNeed) -> Optional[int]:
    env = _NEED_TO_ENV.get(need)
    if not env:
        return None
    return _parse_chat_id(env)


async def notify_max_department(
    client: "MaxApiClient",
    http: "aiohttp.ClientSession",
    need: ClientNeed,
    text: str,
) -> bool:
    """Отправить текст в MAX-чат отдела, если MAX_CHAT_* задан. Без вложений."""
    chat_id = max_chat_id_for_need(need)
    if chat_id is None:
        env = _NEED_TO_ENV.get(need)
        logger.warning(
            "Лид MAX не дублируется в мессенджер MAX: для need=%s не задан %s в окружении",
            need.value,
            env,
        )
        return True
    try:
        await client.send_message(
            http,
            chat_id=chat_id,
            user_id=None,
            text=text,
            attachments=None,
        )
        return True
    except Exception as e:
        logger.error("Не удалось отправить лид в MAX-чат %s: %s", chat_id, e)
        return False
