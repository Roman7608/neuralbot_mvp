#!/bin/bash
# Прямое тестирование подключения

echo "Тестирование подключения к PostgreSQL"
echo "======================================"
echo ""

# Попробуем подключиться через sudo (без пароля)
echo "Попытка 1: Подключение через sudo (от postgres)"
sudo -u postgres psql -d vikingi_analytics -c "\du analytics_user"

echo ""
echo "Попытка 2: Подключение с запросом пароля"
echo "Введите пароль для analytics_user:"
psql -U analytics_user -d vikingi_analytics -c "SELECT current_user, current_database();"

echo ""
echo "Попытка 3: Проверка существования пользователя"
sudo -u postgres psql -c "SELECT usename, usesuper FROM pg_user WHERE usename = 'analytics_user';"
