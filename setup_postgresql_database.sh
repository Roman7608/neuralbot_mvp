#!/bin/bash
# Скрипт настройки базы данных PostgreSQL для проекта Vikingi

set -e

echo "=========================================="
echo "Настройка базы данных PostgreSQL"
echo "=========================================="

# Запрос паролей
echo ""
echo "Введите пароли для пользователей базы данных:"
echo ""
read -sp "Пароль для analytics_user: " ANALYTICS_PASSWORD
echo ""
read -sp "Пароль для ics_user: " ICS_PASSWORD
echo ""

# Проверка, что пароли не пустые
if [ -z "$ANALYTICS_PASSWORD" ] || [ -z "$ICS_PASSWORD" ]; then
    echo "Ошибка: пароли не могут быть пустыми!"
    exit 1
fi

echo ""
echo "Создание базы данных и пользователей..."

# Выполнение команд через stdin (избегаем проблем с правами доступа к файлам)
sudo -u postgres psql <<EOF
-- Создание базы данных
CREATE DATABASE vikingi_analytics;

-- Создание пользователей
CREATE USER analytics_user WITH PASSWORD '$ANALYTICS_PASSWORD';
CREATE USER ics_user WITH PASSWORD '$ICS_PASSWORD';

-- Выдача прав
GRANT ALL PRIVILEGES ON DATABASE vikingi_analytics TO analytics_user;
GRANT CONNECT ON DATABASE vikingi_analytics TO ics_user;
EOF

# Выдача дополнительных прав на схему
sudo -u postgres psql -d vikingi_analytics <<EOF
-- Выдача прав на схему public для analytics_user
GRANT ALL ON SCHEMA public TO analytics_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO analytics_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO analytics_user;

-- Выдача прав на чтение для ics_user (только SELECT)
GRANT USAGE ON SCHEMA public TO ics_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ics_user;
EOF

echo ""
echo "=========================================="
echo "Настройка завершена!"
echo "=========================================="
echo ""
echo "Параметры подключения:"
echo "  База данных: vikingi_analytics"
echo "  Пользователь (аналитика): analytics_user"
echo "  Пользователь (1С): ics_user"
echo ""
echo "Для подключения используйте:"
echo "  psql -U analytics_user -d vikingi_analytics"
echo "  или"
echo "  psql -U ics_user -d vikingi_analytics"
echo ""
echo "Для подключения с сервера 1С используйте:"
echo "  Host: IP_адрес_сервера_3"
echo "  Port: 5432"
echo "  Database: vikingi_analytics"
echo "  User: ics_user"
echo "  Password: [введенный пароль для ics_user]"
echo ""
