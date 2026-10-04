"""
Основной файл Telegram-бота.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Добавляем корневую директорию в путь для импорта конфига
sys.path.insert(0, str(Path(__file__).parent.parent))

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from telegram_bot_config import TELEGRAM_BOT_TOKEN
from telegram_bot.handlers import start, lead_handler, channel_handler

# Логи в папке logs/ в корне проекта
_log_dir = Path(__file__).resolve().parent.parent / "logs"
_log_dir.mkdir(exist_ok=True)
_log_file = _log_dir / "telegram_bot.log"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger(__name__)

from licensing.checker import verify_license, start_periodic_check
verify_license("telegram_bot")
start_periodic_check("telegram_bot")

# Перезапуск при обрывах: пауза между попытками (сек), макс. пауза при backoff
RESTART_DELAY_SEC = 5
RESTART_DELAY_MAX_SEC = 300
RESTART_BACKOFF_FACTOR = 1.5


# Dispatcher создаём один раз — роутеры нельзя подключать повторно (aiogram 3)
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(start.router)
dp.include_router(lead_handler.router)
dp.include_router(channel_handler.router)


async def run_polling_once():
    """Один цикл: создание бота, polling, закрытие сессии. При ошибке исключение пробрасывается."""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        logger.info("Telegram-бот запущен")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


async def main():
    """Запуск бота с автоматическим перезапуском при сетевых/прочих ошибках."""
    delay = RESTART_DELAY_SEC
    while True:
        try:
            await run_polling_once()
            # Polling завершился без исключения (редко) — выходим
            logger.info("Polling завершён, выход")
            break
        except (KeyboardInterrupt, SystemExit):
            logger.info("Остановка по запросу")
            raise
        except Exception as e:
            logger.exception("Ошибка при работе бота, перезапуск через %.0f с: %s", delay, e)
            await asyncio.sleep(delay)
            delay = min(delay * RESTART_BACKOFF_FACTOR, RESTART_DELAY_MAX_SEC)


if __name__ == "__main__":
    asyncio.run(main())
