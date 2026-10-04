#!/usr/bin/env python3
"""
Настройка учётных записей админки. Создаёт пользователей или обновляет пароли.
Запускать из корня проекта: python scripts/setup_admin_users.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.postgresql_manager import AdminAuthDB, PostgreSQLManager

USERS = [
    ("admin", "TOP", 6),
    ("sdir", "SDir", 4),
    ("service", "Service", 5),
    ("sales", "sales11", 1),   # общий для всех менеджеров ОП
    ("sto", "STO2", 2),       # общий для всех ассистентов СТО
    ("front", "Front007", 3),
]

# Старые индивидуальные логины — деактивировать
OLD_LOGINS = [
    "evdokimov", "zakharov", "krasnoshchekova", "schegolev",
    "plaksina", "andreeva", "grinkina", "darya",
]

def user_exists(login: str) -> bool:
    try:
        with PostgreSQLManager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM admin_users WHERE login = %s AND is_active = TRUE", (login,))
                return cur.fetchone() is not None
    except Exception:
        return False

def main():
    print("=== Настройка учётных записей админки ===\n")
    for login in OLD_LOGINS:
        if user_exists(login):
            if AdminAuthDB.deactivate_user(login):
                print(f"  Деактивирован: {login}")
    print()
    for login, password, role_id in USERS:
        if user_exists(login):
            if AdminAuthDB.set_password(login, password):
                print(f"  Пароль обновлён: {login}")
            else:
                print(f"  Ошибка обновления: {login}")
        else:
            uid = AdminAuthDB.create_user(login, password, role_id)
            if uid:
                print(f"  Создан: {login} (роль {role_id})")
            else:
                print(f"  Ошибка создания: {login}")
    print("\n=== Готово ===")

if __name__ == "__main__":
    main()
