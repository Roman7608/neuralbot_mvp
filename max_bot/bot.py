"""
Точка входа MAX-бота: long polling (разработка / до готовности HTTPS webhook).

Перевод на webhook: отдельный HTTP-сервер + POST /subscriptions в API MAX,
этот цикл polling не запускать (или MAX_BOT_TRANSPORT=webhook).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from licensing.checker import verify_license, start_periodic_check

from max_bot.api_client import MaxApiClient, MaxApiError
from max_bot.config import (
    MAX_BOT_TOKEN,
    MAX_POLL_LIMIT,
    MAX_POLL_TIMEOUT,
    max_service_booking_via_alfa_enabled,
)
from max_bot.dispatcher import handle_update
from telegram_bot.services.working_hours_schedule import dealer_local_now

_log_dir = Path(__file__).resolve().parent.parent / "logs"
_log_dir.mkdir(exist_ok=True)
_log_file = _log_dir / "max_bot.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger(__name__)

# Модуль max_bot пока не в списке license.key — проверяем только срок лицензии
verify_license(None)
start_periodic_check(None)

RESTART_DELAY_SEC = 5.0
RESTART_DELAY_MAX_SEC = 300.0
RESTART_BACKOFF_FACTOR = 1.5


async def run_polling_session() -> None:
    if not MAX_BOT_TOKEN:
        logger.error("MAX_BOT_TOKEN не задан в .env")
        sys.exit(1)

    logger.info(
        "MAX: запись на ТО через 1С Альфа — %s (MAX_ALFA_SERVICE_BOOKING_ENABLED=%r)",
        "включена" if max_service_booking_via_alfa_enabled() else "выключена",
        os.environ.get("MAX_ALFA_SERVICE_BOOKING_ENABLED", ""),
    )
    logger.info(
        "MAX: TZ=%r локальное время процесса=%s (для фраз «перезвоним»; задайте TZ в compose как на хосте)",
        os.environ.get("TZ", ""),
        dealer_local_now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    client = MaxApiClient()
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        me = await client.get_me(session)
        logger.info("MAX бот: /me OK: %s", me)

        marker: int | None = None
        while True:
            try:
                data = await client.get_updates(
                    session,
                    marker=marker,
                    limit=MAX_POLL_LIMIT,
                    timeout=MAX_POLL_TIMEOUT,
                )
            except MaxApiError as e:
                logger.exception("get_updates ошибка: %s", e)
                await asyncio.sleep(RESTART_DELAY_SEC)
                continue

            updates = data.get("updates") or []
            new_marker = data.get("marker")
            if new_marker is not None:
                try:
                    marker = int(new_marker)
                except (TypeError, ValueError):
                    pass

            for u in updates:
                if not isinstance(u, dict):
                    continue
                try:
                    await handle_update(client, session, u)
                except Exception:
                    logger.exception("Ошибка обработки update: %s", u)

            if not updates:
                # long poll уже ждал timeout на стороне API; можно без sleep
                await asyncio.sleep(0)


async def main() -> None:
    delay = RESTART_DELAY_SEC
    while True:
        try:
            await run_polling_session()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.exception("MAX polling упал, перезапуск через %.0f с: %s", delay, e)
            await asyncio.sleep(delay)
            delay = min(delay * RESTART_BACKOFF_FACTOR, RESTART_DELAY_MAX_SEC)


if __name__ == "__main__":
    asyncio.run(main())
