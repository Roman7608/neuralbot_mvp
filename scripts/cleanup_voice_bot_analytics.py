#!/usr/bin/env python3
"""
Удаление из БД сессий голосового бота старше N дней (по умолчанию 14) и файлов записей по путям из БД.

Рекомендуется cron, например ежедневно в 03:30:

  30 3 * * * cd /path/to/VikingiAll && python scripts/cleanup_voice_bot_analytics.py >> /var/log/voice_bot_cleanup.log 2>&1

Переменные окружения: как у приложения (POSTGRESQL_*), опционально VOICE_BOT_RETENTION_DAYS (по умолчанию 14).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.postgresql_manager import VoiceBotAnalyticsDB  # noqa: E402


def main() -> None:
    days = int(os.environ.get("VOICE_BOT_RETENTION_DAYS", "14"))
    n, paths = VoiceBotAnalyticsDB.delete_older_than_days(days)
    removed_files = 0
    for raw in paths:
        p = Path(raw)
        try:
            if p.is_file():
                p.unlink()
                removed_files += 1
            elif p.is_dir():
                import shutil

                shutil.rmtree(p, ignore_errors=True)
                removed_files += 1
        except OSError as e:
            print(f"Не удалось удалить {raw}: {e}", file=sys.stderr)
    print(f"Удалено сессий в БД: {n}, обработано путей (файл/каталог): {len(paths)}, удалено файлов/каталогов: {removed_files}")


if __name__ == "__main__":
    main()
