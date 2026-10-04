"""
Приоритет использования GPU: голосовой бот (STT) имеет наивысший приоритет.
Модуль аналитики звонков может использовать GPU только когда бот не использует его.

Файловый lock: голосовой бот создаёт/обновляет lock-файл на время работы STT,
аналитика проверяет наличие файла перед запуском транскрибации.
"""

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Максимальный возраст lock-файла (сек): если бот упал и не удалил файл
LOCK_MAX_AGE_SEC = 300  # 5 минут

# Путь к lock-файлу: из env или по умолчанию /tmp
def _lock_path() -> Path:
    path = os.getenv("VIKINGI_GPU_LOCK_FILE", "")
    if path:
        return Path(path)
    return Path("/tmp") / "vikingi_gpu_voice_bot.lock"


def acquire_voice_bot_gpu() -> None:
    """Пометить, что голосовой бот сейчас использует GPU (вызывать перед STT)."""
    p = _lock_path()
    try:
        p.write_text(str(time.time()), encoding="utf-8")
    except OSError as e:
        logger.warning("Не удалось создать GPU lock %s: %s", p, e)


def release_voice_bot_gpu() -> None:
    """Снять пометку использования GPU (вызывать после завершения STT)."""
    p = _lock_path()
    try:
        if p.exists():
            p.unlink()
    except OSError as e:
        logger.warning("Не удалось удалить GPU lock %s: %s", p, e)


def is_gpu_held_by_voice_bot() -> bool:
    """
    Занят ли GPU голосовым ботом в данный момент.
    Устаревший lock (старше LOCK_MAX_AGE_SEC) считается сброшенным (бот мог упасть).
    """
    p = _lock_path()
    if not p.exists():
        return False
    try:
        content = p.read_text(encoding="utf-8").strip()
        ts = float(content)
    except (OSError, ValueError):
        return True  # Файл есть, но не прочитали — считаем занятым
    age = time.time() - ts
    if age > LOCK_MAX_AGE_SEC:
        try:
            p.unlink()
        except OSError:
            pass
        return False
    return True
