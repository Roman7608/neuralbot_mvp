#!/usr/bin/env python3
"""
Создать пользователя админки.
Использование: python -m admin_panel.create_admin_user LOGIN PASSWORD ROLE_ID
ROLE_ID: 1=Менеджер ОП, 2=Ассистент СТО, 3=Хостес, 4=Руководитель ОП, 5=Руководитель СТО,
         6=Полный доступ (директор), 8=Суперадмин (admin1). Значение 7 не задавать.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.postgresql_manager import AdminAuthDB

def main():
    if len(sys.argv) < 4:
        print("Использование: python -m admin_panel.create_admin_user LOGIN PASSWORD ROLE_ID")
        print("ROLE_ID: 1-8 (см. описание в скрипте)")
        sys.exit(1)
    login = sys.argv[1]
    password = sys.argv[2]
    role_id = int(sys.argv[3])
    if role_id < 1 or role_id > 8 or role_id == 7:
        print("ROLE_ID: 1–6 или 8 (роль 7 снята с поддержки)")
        sys.exit(1)
    uid = AdminAuthDB.create_user(login, password, role_id)
    if uid:
        print(f"Пользователь создан: id={uid}, login={login}, role_id={role_id}")
    else:
        print("Ошибка: пользователь не создан (возможно, логин занят)")
        sys.exit(1)

if __name__ == "__main__":
    main()
