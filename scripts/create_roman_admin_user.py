#!/usr/bin/env python3
"""
Раньше: пользователь с ролью 7 (Roman) — только «Переводы голосового бота».

Сейчас роль 7 не поддерживается. Журнал переводов — у суперадмина (роль 8, admin1).
Создание пользователя: python -m admin_panel.create_admin_user LOGIN PASSWORD 6|8
См. docs/инструкции/00_ИНСТРУКЦИЯ_ADMIN1.md
"""
from __future__ import annotations

import sys


def main() -> None:
    print(
        "Скрипт устарел: роль 7 снята с поддержки.\n"
        "Используйте admin1 (роль 8) для переводов бота или создайте пользователя:\n"
        "  python -m admin_panel.create_admin_user LOGIN PASSWORD 6\n"
        "  python -m admin_panel.create_admin_user LOGIN PASSWORD 8",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
