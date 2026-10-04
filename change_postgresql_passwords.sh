#!/bin/bash
# Скрипт изменения паролей пользователей PostgreSQL

set -e

echo "=========================================="
echo "Изменение паролей пользователей PostgreSQL"
echo "=========================================="

# Запрос паролей
echo ""
echo "Введите новые пароли для пользователей базы данных:"
echo ""
read -sp "Новый пароль для analytics_user: " ANALYTICS_PASSWORD
echo ""
read -sp "Повторите пароль для analytics_user: " ANALYTICS_PASSWORD_CONFIRM
echo ""

if [ "$ANALYTICS_PASSWORD" != "$ANALYTICS_PASSWORD_CONFIRM" ]; then
    echo "❌ Пароли для analytics_user не совпадают!"
    exit 1
fi

read -sp "Новый пароль для ics_user: " ICS_PASSWORD
echo ""
read -sp "Повторите пароль для ics_user: " ICS_PASSWORD_CONFIRM
echo ""

if [ "$ICS_PASSWORD" != "$ICS_PASSWORD_CONFIRM" ]; then
    echo "❌ Пароли для ics_user не совпадают!"
    exit 1
fi

# Проверка, что пароли не пустые
if [ -z "$ANALYTICS_PASSWORD" ] || [ -z "$ICS_PASSWORD" ]; then
    echo "❌ Ошибка: пароли не могут быть пустыми!"
    exit 1
fi

echo ""
echo "Изменение паролей..."

# Изменение пароля для analytics_user
sudo -u postgres psql <<EOF
ALTER USER analytics_user WITH PASSWORD '$ANALYTICS_PASSWORD';
EOF

if [ $? -eq 0 ]; then
    echo "✅ Пароль для analytics_user изменен"
else
    echo "❌ Ошибка при изменении пароля для analytics_user"
    exit 1
fi

# Изменение пароля для ics_user
sudo -u postgres psql <<EOF
ALTER USER ics_user WITH PASSWORD '$ICS_PASSWORD';
EOF

if [ $? -eq 0 ]; then
    echo "✅ Пароль для ics_user изменен"
else
    echo "❌ Ошибка при изменении пароля для ics_user"
    exit 1
fi

echo ""
echo "=========================================="
echo "Пароли успешно изменены!"
echo "=========================================="
echo ""
echo "⚠️ ВАЖНО: Обновите пароли в файле postgresql_config.py:"
echo ""
echo "POSTGRESQL_ANALYTICS_PASSWORD: str = \"ваш_новый_пароль_analytics\""
echo "POSTGRESQL_ICS_PASSWORD: str = \"ваш_новый_пароль_ics\""
echo ""
echo "Или используйте переменные окружения для безопасности."
echo ""
