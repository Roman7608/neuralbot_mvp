#!/usr/bin/env python3
"""
Изменить пароль пользователя админки.
Использование: python -m admin_panel.set_admin_password LOGIN NEW_PASSWORD
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.postgresql_manager import AdminAuthDB

def main():
    if len(sys.argv) < 3:
        print("Использование: python -m admin_panel.set_admin_password LOGIN NEW_PASSWORD")
        sys.exit(1)
    login = sys.argv[1]
    password = sys.argv[2]
    if AdminAuthDB.set_password(login, password):
        print(f"Пароль для {login} изменён.")
    else:
        print(f"Ошибка: пользователь '{login}' не найден или неактивен.")
        sys.exit(1)

if __name__ == "__main__":
    main()
